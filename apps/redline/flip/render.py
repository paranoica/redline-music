"""Mixing a flip: bars cut and joined in play order, effects on bar ranges, stems summed."""

import itertools
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core import native
from redline.core.loudness import integrated_lufs
from redline.core.private import write_private
from redline.flip.model import BEATS_PER_BAR, Block, Effect, Flip

Samples = npt.NDArray[np.float32]

RATE = 48000
FADE_S = 0.010
RACK_BLOCK = 512
GRANULAR_HISTORY_S = 2.0
LICENCE_NOTE = (
    "Editing a beat does not change its licence: free type beats are often for non-profit use "
    "only; check the producer's terms before releasing."
)
STEM_NOTE = (
    "The stems were separated by a model and carry artefacts; hats that leak into the other "
    "stem stay in the mix."
)
RENDER_FILE = re.compile(r"flip-(\d+)\.(wav|report\.md)")

# the render plays as the beat while recording; it peaks here at most
CEILING_DB = -1.0
# a quiet flip is brought up toward the source at most this much
MAX_GAIN_DB = 24.0
MASTER_PASSES = 6
LEVEL_WINDOW_S = 0.05
PLAYING_DB = 20.0


def play_bar_starts(bar_starts_frames: Sequence[int], arrangement: Sequence[Block]) -> list[int]:
    """Frame of every play-order bar's start, and the end, from the source bar lengths."""
    count = len(bar_starts_frames) - 1
    for block in arrangement:
        if block.to_bar > count:
            raise ValueError(f"bars {block.from_bar}..{block.to_bar} are outside the {count} bars")
    lengths = [
        bar_starts_frames[bar + 1] - bar_starts_frames[bar]
        for block in arrangement
        for bar in range(block.from_bar, block.to_bar)
    ]
    return [0, *(int(v) for v in np.cumsum(lengths))]


def _fade_frames(rate: int) -> int:
    return max(2, round(FADE_S * rate))


def _slice(stem: Samples, start: int, end: int) -> Samples:
    """Frames [start, end) of the stem; outside it is silence."""
    out = np.zeros((end - start, stem.shape[1]), np.float32)
    lo, hi = max(start, 0), min(end, len(stem))
    if hi > lo:
        out[lo - start : hi - start] = stem[lo:hi]
    return out


def splice(
    stem: Samples,
    bar_starts_frames: Sequence[int],
    arrangement: Sequence[Block],
    rate: int,
) -> Samples:
    """The stem's source bars in play order, with equal-power crossfades at the joints.

    A joint where the next block continues the previous one's source bars is left alone, so
    tails ring across it. At any other joint the 10 ms crossfade is centred on the cut: the
    outgoing block runs on into its following source frames while the incoming one comes in
    from its preceding frames, so no sample changes place.

    Args:
        stem: the source stem, (frames, channels).
        bar_starts_frames: the start of every source bar and, last, the end of the final bar.
        arrangement: the blocks in play order, source bars [from_bar, to_bar).
        rate: the sample rate in Hz.

    Raises:
        ValueError: a block names a bar the source does not have.
    """
    bars = len(bar_starts_frames) - 1
    pieces: list[Samples] = []
    for block in arrangement:
        if block.to_bar > bars or block.from_bar < 0:
            raise ValueError(f"bars {block.from_bar}..{block.to_bar} are outside the {bars} bars")
        a, b = bar_starts_frames[block.from_bar], bar_starts_frames[block.to_bar]
        pieces.append(np.array(stem[a:b], dtype=np.float32))
    out = np.concatenate(pieces) if pieces else np.zeros((0, stem.shape[1]), np.float32)
    half = _fade_frames(rate) // 2
    ramp = np.linspace(0.0, np.pi / 2, 2 * half, dtype=np.float64)
    fade_in = np.sin(ramp).astype(np.float32)[:, None]
    fade_out = np.cos(ramp).astype(np.float32)[:, None]
    at = 0
    for prev, block in itertools.pairwise(arrangement):
        at += bar_starts_frames[prev.to_bar] - bar_starts_frames[prev.from_bar]
        if block.from_bar == prev.to_bar:
            continue
        end, begin = bar_starts_frames[prev.to_bar], bar_starts_frames[block.from_bar]
        mixed = _slice(stem, end - half, end + half) * fade_out
        mixed += _slice(stem, begin - half, begin + half) * fade_in
        lo, hi = at - half, min(at + half, len(out))
        out[lo:hi] = mixed[: hi - lo]
    return out


