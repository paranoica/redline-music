"""Settings for a track's engine chosen by measurement: the vocal closest to the references.

Close means the references' medians, each metric scaled by how much the references spread on
it. The references were separated from mastered songs, so a render is scored after its master.
Closer to the references is all the numbers say; whether it sounds better is for the ear.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from redline.core.dynamics import waveform_kurtosis
from redline.core.loudness import gated_crest_db, integrated_lufs, short_term_spread
from redline.engine.render import CACHE_BYTES, Engine, Rendered
from redline.engine.settings import Settings
from redline.measure.run import FileResult

Samples = npt.NDArray[np.floating]

METRICS = (
    "pair.vocal_minus_beat_lu",
    "vocal.crest_db",
    "vocal.short_term_spread_lu",
    "vocal.kurtosis",
)
# the smallest spread a metric is scaled by; differences below it are measuring noise
FLOOR = {
    "pair.vocal_minus_beat_lu": 1.0,
    "vocal.crest_db": 1.0,
    "vocal.short_term_spread_lu": 0.5,
    "vocal.kurtosis": 0.5,
}
# what a metric the render lost costs: more than any realistic distance on the others
MISSING = 100.0
AXES: dict[str, tuple[float, ...]] = {
    "vocal_db": (-12, -9, -6, -3, 0, 3, 6),
    "comp_threshold_db": (-56, -50, -44, -38, -32, -26, -20, -14),
    "drive_db": (0, 10, 20, 30, 40),
}
# a block counts as sung when it is within this much of the vocal's loudest block
ACTIVE_DB = 30.0
# below this the mix is too close to zero for the master's change to be read off it
SILENT = 1e-6


@dataclass(frozen=True)
class AutoResult:
    """The chosen settings and how close they and the base came to the references."""

    settings: Settings
    distance_before: float
    distance_after: float
    values: dict[str, float | None]
    targets: dict[str, tuple[float, float]]
    at_edge: tuple[str, ...]


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def reference_targets(refs: Sequence[FileResult]) -> dict[str, tuple[float, float]]:
    """Median and spread of every metric over the references that have it.

    Args:
        refs: The references' measurements.

    Returns:
        For each metric at least one reference has, its median and its interquartile range
        floored at FLOOR; metrics no reference has are left out.
    """
    targets = {}
    for metric in METRICS:
        known = [v for r in refs if (v := _finite(r.values.get(metric))) is not None]
        if not known:
            continue
        low, high = np.percentile(known, [25, 75])
        targets[metric] = (float(np.median(known)), max(float(high - low), FLOOR[metric]))
    return targets


def measure_processed(vocal: Samples, beat: Samples, rate: int) -> dict[str, float | None]:
    """The METRICS on a rendered vocal and beat, by the functions the references went through.

    Args:
        vocal: The processed vocal, shape (n, 2).
        beat: The processed beat, shape (n, 2).
        rate: Sample rate of both.

    Returns:
        Each metric, None where the signal gives nothing to measure.
    """
    values = {
        "pair.vocal_minus_beat_lu": integrated_lufs(vocal, rate) - integrated_lufs(beat, rate),
        "vocal.crest_db": gated_crest_db(vocal, rate),
        "vocal.short_term_spread_lu": short_term_spread(vocal, rate),
        "vocal.kurtosis": waveform_kurtosis(vocal, rate),
    }
    return {key: _finite(value) for key, value in values.items()}


def distance(
    values: Mapping[str, float | None], targets: Mapping[str, tuple[float, float]]
) -> float:
    """Sum of squared distances from the medians in spreads, over metrics both sides have.

    Args:
        values: Measured metrics.
        targets: Median and spread per metric.

    Returns:
        0 at the medians. A metric no reference has adds nothing; one the render lost adds
        MISSING, more than any realistic distance, so losing a metric never wins.
    """
    total = 0.0
    for metric, (median, spread) in targets.items():
        value = values.get(metric)
        total += MISSING if value is None else ((value - median) / spread) ** 2
    return total


def spread_windows(
    vocal: Samples, rate: int, count: int = 4, seconds: float = 8.0
) -> list[tuple[int, int]]:
    """Windows of the given length spread evenly over the part of the track that is sung.

    The sung part runs from the first to the last whole-second block within ACTIVE_DB of
    the vocal's loudest block; the first window starts where it starts and the last ends
    where it ends. Fewer windows are taken when count of them do not fit without overlap.

    Args:
        vocal: The vocal, shape (n, 2).
        rate: Sample rate.
        count: Most windows to take.
        seconds: Length of each window.

    Returns:
        Start and end sample of each window, in order. One window, in the middle of the sung
        part, when only one fits; it covers the whole track when the track is no longer.

    Raises:
        ValueError: count is below one or seconds not above zero.
    """
    if count < 1 or seconds <= 0:
        raise ValueError("the windows need a count of one or more and a length above zero")
    size, total = round(seconds * rate), len(vocal)
    power = np.square(np.asarray(vocal, dtype=np.float64)).sum(axis=1)
    edges = np.arange(0, total, rate)
    blocks = np.add.reduceat(power, edges) / np.diff(np.append(edges, total))
    first, last = 0, total
    if blocks.max() > 0:
        with np.errstate(divide="ignore"):
            level = 10 * np.log10(blocks)
        active = np.flatnonzero(level >= level.max() - ACTIVE_DB)
        first, last = int(active[0]) * rate, min(total, (int(active[-1]) + 1) * rate)
    span = last - first
    fits = min(count, span // size)
    if fits <= 1:
        start = max(0, min(first + (span - size) // 2, total - size))
        return [(start, min(total, start + size))]
    step = (span - size) / (fits - 1)
    return [(first + round(i * step), first + round(i * step) + size) for i in range(fits)]


def after_master(rendered: Rendered) -> tuple[Samples, Samples]:
    """The vocal and the beat as the master left them, each changed sample by sample as the mix.

    The ratio of the final to the mix is held between 0 and the master's gain: a clipper
    never goes past either, and outside them it is the band limit ringing near a zero of
    the mix, which would multiply a stem by hundreds.

    Args:
        rendered: A render; its vocal and beat sum to the mix the master was given.

    Returns:
        The vocal and the beat, each times the master's change; as rendered without a master.
    """
    if rendered.master is None:
        return rendered.vocal, rendered.beat
    gain = 10 ** (rendered.master.gain_db / 20)
    mix = rendered.vocal + rendered.beat
    sounding = np.abs(mix) > SILENT
    ratio = np.divide(rendered.final, mix, out=np.full(mix.shape, gain), where=sounding)
    ratio = np.clip(ratio, 0.0, gain).astype(np.float32)
    return rendered.vocal * ratio, rendered.beat * ratio


def auto_settings(
    engine: Engine,
    refs: Sequence[FileResult],
    base: Settings | None = None,
    count: int = 4,
    seconds: float = 8.0,
    passes: int = 2,
) -> AutoResult:
    """Coordinate search over AXES for the settings whose render measures closest.

    Renders only windows spread over the sung part of the track, each through the master,
    and measures them pooled. Each pass goes through the keys in order and tries every grid
    value with the other keys as currently best.

    Args:
        engine: The track's engine; its stems and context are used, its cache is not.
        refs: The references' measurements.
        base: Settings to start from and to keep outside AXES; the defaults when None.
        count: Most windows rendered.
        seconds: Length of each window.
        passes: Rounds over the keys.

    Returns:
        The chosen settings, the distances of the base and of the choice, the choice's
        metrics, the targets and the keys whose chosen value is the first or last of its grid.
    """
    base = Settings() if base is None else base
    targets = reference_targets(refs)
    rate = engine.context.rate
    windows = spread_windows(engine.vocal, rate, count, seconds)
    # built once, so a candidate reuses the stages it shares with those before it
    pieces = [
        Engine(engine.vocal[a:b], engine.beat[a:b], engine.context, CACHE_BYTES // len(windows))
        for a, b in windows
    ]
    measured: dict[Settings, dict[str, float | None]] = {}

    def measure(settings: Settings) -> dict[str, float | None]:
        if settings not in measured:
            stems = [after_master(piece.render(settings)) for piece in pieces]
            vocal = np.concatenate([v for v, _ in stems])
            beat = np.concatenate([b for _, b in stems])
            measured[settings] = measure_processed(vocal, beat, rate)
        return measured[settings]

    best = base
    before = best_distance = distance(measure(base), targets)
    for _ in range(passes):
        for key, grid in AXES.items():
            for value in grid:
                candidate = replace(best, **{key: float(value)})
                score = distance(measure(candidate), targets)
                # strictly lower only, so a tie keeps what is already chosen
                if score < best_distance:
                    best, best_distance = candidate, score
    at_edge = tuple(key for key, grid in AXES.items() if getattr(best, key) in (grid[0], grid[-1]))
    return AutoResult(best, before, best_distance, measure(best), targets, at_edge)
