"""Measure the takes recorded over one beat and rank them by how clean and tight they are.

The score is a sum of penalty points; fewer points rank higher, and ties go to the tighter
timing, then to the name. A take shorter than SHORTER_SHARE of the beat ranks below every
full-length one whatever its points. Each part has a tolerance below which it costs nothing:

- timing: 1 point per TIMING_STEP_MS that the take's median onset deviation from the median
  of all takes exceeds TIMING_TOLERANCE_MS. The reference is the other takes, not the beat
  grid, so a flow laid back the same way in every take costs nothing;
- clipping: CLIP_POINTS per run of CLIP_RUN or more samples at or above CLIP_LEVEL, at most
  CLIP_MAX_POINTS;
- noise: 1 point per FLOOR_STEP_DB that the floor in the gaps sits above FLOOR_TOLERANCE_DB
  under the voice;
- dropouts: DROPOUT_POINTS per dropout the recorder logged, at most DROPOUT_MAX_POINTS; a take
  whose dropouts are unknown (no sidecar, a recording that did not finish, or one during
  which the graph's quantum changed) gets the most;
- bleed: BLEED_POINTS per unit of take-to-beat correlation above BLEED_TOLERANCE;
- deep (with --deep only): DEEP_POINTS per unit of the own-vocal auto search's distance to the
  references.

Level steadiness and the offset from the beat grid are reported and never scored. The
points say cleaner and closer to the other takes, nothing about how a take sounds.
"""

import json
import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import librosa
import numpy as np
import numpy.typing as npt
from scipy.signal import correlate, resample_poly

from redline.core.audio import AudioError, Decoded, decode
from redline.core.dsp import LEVEL_FLOOR, block_levels_db
from redline.core.loudness import integrated_lufs, short_term_spread
from redline.measure.separate import Separator
from redline.takes.deep import deep_distance

log = logging.getLogger(__name__)

TAKE_NAME = re.compile(r"take-(\d+)\.wav", re.IGNORECASE)
RECORDER_RATE = 48000
# a take may run past the beat by the time it takes to press stop
LONGER_S = 2.0
# a take shorter than this share of the beat is measured over the overlap and reported as short
SHORTER_SHARE = 0.5
# a take whose peak stays under this is empty for our purposes (-40 dBFS)
SILENT_PEAK = 0.01
ONSET_RATE = 22050
ONSET_HOP = 128
# farther than this from a take's onset, another take's onset is a different syllable
MATCH_S = 0.1
GRID_DIVISIONS = 4
SNAP_S = 0.03
TIMING_TOLERANCE_MS = 10.0
TIMING_STEP_MS = 10.0
CLIP_LEVEL = 0.999
CLIP_RUN = 3
CLIP_POINTS = 2.0
CLIP_MAX_POINTS = 10.0
FLOOR_PERCENTILE = 10
LOUD_PERCENTILE = 95
FLOOR_TOLERANCE_DB = -50.0
FLOOR_STEP_DB = 5.0
DROPOUT_POINTS = 5.0
DROPOUT_MAX_POINTS = 20.0
BLEED_RATE = 8000
# the beat reaches the mic through the cups or the room a little after the singer's ears
BLEED_LAGS_S = (-0.005, 0.05)
BLEED_TOLERANCE = 0.05
BLEED_FLAG = 0.1
BLEED_POINTS = 20.0
DEEP_POINTS = 0.5
PARTS = ("timing", "clipping", "noise", "dropouts", "bleed", "deep")

Samples = npt.NDArray[np.floating]


class TakesError(Exception):
    """The takes folder cannot be ranked at all."""


@dataclass(frozen=True)
class Deep:
    """What the deep measurement needs besides the take and the beat."""

    refs: list[Path]
    work_dir: Path
    separator: Separator


@dataclass(frozen=True)
class Grid:
    """The beat's grid from librosa's beat tracker; information only."""

    bpm: float | None
    beats: tuple[float, ...]


@dataclass(frozen=True)
class TakeMetrics:
    """What was measured on one take; None where nothing could be measured."""

    seconds: float
    timing_ms: float | None
    late_ms: float | None
    matched_share: float | None
    grid_ms: float | None
    clip_share: float
    clip_runs: int
    floor_db: float | None
    floor_below_voice_db: float | None
    dropouts: int | None
    dropout_ms: float | None
    bleed: float
    lufs: float | None
    level_spread_lu: float | None
    deep_distance: float | None = None
    notes: tuple[str, ...] = ()
    short: bool = False
    quantum_changed: bool = False