def _rack(effect: str, params: Mapping[str, float]) -> str:
    return json.dumps({"effects": [{"effect": effect, "on": True, "params": dict(params)}]})


def _reverse(segment: Samples, rate: int) -> Samples:
    out = np.array(segment[::-1], dtype=np.float32)
    n = min(_fade_frames(rate), len(out) // 2)
    if n:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
        out[:n] *= ramp
        out[-n:] *= ramp[::-1]
    return out


def _pre_roll(effect: Effect, rate: int) -> int:
    if effect.effect == "stutter":
        return math.ceil(effect.params.get("length", 125.0) * rate / 1000) + 8
    if effect.effect == "granular":
        return math.ceil(GRANULAR_HISTORY_S * rate)
    return 0


def _wet(out: Samples, effect: Effect, start: int, end: int, rate: int) -> Samples:
    rack = _rack(effect.effect, effect.params)
    aligned = native.rack_alignment(rack, rate)
    if aligned is None:
        raise RuntimeError("the native effects module is missing")
    grid = aligned[0]
    step = math.lcm(RACK_BLOCK, max(grid, 1))
    pre = math.ceil(_pre_roll(effect, rate) / step) * step if _pre_roll(effect, rate) else 0
    channels = out.shape[1]
    history = _slice(out, start - pre, start)
    x = np.concatenate([history, out[start:end]])
    if effect.effect == "stutter":
        t = pre / rate
        lane = [(0, "trigger", [(0.0, 0.0), (t - 1e-6, 0.0), (t, 1.0)])]
        y = native.rack_process_automated(x, rate, rack, lane)
    else:
        y = native.rack_process(x, rate, rack)
    if y is None:
        raise RuntimeError("the native effects module is missing")
    assert y.shape == x.shape and y.shape[1] == channels
    return np.asarray(y[pre:], dtype=np.float32)


def apply_effects(
    mix: Samples,
    play_bar_starts: Sequence[int],
    effects: Sequence[Effect],
    rate: int,
) -> Samples:
    """The mix with each effect laid on its play-order bar range, in order.

    Reverse is the range reversed in numpy with 10 ms fades. The rest run through the rack on
    the range, with stutter and granular given the audio before the range as their history;
    the last 10 ms of the range crossfade back to the unprocessed signal.

    Args:
        mix: the play-order mix, (frames, 2).
        play_bar_starts: the start of every play-order bar and, last, the end of the final one.
        effects: the effects, ranges in play-order bars.
        rate: the sample rate in Hz.

    Raises:
        ValueError: a range is outside the bars.
        RuntimeError: the native effects module is missing.
    """
    out = np.array(mix, dtype=np.float32)
    bars = len(play_bar_starts) - 1
    for effect in effects:
        if not 0 <= effect.from_bar < effect.to_bar <= bars:
            raise ValueError(f"effect bars {effect.from_bar}..{effect.to_bar} are outside {bars}")
        start, end = int(play_bar_starts[effect.from_bar]), int(play_bar_starts[effect.to_bar])
        dry = out[start:end].copy()
        wet = (
            _reverse(dry, rate)
            if effect.effect == "reverse"
            else _wet(out, effect, start, end, rate)
        )
        n = min(_fade_frames(rate), len(wet) // 2)
        if n and effect.effect != "reverse":
            ramp = np.linspace(0.0, np.pi / 2, n, dtype=np.float64)
            wet[-n:] = (
                wet[-n:] * np.cos(ramp).astype(np.float32)[:, None]
                + dry[-n:] * np.sin(ramp).astype(np.float32)[:, None]
            )
        out[start:end] = wet
    return out


def remove_hats(
    drums: Samples, drumsep_stems: Mapping[str, Samples], which: Sequence[str]
) -> Samples:
    """The drums minus the chosen DrumSep stems.

    Raises:
        ValueError: a stem is missing or is not the drums' shape.
    """
    out = np.array(drums, dtype=np.float32)
    for name in which:
        if name not in drumsep_stems:
            raise ValueError(f"no DrumSep stem {name!r}")
        if drumsep_stems[name].shape != drums.shape:
            raise ValueError(
                f"DrumSep stem {name!r} has shape {drumsep_stems[name].shape}, drums {drums.shape}"
            )
        out -= drumsep_stems[name]
    return out


def source_bar_starts(flip: Flip, frames: int, rate: int) -> list[int]:
    """Frame of every whole source bar's start from the downbeat, and the end of the last."""
    bar = BEATS_PER_BAR * 60.0 / flip.bpm * rate
    first = flip.downbeat_s * rate
    count = max(int((frames - first) // bar), 0)
    return [round(first + k * bar) for k in range(count + 1)]


def _fit(samples: Samples, frames: int) -> Samples:
    return _slice(samples, 0, frames)


def mix_flip(stems48: Mapping[str, Samples], ours48: Mapping[str, Samples], flip: Flip) -> Samples:
    """The flip's mix at 48 kHz: modes and gains applied, bars in play order, effects laid.

    Args:
        stems48: source stems, (frames, 2): drums, bass, other, optionally vocals (kept with
            other) and the DrumSep stems by name (hh, ride, crash) for removed hats.
        ours48: our renders in play order: `bass808` for the ours 808, `hats` for ours hats.
        flip: the flip.

    Raises:
        ValueError: a stem the modes need is missing, or the arrangement leaves the source.
    """
    for name in ("drums", "bass", "other"):
        if name not in stems48:
            raise ValueError(f"stems: {name!r} is missing")
    frames = len(stems48["drums"])
    source = source_bar_starts(flip, frames, RATE)
    starts = play_bar_starts(source, flip.arrangement)
    total = int(starts[-1])

    drums_src = stems48["drums"]
    if flip.hats_mode != "original":
        drums_src = remove_hats(drums_src, stems48, flip.hats_remove)
    drums = splice(drums_src, source, flip.arrangement, RATE)
    if flip.hats_mode == "ours":
        removed = splice(stems48["drums"] - drums_src, source, flip.arrangement, RATE)
        drums = drums + _at_level_of(_fit(_ours(ours48, "hats"), total), removed)

    other_src = stems48["other"]
    if "vocals" in stems48:
        other_src = other_src + stems48["vocals"]
    other = splice(other_src, source, flip.arrangement, RATE)

    if flip.bass_mode == "original":
        bass = splice(stems48["bass"], source, flip.arrangement, RATE)
    elif flip.bass_mode == "ours":
        original = splice(stems48["bass"], source, flip.arrangement, RATE)
        bass = _at_level_of(_fit(_ours(ours48, "bass808"), total), original)
    else:
        bass = np.zeros_like(drums)

    def gain(name: str) -> np.float32:
        return np.float32(10.0 ** (flip.gains[name] / 20.0))

    mixed = drums * gain("drums") + other * gain("other") + bass * gain("bass")
    return apply_effects(mixed.astype(np.float32), starts, flip.effects, RATE)


def _playing_rms(x: Samples) -> float:
    """RMS over the windows where the part plays (within 20 dB of its loudest window)."""
    if x.size == 0:
        return 0.0
    size = max(1, int(LEVEL_WINDOW_S * RATE))
    usable = len(x) // size * size
    if usable == 0:
        return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    power = np.square(x[:usable], dtype=np.float64).reshape(-1, size, x.shape[1]).mean(axis=(1, 2))
    if power.max() <= 0.0:
        return 0.0
    playing = power[power >= power.max() * 10.0 ** (-PLAYING_DB / 10.0)]
    return float(np.sqrt(playing.mean()))


def _at_level_of(ours: Samples, replaced: Samples) -> Samples:
    """Ours at the level of what it replaces while each plays, so a swap changes the sound only.

    Measured where each part sounds: hats that played in one section are not averaged with the
    bars they were silent in.
    """
    have, want = _playing_rms(ours), _playing_rms(replaced)
    if have <= 0.0 or want <= 0.0:
        return ours
    return (ours * np.float32(want / have)).astype(np.float32)


def fit_ceiling(mix: Samples, ceiling_db: float = CEILING_DB) -> tuple[Samples, float]:
    """The mix lowered to peak at the ceiling when it goes over, and the gain in dB (0 if not).

    Stems summed with new parts can exceed full scale; the recording engine plays the render
    as the beat, so it must not clip there.
    """
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    limit = 10.0 ** (ceiling_db / 20.0)
    if peak <= limit:
        return mix, 0.0
    return (mix * np.float32(limit / peak)).astype(np.float32), 20.0 * np.log10(limit / peak)


def master_flip(mix: Samples, rate: int, target_lufs: float) -> tuple[Samples, dict[str, float]]:
    """The mix at the source's loudness, its peaks held at the ceiling by the core's limiter.

    Returns:
        The mastered mix, and what was done: `gain_db` toward the target, `lowered_db` the
        last-resort scaling when the limiter's output still went over (0 when it did not), and
        `lufs` the loudness reached (a source mastered over full scale is not reached under the
        ceiling).
    """
    have = integrated_lufs(mix, rate)
    if not (math.isfinite(have) and math.isfinite(target_lufs)):
        held, lowered_db = fit_ceiling(mix)
        return held, {"gain_db": 0.0, "lowered_db": lowered_db, "lufs": integrated_lufs(held, rate)}
    gain_db = min(MAX_GAIN_DB, target_lufs - have)
    out = mix
    # the limiter takes back some loudness where it works, so the gain is corrected on its output
    for _ in range(MASTER_PASSES):
        out = _limited(mix, rate, gain_db)
        missing = target_lufs - integrated_lufs(out, rate)
        if abs(missing) < 0.3 or gain_db >= MAX_GAIN_DB:
            break
        gain_db = min(MAX_GAIN_DB, gain_db + missing)
    held, lowered_db = fit_ceiling(out)
    return held, {"gain_db": gain_db, "lowered_db": lowered_db, "lufs": integrated_lufs(held, rate)}


def _limited(mix: Samples, rate: int, gain_db: float) -> Samples:
    louder = (mix * np.float32(10.0 ** (gain_db / 20.0))).astype(np.float32)
    rack = json.dumps(
        {
            "effects": [
                {
                    "effect": "limiter",
                    "on": True,
                    "params": {"ceiling": CEILING_DB, "release": 50.0, "lookahead": 5.0},
                }
            ]
        }
    )
    limited = native.rack_process(louder, rate, rack)
    return louder if limited is None else limited.astype(np.float32)


def _ours(ours48: Mapping[str, Samples], name: str) -> Samples:
    if name not in ours48:
        raise ValueError(f"ours: {name!r} is missing")
    return ours48[name]


def _report(extra: str) -> str:
    parts = ["# Flip render", ""]
    if extra.strip():
        parts += [extra.strip(), ""]
    return "\n".join([*parts, STEM_NOTE, "", LICENCE_NOTE, ""])


def write_render(folder: Path, n: int, mix: Samples, rate: int, report: str) -> Path:
    """Write flip-<n>.wav (float, 48 kHz, stereo) and flip-<n>.report.md, both 0600.

    The wav is written under a temporary name and renamed, so a reader never sees half of it.

    Raises:
        ValueError: the mix is not stereo or the rate is not 48 kHz.
    """
    if rate != RATE or mix.ndim != 2 or mix.shape[1] != 2:
        raise ValueError(f"a flip render is stereo at {RATE} Hz, not {mix.shape} at {rate}")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = folder / f"flip-{n}.wav"
    tmp = folder / f".flip-{n}.wav.{os.getpid()}.tmp"
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            sf.write(out, np.asarray(mix, dtype=np.float32), rate, subtype="FLOAT", format="WAV")
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    write_private(folder / f"flip-{n}.report.md", _report(report))
    return target


def clean(folder: Path, keep: set[int]) -> list[int]:
    """Remove old renders and their reports except the newest and the kept ones; returns the n."""
    found: dict[int, list[Path]] = {}
    for path in folder.iterdir():
        if match := RENDER_FILE.fullmatch(path.name):
            found.setdefault(int(match.group(1)), []).append(path)
    if not found:
        return []
    spared = keep | {max(found)}
    gone = sorted(n for n in found if n not in spared)
    for n in gone:
        for path in found[n]:
            path.unlink(missing_ok=True)
    return gone
