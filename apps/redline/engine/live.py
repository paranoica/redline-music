"""The engine as three streaming racks (vocal, beat, master) the studio page can run live.

What a stream cannot know (the loudness of the whole stem, the fitted EQ, the master's gain
toward its target) is measured once by the Python engine in `analysis`; `racks` turns the
settings and those numbers into rack JSON for `redline_dsp`. The Python engine stays the
reference: the racks come close to its render, not to the sample.

The page runs the beat rack, hands its output to the vocal rack as the side signal, sums the
two and runs the master rack on the sum. The racks have different latencies; a host lines
the vocal and the beat up before summing them (an offline render shifts each back by its own).
"""

import json
import math
from dataclasses import fields
from typing import Any

import numpy as np

from redline.core.dsp import apply_sos, block_levels_db, compress
from redline.core.loudness import integrated_lufs
from redline.engine.render import (
    CHAIN_KEYS,
    LAYER_KEYS,
    VOICE_KEYS,
    Engine,
    _key,
    dirt_level,
    master_target,
)
from redline.engine.settings import MAX_RACK_EFFECTS, Settings, from_dict, to_dict
from redline.master.clip import CEILING_DB
from redline.vocal.chain import (
    EQ_BANDS,
    EQ_Q,
    GAIN_LIMIT_DB,
    PEAK_PERCENTILE,
    PRE_GAIN_LUFS,
    _eq_sos,
    gate_level_db,
    process_vocal,
    render_dirt,
)
from redline.vocal.fx import (
    LOUD_PERCENTILE,
    MIN_GAP_S,
    THROW_TAIL_S,
    VOICED_BELOW_LOUD_DB,
    _frames_db,
)

# the ranges of the rack's gain effect; a gain past them is split over several
GAIN_LOW_DB, GAIN_HIGH_DB = -60.0, 24.0
MASTER_GAIN_LIMIT_DB = 30.0
THROW_THRESHOLD_RANGE_DB = (-60.0, -10.0)
# the ranges of chain_compressor's gate and makeup and chain_clip's peak
GATE_RANGE_DB = (-120.0, 0.0)
MAKEUP_RANGE_DB = (-24.0, 48.0)
PEAK_RANGE_DB = (-60.0, 12.0)
DOUBLER_MAX_DEPTH_MS = 5.0
# hard tune below 100 % glides to the note over up to this long instead of going part way
TUNE_MAX_GLIDE_MS = 200.0
TUNE_SCALE = "chromatic"
EQ_KINDS = {"low_shelf": "lowshelf", "peak": "peak", "high_shelf": "highshelf"}
SHELF_Q = 1 / math.sqrt(2)

_CHAIN = set(CHAIN_KEYS)
_COMPRESSOR = {"chain_on", "comp_threshold_db", "comp_ratio", "comp_attack_ms", "comp_release_ms"}
_DIRT = _COMPRESSOR | {"drive_db", "clip_on", "clip_below_peak_db"}
_BEFORE_FX = _CHAIN | set(VOICE_KEYS) | set(LAYER_KEYS)
_EVERYTHING = {f.name for f in fields(Settings)}

# Each number of the analysis and the settings that change it: after a change of any of
# them the number is stale until `analysis` runs again. The track's own numbers depend on
# none; the master's gain depends on every setting, since every one moves the mix's loudness.
STALE_AFTER: dict[str, frozenset[str]] = {
    "pre_gain_db": frozenset(),
    "gate_db": frozenset(),
    "tuning_cents": frozenset(),
    "makeup_db": frozenset(_COMPRESSOR),
    "peak_db": frozenset(_COMPRESSOR),
    "eq_fit_db": frozenset(_DIRT | {"eq_fit"}),
    "post_gain_db": frozenset(_CHAIN),
    "bitcrush_peak_db": frozenset(_BEFORE_FX - {"bitcrush_on", "bitcrush_bits", "bitcrush_hold"}),
    "throw_threshold_db": frozenset(_BEFORE_FX),
    "master_gain_db": frozenset(_EVERYTHING),
    "master_trim_db": frozenset(_EVERYTHING),
}

