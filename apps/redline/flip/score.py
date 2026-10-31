"""A beat score for our 808 and hats in play order, and its render to stems.

The caller lists the bars of the flip in play order; a bar knows the source bar it came from, the
808 notes that start in it and the hat preset it plays. The score lays those bars out in patterns
of 8 bars, then 4, 2 and 1 for the remainder, one section per chunk, and reuses a pattern whose
content repeats so a long arrangement stays under the format's 64 patterns.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np
import numpy.typing as npt
import redline_dsp

RATE = 48000
BLOCK = 8192
STEPS = 16
MAX_BARS = 512
PATTERN_BARS = (8, 4, 2, 1)
KEY = "C minor"
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

# a hit per step: x hit, a digit a roll of that many hits over the step
HATS = {
    "8ths": "x.x.x.x.x.x.x.x.",
    "16ths": "xxxxxxxxxxxxxxxx",
    "trip_roll": "xxxxxxxxxxxxxx33",
    "roll32": "xxxxxxxxxxxx2222",
}

# a drum row of a block: its sound in the score and the instrument that plays it
DRUM_SOUNDS = {
    "kick": ("drum_kick", "kick"),
    "snare": ("drum_snare", "snare"),
    "clap": ("drum_clap", "clap"),
    "hat": ("drum_hat", "hat_closed"),
    "hat_open": ("drum_hat_open", "hat_open"),
    "perc": ("drum_perc", "rim"),
}


@dataclass(frozen=True)
class FlipNote:
    """An 808 note that starts in a bar.

    Attributes:
        step: Start, 0..15 in the bar.
        len: Length in steps; it may run into the next bars.
        midi: MIDI note number.
        vel: Velocity 0..1.
    """

    step: int
    len: int
    midi: int
    vel: float = 1.0


@dataclass(frozen=True)
class PlayBar:
    """A bar of the flip in play order.

    Attributes:
        source: Index of the source bar it was taken from.
        notes: The 808 notes starting in it.
        hat: Name of a hat preset, or None for no hats.
        drums: Our drum rows in it: row name to 16 characters of "x" and ".".
        own: The notes are the block's own, so the last ones may ring on into the next bar.
    """

    source: int
    notes: tuple[FlipNote, ...] = ()
    hat: str | None = None
    drums: Mapping[str, str] = field(default_factory=dict)
    own: bool = False


@dataclass(frozen=True)
class Bass808Params:
    """The `bass808` params (the instrument's defaults)."""

    tune: float = 0.0
    decay: float = 1.5
    release: float = 0.05
    drive: float = 0.2
    glide_ms: float = 80.0
    click: float = 0.1
    tone: float = 2000.0


@dataclass(frozen=True)
class HatParams:
    """The `hat_closed` params (the instrument's defaults)."""

    decay: float = 0.05
    tone: float = 8000.0
    metal: float = 0.5


def note_name(midi: int) -> str:
    """The score's name of a MIDI note, C1 being 24."""
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def chunk_sizes(bars: int) -> list[int]:
    """Pattern lengths covering `bars`: as many 8s as fit, then 4, 2 and 1 for the rest."""
    sizes = []
    for size in PATTERN_BARS:
        sizes += [size] * (bars // size)
        bars %= size
    return sizes


def _check_drums(i: int, drums: Mapping[str, str]) -> None:
    for row, text in drums.items():
        if row not in DRUM_SOUNDS:
            raise ValueError(f"bars[{i}].drums: no row {row!r}; one of {', '.join(DRUM_SOUNDS)}")
        if len(text) != STEPS or set(text) - {"x", "."}:
            raise ValueError(f"bars[{i}].drums.{row}: {STEPS} characters of x and . expected")


def _check_bars(bars: Sequence[PlayBar]) -> None:
    if not 1 <= len(bars) <= MAX_BARS:
        raise ValueError(f"{len(bars)} bars in play order, a flip takes 1 to {MAX_BARS}")
    for i, bar in enumerate(bars):
        if bar.hat is not None and bar.hat not in HATS:
            raise ValueError(f"bars[{i}].hat: no preset {bar.hat!r}; one of {', '.join(HATS)}")
        _check_drums(i, bar.drums)
        for j, n in enumerate(bar.notes):
            path = f"bars[{i}].notes[{j}]"
            if not 0 <= n.step < STEPS:
                raise ValueError(f"{path}.step: {n.step}, a step is 0 to {STEPS - 1}")
            if n.len < 1:
                raise ValueError(f"{path}.len: {n.len}, at least 1")
            if not 0 <= n.midi <= 127:
                raise ValueError(f"{path}.midi: {n.midi}, MIDI is 0 to 127")
            if not 0.0 <= n.vel <= 1.0:
                raise ValueError(f"{path}.vel: {n.vel}, 0 to 1")


def _bass_notes(bars: Sequence[PlayBar], sizes: list[int], compromises: list[str]) -> list[dict]:
    """The 808 notes at play-order steps with their ends cut where the score cannot hold them."""
    run_end = [0] * len(bars)
    for i in range(len(bars) - 1, -1, -1):
        joined = i + 1 < len(bars) and (bars[i].own or bars[i + 1].source == bars[i].source + 1)
        run_end[i] = run_end[i + 1] if joined else i
    notes = []
    for i, bar in enumerate(bars):
        for n in bar.notes:
            start = i * STEPS + n.step
            notes.append((start, start + n.len, (run_end[i] + 1) * STEPS, i, n))
    notes.sort(key=lambda t: t[0])
    edges = np.cumsum(sizes) * STEPS
    out = []
    for k, (start, end, joined_end, i, n) in enumerate(notes):
        if end > joined_end:
            compromises.append(f"bar {i}: a note at step {n.step} cut where the source bars part")
            end = joined_end
        chunk_end = int(edges[np.searchsorted(edges, start, side="right")])
        if end > chunk_end:
            glides = k + 1 < len(notes) and notes[k + 1][0] < end
            what = "glide into the next note" if glides else "note"
            compromises.append(f"bar {i}: a {what} at step {n.step} cut at the pattern end")
            end = chunk_end
        out.append(
            {
                "step": start,
                "len": end - start,
                "note": note_name(n.midi),
                "vel": n.vel,
                "passing": True,
            }
        )
    return out


def flip_score(
    bars: Sequence[PlayBar],
    bpm: float,
    swing: float = 50.0,
    bass: Bass808Params | None = None,
    hat: HatParams | None = None,
    compromises: list[str] | None = None,
) -> dict[str, Any]:
    """A valid beat score playing the flip's 808, hats and drum rows bar by bar in play order.

    Args:
        bars: The play order.
        bpm: Tempo, 40..220.
        swing: 50..75, the share of a pair of 16ths the first takes.
        bass: Params of the 808 (the instrument's defaults when None).
        hat: Params of the hats (the defaults when None).
        compromises: When given, a line is appended for each note the score had to shorten.

    Returns:
        The score, with sounds `bass` and `hat`, and `drum_<row>` for each drum row in use.

    Raises:
        ValueError: A bar or a setting the format refuses.
    """
    _check_bars(bars)
    found: list[str] = []
    sizes = chunk_sizes(len(bars))
    notes = _bass_notes(bars, sizes, found)
    if compromises is not None:
        compromises.extend(found)

    patterns: dict[str, dict[str, Any]] = {}
    keys: dict[str, str] = {}
    counts: dict[str, int] = {}

    def pattern(prefix: str, size: int, row: Any) -> str:
        key = json.dumps([prefix, size, row], sort_keys=True)
        if key not in keys:
            name = f"{prefix}_{counts.get(prefix, 0)}"
            counts[prefix] = counts.get(prefix, 0) + 1
            keys[key] = name
            patterns[name] = {"bars": size, "rows": {prefix: row}}
        return keys[key]

    sections = []
    used: set[str] = set()
    first = 0
    for k, size in enumerate(sizes):
        play = []
        lo, hi = first * STEPS, (first + size) * STEPS
        row = [dict(n, step=n["step"] - lo) for n in notes if lo <= n["step"] < hi]
        if row:
            play.append(pattern("bass", size, row))
        presets = [bars[b].hat for b in range(first, first + size)]
        if any(presets):
            text = "".join(HATS[p] if p else "." * STEPS for p in presets)
            play.append(pattern("hat", size, text))
        for row, (sound, _) in DRUM_SOUNDS.items():
            text = "".join(bars[b].drums.get(row, "." * STEPS) for b in range(first, first + size))
            if "x" in text:
                play.append(pattern(sound, size, text))
                used.add(row)
        sections.append({"name": f"s{k}", "bars": size, "play": play})
        first += size

    drum_sounds = {
        DRUM_SOUNDS[row][0]: {"instrument": DRUM_SOUNDS[row][1]}
        for row in DRUM_SOUNDS
        if row in used
    }
    score = {
        "version": 1,
        "bpm": bpm,
        "swing": swing,
        "seed": 0,
        "key": KEY,
        "sounds": {
            "bass": {"instrument": "bass808", "params": asdict(bass or Bass808Params())},
            "hat": {"instrument": "hat_closed", "params": asdict(hat or HatParams())},
            **drum_sounds,
        },
        "patterns": patterns,
        "sections": sections,
    }
    errors = redline_dsp.beat_check(json.dumps(score))
    if errors:
        raise ValueError("; ".join(errors))
    return score


def render_flip_parts(
    score: dict[str, Any], rate: int = RATE, block: int = BLOCK
) -> dict[str, npt.NDArray[np.float32]]:
    """Render a flip score to one stereo stem per sound.

    Every stem has exactly the play order's frames; what rings past the last bar is cut.

    Args:
        score: A score from `flip_score`.
        rate: Render rate.
        block: Frames a `process` call.

    Returns:
        Sound name to a float32 array of shape (frames, 2).
    """
    player = redline_dsp.BeatPlayer(json.dumps(score), float(rate))
    names = list(player.sounds)
    frames = player.length
    skip = player.stem_latency
    parts = []
    got = 0
    while got < frames + skip:
        _, stems = player.process(block)
        parts.append(stems)
        got += stems.shape[1]
    stems = np.concatenate(parts, axis=1)[:, skip : skip + frames]
    return {name: np.ascontiguousarray(stems[i]) for i, name in enumerate(names)}


def render_drum_rows(
    bars: Sequence[PlayBar], bpm: float, swing: float = 50.0
) -> dict[str, npt.NDArray[np.float32]]:
    """The drum rows in use, each rendered from a score of its own.

    The synth seeds a hit's noise from its place among all the hits of the score, so rows in one
    score would change each other's samples; apart, a row sounds the same whatever else is filled.

    Returns:
        Row name to a float32 array of shape (frames, 2), the play order's length.

    Raises:
        ValueError: A bar or a setting the format refuses.
    """
    out = {}
    for row, (sound, _) in DRUM_SOUNDS.items():
        if not any("x" in bar.drums.get(row, "") for bar in bars):
            continue
        alone = [
            PlayBar(bar.source, drums={k: v for k, v in bar.drums.items() if k == row})
            for bar in bars
        ]
        out[row] = render_flip_parts(flip_score(alone, bpm, swing))[sound]
    return out


# one bar of every row, a fixed groove the drum layer is levelled against
_REFERENCE = {
    "kick": "x.......x.x.....",
    "snare": "....x.......x...",
    "clap": "....x.......x...",
    "hat": "x.x.x.x.x.x.x.x.",
    "hat_open": "..x.......x.....",
    "perc": "......x.....x...",
}
REFERENCE_BPM = 120.0


@lru_cache(maxsize=1)
def reference_drums() -> dict[str, npt.NDArray[np.float32]]:
    """The drum rows' fixed groove rendered one stem per row, for levelling the drum layer."""
    return render_drum_rows([PlayBar(i, drums=_REFERENCE) for i in range(2)], REFERENCE_BPM)
