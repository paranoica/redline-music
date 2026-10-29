"""Settings for a track's engine chosen by measurement: the vocal closest to the references.

Close means the references' medians, each metric scaled by how much the references spread on
it. Closer to the references is all the numbers say; whether it sounds better is for the ear.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from redline.core.dynamics import waveform_kurtosis
from redline.core.loudness import gated_crest_db, integrated_lufs, short_term_spread
from redline.engine.render import Engine
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
# what a metric the render lost costs: as if it sat three spreads off
MISSING = 9.0
AXES: dict[str, tuple[float, ...]] = {
    "vocal_db": (-12, -9, -6, -3, 0, 3),
    "comp_threshold_db": (-50, -44, -38, -32, -26),
    "drive_db": (10, 20, 30, 40),
}


@dataclass(frozen=True)
class AutoResult:
    """The chosen settings and how close they and the base came to the references."""

    settings: Settings
    distance_before: float
    distance_after: float
    values: dict[str, float | None]
    targets: dict[str, tuple[float, float]]


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
        0 at the medians; a metric no reference has adds nothing, one the render lost adds
        MISSING, so a setting cannot win by making a metric unmeasurable.
    """
    total = 0.0
    for metric, (median, spread) in targets.items():
        value = values.get(metric)
        total += MISSING if value is None else ((value - median) / spread) ** 2
    return total


def loudest_excerpt(vocal: Samples, rate: int, seconds: float) -> tuple[int, int]:
    """The stretch of the given length, starting on a whole second, where the vocal is loudest.

    Args:
        vocal: The vocal, shape (n, 2).
        rate: Sample rate.
        seconds: Length of the stretch.

    Returns:
        Start and end sample; the whole vocal when it is no longer than the stretch.
    """
    if seconds <= 0:
        raise ValueError("the excerpt needs a length above zero")
    size = round(seconds * rate)
    if len(vocal) <= size:
        return 0, len(vocal)
    power = np.square(np.asarray(vocal, dtype=np.float64)).sum(axis=1)
    energy = np.concatenate(([0.0], np.cumsum(power)))
    starts = np.arange(0, len(vocal) - size + 1, rate)
    best = int(starts[np.argmax(energy[starts + size] - energy[starts])])
    return best, best + size


def auto_settings(
    engine: Engine,
    refs: Sequence[FileResult],
    base: Settings | None = None,
    seconds: float = 30.0,
    passes: int = 2,
) -> AutoResult:
    """Coordinate search over AXES for the settings whose render measures closest.

    Renders only the loudest excerpt of the track. Each pass goes through the keys in order
    and tries every grid value with the other keys as currently best.

    Args:
        engine: The track's engine; its stems and context are used, its cache is not.
        refs: The references' measurements.
        base: Settings to start from and to keep outside AXES; the defaults when None.
        seconds: Length of the excerpt rendered.
        passes: Rounds over the keys.

    Returns:
        The chosen settings, the distances of the base and of the choice, the choice's
        metrics and the targets.
    """
    base = Settings() if base is None else base
    targets = reference_targets(refs)
    rate = engine.context.rate
    start, end = loudest_excerpt(engine.vocal, rate, seconds)
    excerpt = Engine(engine.vocal[start:end], engine.beat[start:end], engine.context)
    measured: dict[Settings, dict[str, float | None]] = {}

    def measure(settings: Settings) -> dict[str, float | None]:
        if settings not in measured:
            rendered = excerpt.render(settings)
            measured[settings] = measure_processed(rendered.vocal, rendered.beat, rate)
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
    return AutoResult(best, before, best_distance, measure(best), targets)
