"""One render of the whole chain from settings, staged so a change re-runs only what it touches.

The studio and the final run share it, so the final is what the studio played.
"""

import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import astuple, dataclass, fields

import numpy as np
import numpy.typing as npt

from redline.core.loudness import integrated_lufs
from redline.engine.settings import SPECS, Settings
from redline.master.clip import MasterResult, clip_at, master_clip, saturate_low
from redline.vocal.chain import (
    ALL_LEVELS,
    ChainResult,
    Compressor,
    DirtLevel,
    Target,
    process_vocal,
)
from redline.vocal.fx import MAX_ECHOES, phrases, reverb, stutter, throws
from redline.vocal.timbre import (
    bitcrush,
    double,
    formant_shift,
    hard_tune,
    octave_layer,
    pitch_shift,
    voice_octaves,
)

Samples = npt.NDArray[np.floating]

CACHE_BYTES = 1_500_000_000
# the preview runs the chain on the loop plus this much on each side, so filters and
# envelopes have settled by the time the loop starts
PREVIEW_PAD_S = 3.0
MAX_PREVIEW_PAD_S = 10.0


def _stage_keys(stage: str) -> tuple[str, ...]:
    return tuple(f.name for f in fields(Settings) if SPECS[f.name].stage == stage)


CHAIN_KEYS = _stage_keys("chain")
VOICE_KEYS = _stage_keys("voice")
LAYER_KEYS = _stage_keys("layers")
FX_KEYS = _stage_keys("fx")
BEAT_KEYS = _stage_keys("beat")
MASTER_KEYS = _stage_keys("master")


class Cancelled(Exception):
    """A newer render was asked for before this one finished."""


@dataclass(frozen=True)
class Context:
    """What a render needs besides the stems and the settings, measured once per track."""

    rate: int
    target: Target
    upper: float
    tuning_cents: float
    master_lufs: float


@dataclass(frozen=True)
class Rendered:
    """The stems after their chains, the mix and the final."""

    vocal: Samples
    beat: Samples
    final: Samples
    chain: ChainResult
    master: MasterResult | None


def _key(settings: Settings, names: tuple[str, ...]) -> tuple:
    return tuple(getattr(settings, name) for name in names)


def master_target(settings: Settings, context: Context) -> float | None:
    """The loudness the master raises the mix to; None when the master is off."""
    if not settings.master_on:
        return None
    return context.master_lufs if settings.master_auto else settings.master_lufs


def dirt_level(settings: Settings) -> DirtLevel:
    """The chain's compression, drive and clip; named after a picked level when it is one."""
    if not settings.chain_on:
        return ALL_LEVELS["eq"]
    level = DirtLevel(
        "custom",
        Compressor(
            settings.comp_threshold_db,
            settings.comp_ratio,
            settings.comp_attack_ms,
            settings.comp_release_ms,
        ),
        settings.drive_db,
        settings.clip_below_peak_db if settings.clip_on else None,
    )
    for known in ALL_LEVELS.values():
        if astuple(known)[1:] == astuple(level)[1:]:
            return known
    return level