# Settings the racks leave out, and why. The render through the Python engine still has them.
NOT_EXPRESSED: dict[str, str] = {
    "stutter_on": (
        "the engine repeats the end of each phrase into the pause after it, which takes the "
        "phrases of the whole stem; the rack's stutter repeats on a trigger instead"
    ),
    "stutter_ms": "part of the phrase stutter, which the racks leave out",
    "stutter_repeats": "part of the phrase stutter, which the racks leave out",
    "doubles_right_ms": (
        "the rack's doubler delays its right copy 1.5 times the left one (27 ms against 18, "
        "the default); a right delay of its own is not a param of it"
    ),
}

# Settings the racks reach only through the master's gain, which `analysis` measures.
THROUGH_ANALYSIS: dict[str, str] = {
    "master_auto": "picks the master's target, and so its gain, measured offline",
    "master_lufs": "the master's target, and so its gain, measured offline",
}

# Settings the racks express with another algorithm than the engine's: close, not the same.
APPROXIMATED: dict[str, str] = {
    "hard_tune": (
        "signalsmith's pitch shift on a streaming detector, not Praat's resynthesis; under 100 % "
        "the correction glides to the note instead of going part of the way"
    ),
    "formant": "signalsmith's formant shift on both channels, not Praat's on the mid only",
    "voice_octave": "signalsmith's pitch shift with formants kept, not Praat's",
    "nightcore": "signalsmith's pitch shift, not librosa's phase vocoder",
    "octave_down_on": "signalsmith's octave, not librosa's, and its level not matched to the dry",
    "octave_up_on": "signalsmith's octave, not librosa's, and its level not matched to the dry",
    "doubles_wander_ms": f"the doubler wanders {DOUBLER_MAX_DEPTH_MS:g} ms at most",
    "bitcrush_on": "the peak the crush is scaled to is the whole stem's, measured offline",
    "throw_on": (
        "phrase ends found as they happen (5 ms level under a threshold measured offline), "
        "one-pole tone, no cap of eight echoes"
    ),
    "reverb_on": "Faust's zita room, not a convolution with seeded noise; the wet level differs",
    "bass_drive_db": "the sub's level is kept over the last seconds, not over the whole beat",
    "master_on": (
        "a causal band limit and a clip at the ceiling instead of the zero-phase one and the "
        "whole file's trim; its gain is the engine's for the settings analysed"
    ),
}


def _stages(engine: Engine, settings: Settings) -> tuple[np.ndarray, np.ndarray]:
    """The voiced vocal and the layered one, from the engine's cache when it has them."""
    ctx = engine.context
    chain_key = ("chain", *_key(settings, CHAIN_KEYS))
    chained, _ = engine._stage(
        chain_key,
        lambda: process_vocal(
            engine.vocal,
            ctx.rate,
            dirt_level(settings),
            ctx.target,
            ctx.upper,
            settings.eq_fit,
            settings.eq_offsets_db,
        ),
    )
    voice_key = (*chain_key, "voice", *_key(settings, VOICE_KEYS))
    (voiced,) = engine._stage(voice_key, lambda: (engine._voice(chained, settings),))
    layer_key = (*voice_key, "layers", *_key(settings, LAYER_KEYS))
    (layered,) = engine._stage(layer_key, lambda: (engine._layers(voiced, settings),))
    return voiced, layered


def _db(value: float) -> float | None:
    return 20 * math.log10(value) if value > 0 else None


