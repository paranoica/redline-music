"""Settings for a track's engine chosen by measurement: the vocal closest to the references.

Close means the references' medians, each metric scaled by how much the references spread on
it. The references were separated from mastered songs, so a render is scored after its master,
and with a separator the medians are moved by what separation itself adds to the numbers.
Closer to the references is all the numbers say; whether it sounds better is for the ear.
"""

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from redline.engine.render import CACHE_BYTES, Engine, Rendered
from redline.engine.settings import SPECS, Settings
from redline.measure.metrics import METRICS, finite, vocal_metrics
from redline.measure.run import FileResult
from redline.measure.separate import Separator

Samples = npt.NDArray[np.floating]

# the smallest spread a metric is scaled by; differences below it are measuring noise
FLOOR = {
    "pair.vocal_minus_beat_lu": 1.0,
    "vocal.crest_db": 1.0,
    "vocal.short_term_spread_lu": 0.5,
    "vocal.kurtosis": 0.5,
}
# the spread of loudness over a few seconds is not the spread over a song, so it is left to
# the measurements of whole tracks and not searched on
SEARCHED = tuple(m for m in METRICS if m != "vocal.short_term_spread_lu")
PAIR = "pair.vocal_minus_beat_lu"
# what a metric the render lost costs: more than any realistic distance on the others
MISSING = 100.0
AXES: dict[str, tuple[float, ...]] = {
    "vocal_db": (-12, -9, -6, -3, 0, 3, 6),
    "comp_threshold_db": (-56, -50, -44, -38, -32, -26, -20, -14),
    "drive_db": (0, 10, 20, 30, 40),
}
LEVEL_STEPS = 2
# a block counts as sung when it is within this much of the vocal's loudest block
ACTIVE_DB = 30.0
# below this the mix is too close to zero for the master's change to be read off it
SILENT = 1e-6


@dataclass(frozen=True)
class AutoResult:
    """The chosen settings and how close they and the base came to the references.

    The targets are the ones the distances were taken to: the references' medians less the
    bias separation adds to each metric; the bias is empty without a separator.
    """

    settings: Settings
    distance_before: float
    distance_after: float
    values: dict[str, float | None]
    targets: dict[str, tuple[float, float]]
    at_edge: tuple[str, ...]
    bias: dict[str, float]


def reference_targets(refs: Sequence[FileResult]) -> dict[str, tuple[float, float]]:
    """Median and spread of every metric over the references that have it.

    Args:
        refs: The references' measurements.

    Returns:
        For each metric at least one reference has, its median and its interquartile range
        floored at FLOOR; metrics no reference has are left out.
    """
    targets = {}
    for metric in SEARCHED:
        known = [v for r in refs if (v := finite(r.values.get(metric))) is not None]
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
    return vocal_metrics(vocal, beat, rate)


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


def separation_bias(
    separator: Separator, windows: Sequence[Rendered], rate: int
) -> dict[str, float]:
    """What separating a mastered mix adds to each metric, against the stems it was made of.

    Args:
        separator: Splits a mix into vocal and beat; run once on each window's final.
        windows: Renders of the windows, each with its master.
        rate: Sample rate of the renders.

    Returns:
        Per metric, the median over windows of the separated stems' value less the after-master
        stems' value; windows missing the metric on either side are left out, and a metric no
        window has on both is left out.
    """
    differences: dict[str, list[float]] = {}
    for rendered in windows:
        clean = measure_processed(*after_master(rendered), rate)
        separated = measure_processed(*separator(rendered.final, rate), rate)
        for metric in SEARCHED:
            if (a := separated[metric]) is not None and (b := clean[metric]) is not None:
                differences.setdefault(metric, []).append(a - b)
    return {metric: float(np.median(found)) for metric, found in differences.items()}


def _coordinate_search(
    scores: Callable[[Sequence[Settings]], list[float]],
    base: Settings,
    base_score: float,
    passes: int,
) -> tuple[Settings, float]:
    best, best_score = base, base_score
    for _ in range(passes):
        moved = False
        for key, grid in AXES.items():
            # only the key changes, so the grid's candidates do not depend on each other
            candidates = [replace(best, **{key: float(value)}) for value in grid]
            for candidate, score in zip(candidates, scores(candidates), strict=True):
                # strictly lower only, so a tie keeps what is already chosen
                if score < best_score:
                    best, best_score, moved = candidate, score, True
        if not moved:
            break
    return best, best_score