@dataclass(frozen=True)
class RankedTake:
    """A take with its points; rank is None when there is nothing to rank against."""

    name: str
    path: Path
    rank: int | None
    points: dict[str, float]
    total: float
    metrics: TakeMetrics

    @property
    def headphones(self) -> bool:
        """The beat is loud enough in the mic to ask for headphones."""
        return self.metrics.bleed >= BLEED_FLAG


@dataclass(frozen=True)
class Skip:
    """A take left out: a stable code, an English reason and the numbers the report words."""

    name: str
    code: str
    reason: str
    values: dict[str, float | int] = field(default_factory=dict)


@dataclass(frozen=True)
class Ranking:
    """The takes in rank order, the ones left out with the reason, and the beat's grid."""

    beat: Path
    rate: int | None
    grid: Grid
    takes: list[RankedTake]
    skips: list[Skip] = field(default_factory=list)
    beat_seconds: float | None = None

    @property
    def skipped(self) -> list[tuple[str, str]]:
        """The left-out takes as (name, English reason)."""
        return [(s.name, s.reason) for s in self.skips]

    @property
    def ranked(self) -> bool:
        """More than one take was measured, so the order means something."""
        return len(self.takes) > 1


@dataclass
class _Take:
    path: Path
    decoded: Decoded
    onsets: npt.NDArray[np.float64]
    notes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def seconds(self) -> float:
        return len(self.decoded.samples) / self.decoded.rate


def find_takes(folder: Path) -> list[Path]:
    """The take-NN.wav files in a folder, by take number.

    Raises:
        TakesError: The folder is missing or holds no takes.
    """
    if not folder.is_dir():
        raise TakesError(f"no such folder: {folder}")
    found = [
        (int(match[1]), p.name, p)
        for p in folder.iterdir()
        if p.is_file() and (match := TAKE_NAME.fullmatch(p.name))
    ]
    if not found:
        raise TakesError(f"no take-NN.wav files in {folder}")
    return [p for _, _, p in sorted(found)]