def analysis(engine: Engine, settings: Settings | None = None) -> dict[str, Any]:
    """The numbers the racks need and a stream cannot measure, for these settings.

    Runs the engine's render (from its cache when it has one) and parts of the chain again.
    `stale` says which of the numbers a later change of settings makes stale.

    Args:
        engine: The track's engine.
        settings: The settings to measure for; the defaults when left out.

    Returns:
        A JSON-ready mapping: the vocal's gain to the chain's level, the compressor's gate,
        its makeup and the clip's reference peak, the fitted EQ gains (before the manual
        offsets), the gain that gives the chain's output the stem's loudness back, the
        tuning, the peak the bitcrush scales to, the throws' threshold, the master's gain and
        trim (None with the master off), and the settings they were measured for.
    """
    settings = Settings() if settings is None else settings
    ctx = engine.context
    rate = ctx.rate
    rendered = engine.render(settings)
    level = dirt_level(settings)
    loudness = integrated_lufs(engine.vocal, rate)
    if not math.isfinite(loudness):
        raise ValueError("no vocal to analyse: the vocal stem is silent")
    pre = PRE_GAIN_LUFS - loudness
    staged = engine.vocal * np.float32(10 ** (pre / 20))
    gate = makeup = peak = None
    if level.compressor is not None:
        c = level.compressor
        gate = gate_level_db(staged, rate)
        squashed = compress(staged, rate, c.threshold_db, c.ratio, c.attack_ms, c.release_ms, gate)
        makeup = integrated_lufs(staged, rate) - integrated_lufs(squashed, rate)
        squashed = squashed * np.float32(10 ** (makeup / 20))
        peak = float(np.percentile(block_levels_db(squashed, rate), PEAK_PERCENTILE))
    offsets = np.asarray(settings.eq_offsets_db)
    if not settings.eq_fit:
        fit = np.zeros(len(EQ_BANDS))
    elif offsets.any():
        # the render's gains have the offsets in them, clipped; the fit alone is run again
        _, alone = process_vocal(engine.vocal, rate, level, ctx.target, ctx.upper, True, None)
        fit = np.asarray(alone.eq_gains_db)
    else:
        fit = np.asarray(rendered.chain.eq_gains_db)
    gains = _eq_gains(fit, settings)
    dirty, _ = render_dirt(staged, rate, level)
    equalised = apply_sos(dirty, _eq_sos(gains, rate)) if np.any(gains) else dirty
    post = loudness - integrated_lufs(equalised, rate)
    voiced, layered = _stages(engine, settings)
    crushed_from = (
        engine._layers(voiced, _without_bitcrush(settings)) if settings.bitcrush_on else layered
    )
    master = rendered.master
    return {
        "rate": rate,
        "pre_gain_db": pre,
        "gate_db": gate,
        "makeup_db": makeup,
        "peak_db": peak,
        "eq_fit_db": [float(g) for g in fit],
        "post_gain_db": post,
        "tuning_cents": ctx.tuning_cents,
        "bitcrush_peak_db": _db(float(np.abs(crushed_from).max())),
        "throw_threshold_db": _voiced_floor_db(layered, rate),
        "master_target_lufs": master_target(settings, ctx),
        "master_gain_db": None if master is None else master.gain_db - master.trim_db,
        "master_trim_db": None if master is None else master.trim_db,
        "settings": to_dict(settings),
    }


def _without_bitcrush(settings: Settings) -> Settings:
    data = to_dict(settings)
    data["bitcrush_on"] = False
    return from_dict(data)


def _voiced_floor_db(samples: np.ndarray, rate: int) -> float | None:
    """The level a 20 ms frame counts as voice above, as `redline.vocal.fx.phrases` reads it."""
    db = _frames_db(samples.mean(axis=1), rate)
    heard = db[db > -100]
    if heard.size == 0:
        return None
    return float(np.percentile(heard, LOUD_PERCENTILE)) - VOICED_BELOW_LOUD_DB


def stale(numbers: dict[str, Any], settings: Settings) -> list[str]:
    """The numbers of an analysis that no longer hold for these settings, in STALE_AFTER order.

    Args:
        numbers: What `analysis` returned, with the settings it measured for.
        settings: The settings now.
    """
    then = from_dict(numbers["settings"])
    changed = {
        f.name for f in fields(Settings) if getattr(then, f.name) != getattr(settings, f.name)
    }
    return [name for name, keys in STALE_AFTER.items() if keys & changed]