def _level_to_target(
    scores: Callable[[Sequence[Settings]], list[float]],
    measured: Mapping[Settings, Mapping[str, float | None]],
    targets: Mapping[str, tuple[float, float]],
    best: Settings,
    best_score: float,
) -> tuple[Settings, float]:
    # the vocal's level moves the pair by as many LU as dB, so it is set to the target
    # directly instead of the nearest grid value; the master bends that a little, hence twice
    spec = SPECS["vocal_db"]
    for _ in range(LEVEL_STEPS):
        pair = measured[best].get(PAIR)
        if PAIR not in targets or pair is None:
            break
        wanted = best.vocal_db + targets[PAIR][0] - pair
        level = float(np.clip(round(wanted / spec.step) * spec.step, spec.low, spec.high))
        if level == best.vocal_db:
            break
        candidate = replace(best, vocal_db=level)
        (score,) = scores([candidate])
        if score >= best_score:
            break
        best, best_score = candidate, score
    return best, best_score


def auto_settings(
    engine: Engine,
    refs: Sequence[FileResult],
    base: Settings | None = None,
    count: int = 3,
    seconds: float = 6.0,
    passes: int = 2,
    separator: Separator | None = None,
) -> AutoResult:
    """Coordinate search over AXES for the settings whose render measures closest.

    Renders only windows spread over the sung part of the track, each through the master,
    and measures them pooled. Each pass goes through the keys in order and tries every grid
    value with the other keys as currently best; the search ends early once a whole pass
    moves no key. The windows render side by side, which leaves the result as it would be
    one after another.

    Args:
        engine: The track's engine; its stems and context are used, its cache is not.
        refs: The references' measurements.
        base: Settings to start from and to keep outside AXES; the defaults when None.
        count: Most windows rendered.
        seconds: Length of each window.
        passes: Most rounds over the keys.
        separator: The one the references were separated with. When given, it is run once
            on each window's final at the base settings, and each target's median is moved
            by the bias separation adds to that metric (see separation_bias).

    Returns:
        The chosen settings, the distances of the base and of the choice, the choice's
        metrics, the targets, the keys whose chosen value is the first or last of its grid,
        and the bias per metric.
    """
    base = Settings() if base is None else base
    rate = engine.context.rate
    windows = spread_windows(engine.vocal, rate, count, seconds)
    # built once, so a candidate reuses the stages it shares with those before it
    pieces = [
        Engine(engine.vocal[a:b], engine.beat[a:b], engine.context, CACHE_BYTES // len(windows))
        for a, b in windows
    ]
    measured: dict[Settings, dict[str, float | None]] = {}

    with ThreadPoolExecutor(len(pieces)) as pool:

        def renders(batch: Sequence[Settings]) -> list[list[Rendered]]:
            # one thread per window, so each piece's stage cache serves its candidates in order
            jobs = [pool.submit(lambda p: [p.render(s) for s in batch], piece) for piece in pieces]
            per_piece = [job.result() for job in jobs]
            return [[done[i] for done in per_piece] for i in range(len(batch))]

        def pooled(rendered: Sequence[Rendered]) -> dict[str, float | None]:
            stems = [after_master(r) for r in rendered]
            vocal = np.concatenate([v for v, _ in stems])
            beat = np.concatenate([b for _, b in stems])
            return measure_processed(vocal, beat, rate)

        def measure_all(batch: Sequence[Settings]) -> None:
            new = [s for s in dict.fromkeys(batch) if s not in measured]
            if new:
                measured.update(zip(new, pool.map(pooled, renders(new)), strict=True))

        bias: dict[str, float] = {}
        if separator is not None:
            (at_base,) = renders([base])
            bias = separation_bias(separator, at_base, rate)
        targets = {
            metric: (median - bias.get(metric, 0.0), spread)
            for metric, (median, spread) in reference_targets(refs).items()
        }

        def scores(batch: Sequence[Settings]) -> list[float]:
            measure_all(batch)
            return [distance(measured[s], targets) for s in batch]

        (before,) = scores([base])
        best, best_distance = _coordinate_search(scores, base, before, passes)
        if passes > 0:
            best, best_distance = _level_to_target(scores, measured, targets, best, best_distance)
    at_edge = tuple(
        key for key, grid in AXES.items() if not grid[0] < getattr(best, key) < grid[-1]
    )
    return AutoResult(best, before, best_distance, measured[best], targets, at_edge, bias)