class Engine:
    """Renders one track's stems; stage results are kept up to a byte cap, oldest dropped."""

    def __init__(
        self, vocal: Samples, beat: Samples, context: Context, cache_bytes: int = CACHE_BYTES
    ) -> None:
        """Hold the track's stems and what was measured on it.

        Args:
            vocal: The vocal stem, whole.
            beat: The beat stem, whole.
            context: Rate, EQ target, tuning and loudness target of the track.
            cache_bytes: Most bytes of stage results to keep; 0 keeps only the last one.

        Raises:
            ValueError: A stem is not stereo float32 of shape (n, 2), or the two differ in
                length.
        """
        for name, stem in (("vocal", vocal), ("beat", beat)):
            if stem.ndim != 2 or stem.shape[1] != 2 or stem.dtype != np.float32:
                raise ValueError(
                    f"the {name} stem must be stereo float32 of shape (n, 2), "
                    f"not {stem.dtype} of shape {stem.shape}"
                )
        if len(vocal) != len(beat):
            raise ValueError(
                f"the vocal and the beat differ in length: {len(vocal)} and {len(beat)} samples"
            )
        self.vocal = vocal
        self.beat = beat
        self.context = context
        self.cache_bytes = cache_bytes
        self._cache: OrderedDict[tuple, tuple] = OrderedDict()
        self._lock = threading.Lock()
        # the last whole render's master and mix, kept as one pair: a preview reuses the
        # master's gain instead of searching, and reads the mix's loudness over its loop
        self.last: tuple[MasterResult | None, Samples] | None = None
        # set on a preview's piece: the whole render's master and how much louder the piece's
        # loop must read than the whole mix did there before the master's gain is reused
        self._fixed_master: MasterResult | None = None
        self._loop: tuple[int, int, float] | None = None

    @property
    def cached_bytes(self) -> int:
        """Bytes of audio the stage cache holds."""
        with self._lock:
            return sum(_nbytes(value) for value in self._cache.values())

    def _stage(self, key: tuple, compute: Callable[[], tuple]) -> tuple:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        value = compute()
        with self._lock:
            self._cache[key] = value
            total = sum(_nbytes(v) for v in self._cache.values())
            while total > self.cache_bytes and len(self._cache) > 1:
                _, dropped = self._cache.popitem(last=False)
                total -= _nbytes(dropped)
        return value

    def render(self, settings: Settings, cancelled: Callable[[], bool] = lambda: False) -> Rendered:
        """The whole chain; cancelled is asked between stages and stops the render."""
        ctx = self.context

        def check() -> None:
            if cancelled():
                raise Cancelled

        check()
        chain_key = ("chain", *_key(settings, CHAIN_KEYS))
        vocal, chain = self._stage(
            chain_key,
            lambda: process_vocal(
                self.vocal,
                ctx.rate,
                dirt_level(settings),
                ctx.target,
                ctx.upper,
                settings.eq_fit,
                settings.eq_offsets_db,
            ),
        )
        check()
        voice_key = (*chain_key, "voice", *_key(settings, VOICE_KEYS))
        (voiced,) = self._stage(voice_key, lambda: (self._voice(vocal, settings),))
        check()
        layer_key = (*voice_key, "layers", *_key(settings, LAYER_KEYS))
        (layered,) = self._stage(layer_key, lambda: (self._layers(voiced, settings),))
        check()
        fx_key = (*layer_key, "fx", *_key(settings, FX_KEYS))
        (layered,) = self._stage(fx_key, lambda: (self._fx(layered, settings),))
        check()
        beat_key = ("beat", *_key(settings, BEAT_KEYS))
        (beat,) = self._stage(beat_key, lambda: (self._beat(settings),))
        check()
        master_key = (*fx_key, *beat_key, "master", *_key(settings, MASTER_KEYS))
        mix = layered + beat
        final, master = self._stage(master_key, lambda: self._master(mix, settings))
        self.last = (master, mix)
        return Rendered(layered, beat, final, chain, master)

    def preview(self, settings: Settings, start: int, end: int) -> Samples:
        """The final of one loop, rendered on the loop and some padding: close, not exact."""
        # echoes and tails of phrases before the loop reach into it
        tails = [PREVIEW_PAD_S]
        if settings.throw_on:
            tails.append(MAX_ECHOES * settings.throw_ms / 1000)
        if settings.reverb_on:
            tails.append(settings.reverb_decay_s * 1.2)
        pad = int(min(max(tails), MAX_PREVIEW_PAD_S) * self.context.rate)
        low, high = max(0, start - pad), min(len(self.vocal), end + pad)
        piece = Engine(self.vocal[low:high], self.beat[low:high], self.context, 0)
        last = self.last
        if last is not None and last[0] is not None:
            piece._fixed_master = last[0]
            before = integrated_lufs(last[1][start:end], self.context.rate)
            piece._loop = (start - low, end - low, before)
        return piece.render(settings).final[start - low : end - low]

    def _voice(self, vocal: Samples, settings: Settings) -> Samples:
        out = vocal * np.float32(10 ** (settings.vocal_db / 20))
        rate = self.context.rate
        if settings.hard_tune > 0:
            out = hard_tune(out, rate, self.context.tuning_cents, settings.hard_tune / 100)
        if settings.formant != 1.0:
            out = formant_shift(out, rate, settings.formant)
        if settings.voice_octave:
            out = voice_octaves(out, rate, settings.voice_octave)
        if settings.nightcore:
            out = pitch_shift(out, rate, 12.0)
        return out

    def _layers(self, voiced: Samples, settings: Settings) -> Samples:
        out, rate = voiced, self.context.rate
        if settings.octave_down_on:
            out = octave_layer(out, rate, -1, settings.octave_down_db)
        if settings.octave_up_on:
            out = octave_layer(out, rate, 1, settings.octave_up_db)
        if settings.doubles_on:
            out = double(
                out,
                rate,
                settings.doubles_db,
                (settings.doubles_left_ms, settings.doubles_right_ms),
                settings.doubles_wander_ms,
            )
        if settings.bitcrush_on:
            # the crush works on the vocal's own scale, not on its level under the beat
            peak = float(np.abs(out).max())
            if peak > 0:
                crushed = bitcrush(out / peak, rate, settings.bitcrush_bits, settings.bitcrush_hold)
                out = crushed * np.float32(peak)
        return out

    def _fx(self, layered: Samples, settings: Settings) -> Samples:
        out, rate = layered, self.context.rate
        # read once: a stutter fills the pauses the throws would look for
        found = phrases(out, rate) if settings.stutter_on or settings.throw_on else None
        if settings.stutter_on:
            out = stutter(out, rate, settings.stutter_ms, settings.stutter_repeats, found)
        if settings.throw_on:
            out = throws(
                out,
                rate,
                settings.throw_db,
                settings.throw_ms,
                settings.throw_feedback,
                settings.throw_tone_hz,
                found,
            )
        if settings.reverb_on:
            out = reverb(
                out,
                rate,
                settings.reverb_mix_db,
                settings.reverb_decay_s,
                settings.reverb_predelay_ms,
                settings.reverb_tone_hz,
            )
        return out

    def _beat(self, settings: Settings) -> Samples:
        beat = self.beat
        if settings.beat_db:
            beat = beat * np.float32(10 ** (settings.beat_db / 20))
        if settings.bass_drive_db <= 0:
            # the crossover alone is an allpass, not the beat as it came
            return beat
        return saturate_low(beat, self.context.rate, settings.bass_freq_hz, settings.bass_drive_db)

    def _master(self, mix: Samples, settings: Settings) -> tuple[Samples, MasterResult | None]:
        target = master_target(settings, self.context)
        if target is None:
            return mix, None
        fixed = self._fixed_master
        if fixed is not None and self._loop is not None:
            # a loop reads louder or quieter than the whole track, so its own gain search
            # would miss; the whole track's gain is closer, moved by the change of target
            # and by how much the mix got louder at the loop since that render
            first, last, before = self._loop
            now = integrated_lufs(mix[first:last], self.context.rate)
            shift = 0.0 if not (math.isfinite(now) and math.isfinite(before)) else now - before
            gain = fixed.gain_db - fixed.trim_db + target - fixed.target_lufs - shift
            return clip_at(mix, gain, fixed.trim_db), fixed
        return master_clip(mix, self.context.rate, target)


def timbre_of(settings: Settings) -> str:
    """The voice and layer effects that are on, in order, joined by " + "."""
    parts = []
    if settings.hard_tune > 0:
        parts.append(f"hard tune {settings.hard_tune:g}%")
    if settings.formant != 1.0:
        parts.append(f"formant x{settings.formant:g}")
    if settings.voice_octave:
        parts.append("voice up" if settings.voice_octave > 0 else "voice down")
    if settings.nightcore:
        parts.append("nightcore")
    for on, name in (
        (settings.octave_down_on, "octave down"),
        (settings.octave_up_on, "octave up"),
        (settings.doubles_on, "doubles"),
        (settings.bitcrush_on, "bitcrush"),
        (settings.stutter_on, "stutter"),
        (settings.throw_on, "delay throws"),
        (settings.reverb_on, "reverb"),
    ):
        if on:
            parts.append(name)
    return " + ".join(parts)


def _nbytes(value: tuple) -> int:
    return sum(item.nbytes for item in value if isinstance(item, np.ndarray))