def _eq_gains(fit: np.ndarray, settings: Settings) -> np.ndarray:
    offsets = np.asarray(settings.eq_offsets_db, dtype=float)
    if not offsets.any():
        return np.asarray(fit, dtype=float)
    return np.clip(np.asarray(fit) + offsets, -2 * GAIN_LIMIT_DB, 2 * GAIN_LIMIT_DB)


def _need(numbers: dict[str, Any], name: str) -> float:
    value = numbers.get(name)
    if value is None:
        raise ValueError(f"the analysis has no {name} for these settings; analyse them again")
    return float(value)


def _within(value: float, bounds: tuple[float, float]) -> float:
    return min(bounds[1], max(bounds[0], value))


def _effect(name: str, on: bool = True, **params: float | str) -> dict[str, Any]:
    return {"effect": name, "on": on, "params": params}


def _gains(db: float) -> list[dict[str, Any]]:
    """Gain effects that add up to db; none for 0 dB."""
    out = []
    while db > GAIN_HIGH_DB or db < GAIN_LOW_DB:
        step = GAIN_HIGH_DB if db > 0 else GAIN_LOW_DB
        out.append(_effect("gain", db=step))
        db -= step
    if db:
        out.append(_effect("gain", db=round(db, 6)))
    return out


def _chain(settings: Settings, numbers: dict[str, Any]) -> list[dict[str, Any]]:
    out = _gains(_need(numbers, "pre_gain_db"))
    if settings.chain_on:
        out.append(
            _effect(
                "chain_compressor",
                threshold=settings.comp_threshold_db,
                ratio=settings.comp_ratio,
                attack=settings.comp_attack_ms,
                release=settings.comp_release_ms,
                gate=_within(_need(numbers, "gate_db"), GATE_RANGE_DB),
                makeup=_within(_need(numbers, "makeup_db"), MAKEUP_RANGE_DB),
            )
        )
        if settings.drive_db > 0:
            out.append(
                _effect(
                    "chain_clip",
                    drive=settings.drive_db,
                    clip="on" if settings.clip_on else "off",
                    below=settings.clip_below_peak_db,
                    peak=_within(_need(numbers, "peak_db"), PEAK_RANGE_DB),
                )
            )
    fit = np.zeros(len(EQ_BANDS)) if not settings.eq_fit else np.asarray(numbers["eq_fit_db"])
    for (kind, freq), gain in zip(EQ_BANDS, _eq_gains(fit, settings), strict=True):
        if gain:
            q = EQ_Q if kind == "peak" else SHELF_Q
            out.append(_effect("eq", kind=EQ_KINDS[kind], freq=freq, gain=float(gain), q=q))
    # the chain's loudness back and the vocal against the beat, both plain gains
    out.extend(_gains(_need(numbers, "post_gain_db") + settings.vocal_db))
    return out