def _resampled(mono: Samples, rate: int, target: int) -> npt.NDArray[np.float64]:
    mono = np.asarray(mono, dtype=np.float64)
    if rate == target:
        return mono
    divisor = math.gcd(rate, target)
    return np.asarray(resample_poly(mono, target // divisor, rate // divisor))


def _envelope(mono: Samples, rate: int) -> npt.NDArray[np.float64]:
    return librosa.onset.onset_strength(
        y=_resampled(mono, rate, ONSET_RATE), sr=ONSET_RATE, hop_length=ONSET_HOP
    )


def onset_times(mono: Samples, rate: int) -> npt.NDArray[np.float64]:
    """Onset times of a mono signal, in seconds."""
    if not np.any(mono):
        return np.zeros(0)
    frames = librosa.onset.onset_detect(
        onset_envelope=_envelope(mono, rate), sr=ONSET_RATE, hop_length=ONSET_HOP
    )
    return np.asarray(librosa.frames_to_time(frames, sr=ONSET_RATE, hop_length=ONSET_HOP))


def beat_grid(mono: Samples, rate: int) -> Grid:
    """The beat times and tempo by librosa's tracker, each beat moved onto the beat's onset.

    The tracker places beats several ms after the onsets the takes are read by; a beat with
    an onset within SNAP_S takes that onset's time instead.
    """
    if not np.any(mono):
        return Grid(None, ())
    envelope = _envelope(mono, rate)
    tempo, frames = librosa.beat.beat_track(
        onset_envelope=envelope, sr=ONSET_RATE, hop_length=ONSET_HOP
    )
    beats = librosa.frames_to_time(frames, sr=ONSET_RATE, hop_length=ONSET_HOP)
    onsets = librosa.frames_to_time(
        librosa.onset.onset_detect(onset_envelope=envelope, sr=ONSET_RATE, hop_length=ONSET_HOP),
        sr=ONSET_RATE,
        hop_length=ONSET_HOP,
    )
    snapped = []
    for b in beats:
        near = onsets[np.argmin(np.abs(onsets - b))] if onsets.size else b
        snapped.append(float(near if abs(near - b) <= SNAP_S else b))
    bpm = float(np.atleast_1d(tempo)[0])
    return Grid(bpm if bpm > 0 else None, tuple(snapped))


def grid_offset_ms(onsets: npt.NDArray[np.float64], grid: Grid) -> float | None:
    """Mean offset of the onsets from the nearest sixteenth of the grid, in ms; + is late.

    A circular mean over each beat's sixteenths, so onsets sitting just either side of a
    grid point average to it instead of to a point between two.
    """
    beats = np.asarray(grid.beats)
    if beats.size < 2:
        return None
    inside = onsets[(onsets >= beats[0]) & (onsets < beats[-1])]
    if inside.size == 0:
        return None
    index = np.searchsorted(beats, inside, side="right") - 1
    span = beats[index + 1] - beats[index]
    phase = 2 * np.pi * GRID_DIVISIONS * (inside - beats[index]) / span
    mean = math.atan2(float(np.sin(phase).mean()), float(np.cos(phase).mean()))
    return 1000 * mean / (2 * np.pi * GRID_DIVISIONS) * float(np.median(span))


def agreement(
    onsets: Sequence[npt.NDArray[np.float64]], seconds: Sequence[float]
) -> list[tuple[float | None, float | None, float | None]]:
    """How far each take's onsets sit from the median of all takes at the same syllable.

    For every onset of a take, the nearest onset of each other take within MATCH_S joins it;
    the onset's deviation is its distance from the median of those times, its own included.
    Onsets no other take has are left out.

    Returns:
        Per take: the median absolute deviation and the median signed one in ms (+ is late),
        and the share of its onsets another take matched; None with nothing matched.
    """
    found = []
    for i, own in enumerate(onsets):
        deviations = []
        for t in own:
            times = [t]
            for j, other in enumerate(onsets):
                if j == i or other.size == 0 or t > seconds[j]:
                    continue
                k = int(np.searchsorted(other, t))
                near = min(other[max(k - 1, 0) : k + 1], key=lambda u, t=t: abs(u - t))
                if abs(near - t) <= MATCH_S:
                    times.append(near)
            if len(times) > 1:
                deviations.append(t - float(np.median(times)))
        if not deviations:
            found.append((None, None, 0.0 if own.size else None))
            continue
        dev = np.asarray(deviations)
        found.append(
            (
                1000 * float(np.median(np.abs(dev))),
                1000 * float(np.median(dev)),
                len(deviations) / own.size,
            )
        )
    return found


def clipping(samples: Samples) -> tuple[float, int]:
    """Share of frames at or near full scale, and the runs of CLIP_RUN or more of them."""
    hot = np.abs(samples).max(axis=1) >= CLIP_LEVEL
    edges = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
    lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
    return float(hot.mean()), int((lengths >= CLIP_RUN).sum())


def noise_floor(samples: Samples, rate: int) -> tuple[float | None, float | None]:
    """The floor in the gaps in dBFS, and how far it sits under the voice, in dB.

    The gaps are the quietest FLOOR_PERCENTILE of 1 ms blocks and the voice is the
    LOUD_PERCENTILE, as the vocal report reads them; digital silence (the recorder's
    dropout fill) is left out.
    """
    levels = block_levels_db(samples, rate)
    levels = levels[levels > 20 * math.log10(LEVEL_FLOOR)]
    if levels.size == 0:
        return None, None
    floor = float(np.percentile(levels, FLOOR_PERCENTILE))
    return floor, floor - float(np.percentile(levels, LOUD_PERCENTILE))


def bleed(take: Samples, take_rate: int, beat: Samples, beat_rate: int) -> float:
    """Largest normalised correlation of the take with the beat over BLEED_LAGS_S.

    Its square is about the share of the take's energy that is the beat.
    """
    a = _resampled(take, take_rate, BLEED_RATE)
    b = _resampled(beat, beat_rate, BLEED_RATE)
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    norm = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    if norm == 0:
        return 0.0
    low, high = (round(lag * BLEED_RATE) for lag in BLEED_LAGS_S)
    full = correlate(a, b, mode="full", method="fft")
    window = full[max(n - 1 + low, 0) : n + high]
    return float(np.abs(window).max() / norm)


def read_dropouts(take: Path, rate: int) -> tuple[int | None, float | None, str | None]:
    """Count and length in ms of the dropouts in the take's sidecar.

    A sidecar without "complete" is from before the mark and reads as complete.

    Returns:
        The count, the total ms and None; or None, None and a note when the sidecar is
        missing, unreadable, or marked as a recording that did not finish.
    """
    sidecar = take.with_suffix(".json")
    if not sidecar.exists():
        return None, None, f"no {sidecar.name}; dropouts unknown"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if data.get("quantum_changed") is True:
            now = data.get("quantum_now")
            return None, None, f"the graph's quantum changed to {now} during it; dropouts unknown"
        if data.get("complete") is False:
            error = data.get("error") or "no reason logged"
            return None, None, f"the recording did not finish ({error}); dropouts unknown"
        spans = data["dropouts"]
        if not isinstance(spans, list):
            raise ValueError("dropouts is not a list")
        frames = 0
        for span in spans:
            if not (
                isinstance(span, list)
                and len(span) == 2
                and all(isinstance(f, int) and not isinstance(f, bool) for f in span)
                and 0 <= span[0] < span[1]
            ):
                raise ValueError(f"not a [start, end] frame range: {span!r}")
            frames += span[1] - span[0]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, None, f"{sidecar.name} unreadable ({error}); dropouts unknown"
    return len(spans), 1000 * frames / rate, None


def quantum_changed(take: Path) -> bool:
    """Whether the take's sidecar says the graph's quantum changed while it was recorded."""
    try:
        data = json.loads(take.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("quantum_changed") is True


def _rate_of(decoded: Sequence[Decoded]) -> int:
    """The rate most takes share; on a tie the recorder's, then the first take's."""
    counts = Counter(d.rate for d in decoded)
    # max keeps the first of equals, and a Counter keeps the order the rates came in
    return max(counts, key=lambda r: (counts[r], r == RECORDER_RATE))


def _kept(paths: Sequence[Path], beat_s: float) -> tuple[list[_Take], list[Skip]]:
    decoded, skipped = [], []
    for path in paths:
        try:
            decoded.append(decode(path))
        except AudioError as error:
            skipped.append(Skip(path.stem, "unreadable", str(error)))
    if not decoded:
        return [], skipped
    rate = _rate_of(decoded)
    kept = []
    for d in decoded:
        seconds = len(d.samples) / d.rate
        name = d.path.stem
        if d.rate != rate:
            reason = f"{d.rate} Hz, the other takes are at {rate} Hz"
            skipped.append(Skip(name, "other_rate", reason, {"rate": d.rate, "others": rate}))
        elif d.samples.size == 0 or float(np.abs(d.samples).max()) < SILENT_PEAK:
            skipped.append(Skip(name, "silent", "empty or almost silent", {"seconds": seconds}))
        elif seconds > beat_s + LONGER_S:
            values = {"seconds": seconds, "beat_seconds": beat_s}
            reason = f"{seconds:.1f} s against a beat of {beat_s:.1f} s"
            skipped.append(Skip(name, "too_long", reason, values))
        else:
            kept.append(_Take(d.path, d, onset_times(d.samples.mean(axis=1), d.rate)))
    return kept, skipped


def _measure(
    take: _Take,
    timing: tuple[float | None, float | None, float | None],
    grid: Grid,
    beat: Decoded,
    beat_seconds: float,
) -> TakeMetrics:
    samples, rate = take.decoded.samples, take.decoded.rate
    clip_share, clip_runs = clipping(samples)
    floor, below = noise_floor(samples, rate)
    dropouts, dropout_ms, note = read_dropouts(take.path, rate)
    if note:
        take.notes.append(note)
    lufs = integrated_lufs(samples, rate)
    return TakeMetrics(
        seconds=take.seconds,
        timing_ms=timing[0],
        late_ms=timing[1],
        matched_share=timing[2],
        grid_ms=grid_offset_ms(take.onsets, grid),
        clip_share=clip_share,
        clip_runs=clip_runs,
        floor_db=floor,
        floor_below_voice_db=below,
        dropouts=dropouts,
        dropout_ms=dropout_ms,
        bleed=bleed(samples.mean(axis=1), rate, beat.samples.mean(axis=1), beat.rate),
        lufs=lufs if math.isfinite(lufs) else None,
        level_spread_lu=short_term_spread(samples, rate),
        short=take.seconds < SHORTER_SHARE * beat_seconds,
        quantum_changed=quantum_changed(take.path),
    )


def points(metrics: TakeMetrics) -> dict[str, float]:
    """The penalty points of each part of the score; see the module docstring."""
    timing = metrics.timing_ms
    below = metrics.floor_below_voice_db
    return {
        "timing": (
            0.0 if timing is None else max(0.0, timing - TIMING_TOLERANCE_MS) / TIMING_STEP_MS
        ),
        "clipping": min(CLIP_MAX_POINTS, CLIP_POINTS * metrics.clip_runs),
        "noise": 0.0 if below is None else max(0.0, below - FLOOR_TOLERANCE_DB) / FLOOR_STEP_DB,
        "dropouts": (
            DROPOUT_MAX_POINTS
            if metrics.dropouts is None
            else min(DROPOUT_MAX_POINTS, DROPOUT_POINTS * metrics.dropouts)
        ),
        "bleed": max(0.0, metrics.bleed - BLEED_TOLERANCE) * BLEED_POINTS,
        "deep": 0.0 if metrics.deep_distance is None else DEEP_POINTS * metrics.deep_distance,
    }


def _deep(takes: Sequence[_Take], beat: Path, deep: Deep) -> list[float | None]:
    found: list[float | None] = []
    for take in takes:
        log.info("deep: %s (about 40 s)", take.path.name)
        try:
            found.append(deep_distance(take.path, beat, deep.refs, deep.work_dir, deep.separator))
        except (AudioError, ValueError) as error:
            take.notes.append(f"deep measurement failed: {error}")
            found.append(None)
    return found


def _ordered(measured: Sequence[tuple[_Take, TakeMetrics]]) -> list[RankedTake]:
    scored = [(take, metrics, points(metrics)) for take, metrics in measured]
    known = [p["deep"] for _, m, p in scored if m.deep_distance is not None]
    worst_deep = max(known, default=0.0)
    for take, metrics, part in scored:
        # a take the deep search failed on never wins on it
        if metrics.deep_distance is None and known:
            part["deep"] = worst_deep
            take.notes.append("no deep distance: counted as the farthest take")
    # fewer events make a short take look cleaner than it is, so it never beats a full one
    scored.sort(key=lambda s: (s[1].short, sum(s[2].values()), s[1].timing_ms or 0.0, s[0].name))
    ranked = len(scored) > 1
    return [
        RankedTake(
            take.name,
            take.path,
            i + 1 if ranked else None,
            part,
            sum(part.values()),
            replace(metrics, notes=tuple(take.notes)),
        )
        for i, (take, metrics, part) in enumerate(scored)
    ]


def rank_folder(beat: Path, folder: Path, deep: Deep | None = None) -> Ranking:
    """Measure every take-NN.wav in a folder against the beat and the other takes, and rank.

    A take that cannot be decoded, is at another rate than most, is empty or almost silent,
    or runs well past the beat is left out with the reason. A take shorter than half the beat
    is kept: it is measured over the range it shares with the beat and the other takes, and
    ranked below every full-length take.

    Args:
        beat: The beat the takes were recorded over; frame 0 of a take is frame 0 of it.
        folder: The takes folder; take-NN.json sidecars hold the recorder's dropouts.
        deep: Adds the own-vocal auto search's distance to the references, about 40 s a take.

    Returns:
        The takes in rank order, cleanest and tightest first.

    Raises:
        TakesError: The folder is missing or holds no takes.
        AudioError: The beat cannot be read.
    """
    paths = find_takes(folder)
    beat_decoded = decode(beat)
    beat_mono = beat_decoded.samples.mean(axis=1)
    beat_seconds = len(beat_mono) / beat_decoded.rate
    takes, skipped = _kept(paths, beat_seconds)
    grid = beat_grid(beat_mono, beat_decoded.rate)
    timing = agreement([t.onsets for t in takes], [t.seconds for t in takes])
    measured = [
        (take, _measure(take, timed, grid, beat_decoded, beat_seconds))
        for take, timed in zip(takes, timing, strict=True)
    ]
    if deep is not None:
        distances = _deep(takes, beat, deep)
        measured = [
            (take, replace(metrics, deep_distance=distance))
            for (take, metrics), distance in zip(measured, distances, strict=True)
        ]
    rate = takes[0].decoded.rate if takes else None
    return Ranking(beat, rate, grid, _ordered(measured), skipped, beat_seconds)
