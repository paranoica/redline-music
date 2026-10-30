"""Settings for a track's engine chosen by measurement: the vocal closest to the references.

Close means the references' medians, each metric scaled by how much the references spread on
it. The references were separated from mastered songs, so a render is scored after its master,
and with a separator the medians are moved by what separation itself adds to the numbers.
Closer to the references is all the numbers say; whether it sounds better is for the ear.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace

import numpy as np
import numpy.typing as npt

from redline.engine.render import CACHE_BYTES, Cancelled, Context, Engine, Rendered, master_target
from redline.engine.settings import SPECS, Settings
from redline.master.clip import master_clip
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
    # distance of each settings in a batch on the same windows and targets; not part of equality
    score: Callable[[Sequence[Settings]], list[float]] | None = field(
        default=None, compare=False, repr=False
    )


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
    vocal: Samples, rate: int, count: int = 3, seconds: float = 6.0
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


def master_pooled(windows: Sequence[Rendered], settings: Settings, context: Context) -> Rendered:
    """The windows joined and put through one master, as the whole track would be.

    A master on each window alone would clip a quiet one deeper than the track's master does.

    Args:
        windows: Renders of the windows without a master, in order.
        settings: The candidate; its master settings choose the target, as in the engine.
        context: The track's; its loudness target serves an automatic master.

    Returns:
        The joined vocal and beat, their mix through master_clip and that one master; the mix
        as it is and no master when the candidate's master is off.
    """
    vocal = np.concatenate([w.vocal for w in windows])
    beat = np.concatenate([w.beat for w in windows])
    mix = vocal + beat
    target = master_target(settings, context)
    final, master = (mix, None) if target is None else master_clip(mix, context.rate, target)
    return Rendered(vocal, beat, final, windows[0].chain, master)


def separation_bias(separator: Separator, pooled: Rendered, rate: int) -> dict[str, float]:
    """What separating a mastered mix adds to each metric, against the stems it was made of.

    Args:
        separator: Splits a mix into vocal and beat; run once, on the pooled final.
        pooled: The windows joined under one master (see master_pooled).
        rate: Sample rate of the render.

    Returns:
        Per metric, the separated stems' value less the after-master stems' value; a metric
        missing on either side is left out.
    """
    clean = measure_processed(*after_master(pooled), rate)
    separated = measure_processed(*separator(pooled.final, rate), rate)
    return {
        metric: a - b
        for metric in SEARCHED
        if (a := separated[metric]) is not None and (b := clean[metric]) is not None
    }


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


class _Windows:
    """The track's windows, each with its own engine and thread, measured joined."""

    def __init__(self, engine: Engine, count: int, seconds: float) -> None:
        self.context = engine.context
        spans = spread_windows(engine.vocal, self.context.rate, count, seconds)
        # built once, so a candidate reuses the stages it shares with those before it
        self.pieces = [
            Engine(engine.vocal[a:b], engine.beat[a:b], self.context, CACHE_BYTES // len(spans))
            for a, b in spans
        ]
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(len(self.pieces))

    def _run(self, piece: Engine, batch: Sequence[Settings]) -> list[Rendered]:
        done = []
        for settings in batch:
            if self.stop.is_set():
                raise Cancelled
            done.append(piece.render(replace(settings, master_on=False), self.stop.is_set))
        return done

    def renders(self, batch: Sequence[Settings]) -> list[list[Rendered]]:
        """Each candidate's windows, rendered without a master; the first error is raised."""
        # one thread per window, so each piece's stage cache serves its candidates in order
        jobs = [self.pool.submit(self._run, piece, batch) for piece in self.pieces]
        finished, _ = wait(jobs, return_when=FIRST_EXCEPTION)
        for job in finished:
            if (error := job.exception()) is not None:
                raise error
        per_piece = [job.result() for job in jobs]
        return [[done[i] for done in per_piece] for i in range(len(batch))]

    def _measure(self, settings: Settings, windows: Sequence[Rendered]) -> dict[str, float | None]:
        pooled = master_pooled(windows, settings, self.context)
        return measure_processed(*after_master(pooled), self.context.rate)

    def measure(self, batch: Sequence[Settings]) -> list[dict[str, float | None]]:
        """Each candidate's metrics on its windows joined under one master."""
        return list(self.pool.map(self._measure, batch, self.renders(batch)))

    def cancel(self) -> None:
        """Stop the renders at their next stage and drop what is queued, without waiting."""
        self.stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


def _at_edge(settings: Settings) -> tuple[str, ...]:
    edges = []
    for key, grid in AXES.items():
        # the level step sets vocal_db anywhere in its range, so only the range ends it
        spec = SPECS[key]
        low, high = (spec.low, spec.high) if key == "vocal_db" else (grid[0], grid[-1])
        if not low < getattr(settings, key) < high:
            edges.append(key)
    return tuple(edges)


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

    Renders only windows spread over the sung part of the track, without a master, and
    measures them joined under one master (see master_pooled). Each pass goes through the
    keys in order and tries every grid value with the other keys as currently best; the
    search ends early once a whole pass moves no key. The windows render side by side, which
    leaves the result as it would be one after another. An error in any window, Ctrl+C
    included, stops the others at their next stage and is raised at once.

    Args:
        engine: The track's engine; its stems and context are used, its cache is not.
        refs: The references' measurements.
        base: Settings to start from and to keep outside AXES; the defaults when None.
        count: Most windows rendered.
        seconds: Length of each window.
        passes: Most rounds over the keys.
        separator: The one the references were separated with. When given, it is run once
            on the pooled final at the base settings, and each target's median is moved by
            the bias separation adds to that metric (see separation_bias).

    Returns:
        The chosen settings, the distances of the base and of the choice, the choice's
        metrics, the targets, the keys at an end of their search (the first or last grid
        value, the ends of its range for vocal_db), the bias per metric, and a score of any
        settings by the same windows and targets (it renders anew what the search did not).
    """
    base = Settings() if base is None else base
    rate = engine.context.rate
    windows = _Windows(engine, count, seconds)
    measured: dict[Settings, dict[str, float | None]] = {}

    def scores(batch: Sequence[Settings]) -> list[float]:
        if new := [s for s in dict.fromkeys(batch) if s not in measured]:
            measured.update(zip(new, windows.measure(new), strict=True))
        return [distance(measured[s], targets) for s in batch]

    try:
        bias: dict[str, float] = {}
        if separator is not None:
            (at_base,) = windows.renders([base])
            pooled = master_pooled(at_base, base, engine.context)
            bias = separation_bias(separator, pooled, rate)
        targets = {
            metric: (median - bias.get(metric, 0.0), spread)
            for metric, (median, spread) in reference_targets(refs).items()
        }
        (before,) = scores([base])
        best, best_distance = _coordinate_search(scores, base, before, passes)
        if passes > 0:
            best, best_distance = _level_to_target(scores, measured, targets, best, best_distance)
    except BaseException:
        windows.cancel()
        raise
    windows.pool.shutdown()

    def score(batch: Sequence[Settings]) -> list[float]:
        nonlocal windows
        if any(s not in measured for s in batch):
            windows = _Windows(engine, count, seconds)
            try:
                scores(batch)
            except BaseException:
                windows.cancel()
                raise
            windows.pool.shutdown()
        return scores(batch)

    return AutoResult(
        best, before, best_distance, measured[best], targets, _at_edge(best), bias, score
    )