def _voice(settings: Settings, numbers: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if settings.hard_tune > 0:
        strength = min(settings.hard_tune / 100, 1.0)
        out.append(
            _effect(
                "tune",
                key="C",
                scale=TUNE_SCALE,
                reference=min(100.0, max(-100.0, _need(numbers, "tuning_cents"))),
                speed=(1 - strength) * TUNE_MAX_GLIDE_MS,
                humanize=0.0,
                mix=1.0,
            )
        )
    if settings.formant != 1.0:
        out.append(_effect("formant", semitones=0.0, factor=settings.formant))
    if settings.voice_octave:
        out.append(_effect("pitch", semitones=12.0 * settings.voice_octave, preserve="on"))
    if settings.nightcore:
        out.append(_effect("pitch", semitones=12.0, preserve="off"))
    return out


def _layers(settings: Settings, numbers: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    # one octaver each, one after the other: the engine layers the octave up on the voice
    # that already has the octave down in it
    if settings.octave_down_on:
        out.append(_effect("octaver", down=10 ** (settings.octave_down_db / 20), up=0.0, dry=1.0))
    if settings.octave_up_on:
        out.append(_effect("octaver", down=0.0, up=10 ** (settings.octave_up_db / 20), dry=1.0))
    if settings.doubles_on:
        out.append(
            _effect(
                "doubler",
                level=settings.doubles_db,
                delay=settings.doubles_left_ms,
                depth=min(settings.doubles_wander_ms, DOUBLER_MAX_DEPTH_MS),
            )
        )
    if settings.bitcrush_on:
        peak = _need(numbers, "bitcrush_peak_db")
        out.extend(_gains(-peak))
        out.append(_effect("bitcrush", bits=settings.bitcrush_bits, hold=settings.bitcrush_hold))
        out.extend(_gains(peak))
    return out


def _fx(settings: Settings, numbers: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if settings.throw_on:
        low, high = THROW_THRESHOLD_RANGE_DB
        out.append(
            _effect(
                "throws",
                time=settings.throw_ms,
                feedback=settings.throw_feedback,
                tone=settings.throw_tone_hz,
                level=settings.throw_db,
                threshold=min(high, max(low, _need(numbers, "throw_threshold_db"))),
                gap=MIN_GAP_S * 1000,
                tail=THROW_TAIL_S * 1000,
            )
        )
    if settings.reverb_on:
        # the engine adds the wet on top of the dry; the room blends them, so it is set to
        # the same ratio and the sum raised back
        wet = 10 ** (settings.reverb_mix_db / 20)
        out.append(
            _effect(
                "room",
                mix=wet / (1 + wet),
                predelay=settings.reverb_predelay_ms,
                width=1.0,
                decay=settings.reverb_decay_s,
                damping=settings.reverb_tone_hz,
            )
        )
        out.extend(_gains(20 * math.log10(1 + wet)))
    return out


def _built_in(settings: Settings, numbers: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        *_chain(settings, numbers),
        *_voice(settings, numbers),
        *_layers(settings, numbers),
        *_fx(settings, numbers),
    ]


def racks(settings: Settings, numbers: dict[str, Any]) -> dict[str, str]:
    """The vocal, beat and master racks of these settings, as `redline_dsp` takes them.

    Stages that are off are left out, as the engine skips them. The vocal rack runs the
    chain, the voice, the layers, the fx and then the user's rack; its lanes come from
    `vocal_lanes`. The settings in NOT_EXPRESSED are left out.

    Args:
        settings: The settings to express.
        numbers: What `analysis` measured; when `stale` names a number, the racks use it
            stale.

    Raises:
        ValueError: The analysis lacks a number these settings need (a stage was off when
            it ran), or the vocal rack would hold more effects than a rack takes.
    """
    vocal = _built_in(settings, numbers)
    vocal += [{"effect": e.effect, "on": e.on, "params": dict(e.params)} for e in settings.rack]
    if len(vocal) > MAX_RACK_EFFECTS:
        raise ValueError(
            f"the vocal rack needs {len(vocal)} effects with the user's rack; a rack takes "
            f"{MAX_RACK_EFFECTS}"
        )
    beat = _gains(settings.beat_db)
    if settings.bass_drive_db > 0:
        beat.append(_effect("bass_drive", freq=settings.bass_freq_hz, drive=settings.bass_drive_db))
    master = []
    if settings.master_on:
        gain = _need(numbers, "master_gain_db")
        master.append(
            _effect(
                "master",
                gain=min(MASTER_GAIN_LIMIT_DB, max(-MASTER_GAIN_LIMIT_DB, gain)),
                trim=_need(numbers, "master_trim_db"),
                ceiling=CEILING_DB,
            )
        )
    return {
        "vocal": json.dumps({"effects": vocal}),
        "beat": json.dumps({"effects": beat}),
        "master": json.dumps({"effects": master}),
    }


def vocal_lanes(
    settings: Settings, numbers: dict[str, Any]
) -> list[tuple[int, str, list[tuple[float, float]]]]:
    """The user rack's automation lanes, at their slots in the vocal rack."""
    first = len(_built_in(settings, numbers))
    return [
        (first + slot, lane.param, [(float(t), float(v)) for t, v in lane.points])
        for slot, effect in enumerate(settings.rack)
        for lane in effect.automation
    ]
