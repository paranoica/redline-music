"""Alternatives to the auto mix: one searched setting moved to its next grid value.

The auto result is the closest to the references the search found, so a variant is mostly
further from them; it is there to be listened to, not because it measures better.
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from redline.engine.render import Engine
from redline.engine.settings import SPECS, Settings, to_dict
from redline.measure.run import FileResult
from redline.measure.separate import Separator
from redline.vocal.auto import AXES, auto_settings

Score = Callable[[Sequence[Settings]], list[float]]


@dataclass(frozen=True)
class Variant:
    """The auto result with one searched setting moved a step."""

    settings: Settings
    axis: str
    direction: str
    value: float
    distance: float | None = None


def neighbours(settings: Settings) -> list[Variant]:
    """Every settings one grid step from these on one searched axis.

    Args:
        settings: The settings to move from.

    Returns:
        For each axis of AXES in order, the nearest grid value strictly below the current one
        and the nearest strictly above, for a value off the grid as for one on it; a side with
        no grid value, or whose value is outside the setting's range, is left out.
    """
    found = []
    for key, grid in AXES.items():
        current, spec = getattr(settings, key), SPECS[key]
        below = [v for v in grid if v < current and spec.low <= v <= spec.high]
        above = [v for v in grid if v > current and spec.low <= v <= spec.high]
        for direction, values in (("down", below[-1:]), ("up", above[:1])):
            for value in values:
                moved = replace(settings, **{key: float(value)})
                found.append(Variant(moved, key, direction, float(value)))
    return found


def variants(
    settings: Settings, seed: int, n: int = 3, score: Score | None = None
) -> list[Variant]:
    """Up to n distinct neighbours of the settings, chosen by the seed.

    Args:
        settings: The auto result.
        seed: The same seed gives the same variants.
        n: How many; fewer when there are not so many neighbours.
        score: Distance of a batch of settings; each variant carries it when given.

    Returns:
        The chosen neighbours in the order neighbours lists them.
    """
    near = neighbours(settings)
    picked = sorted(random.Random(seed).sample(range(len(near)), min(max(n, 0), len(near))))
    chosen = [near[i] for i in picked]
    if score is not None and chosen:
        distances = score([v.settings for v in chosen])
        chosen = [replace(v, distance=d) for v, d in zip(chosen, distances, strict=True)]
    return chosen


def auto_with_variants(
    engine: Engine,
    refs: Sequence[FileResult],
    seed: int,
    base: Settings | None = None,
    n: int = 3,
    count: int = 3,
    seconds: float = 6.0,
    passes: int = 2,
    separator: Separator | None = None,
) -> dict[str, Any]:
    """The auto mix and its variants as plain JSON values.

    Args:
        engine: The track's engine.
        refs: The references' measurements.
        seed: Chooses the variants.
        base: Settings to start from; the defaults when None.
        n: How many variants.
        count: Most windows rendered.
        seconds: Length of each window.
        passes: Most rounds over the keys.
        separator: As for auto_settings.

    Returns:
        {"auto": the settings as a file holds them plus distance_before, distance_after and
        at_edge, "variants": [settings, axis, direction, value, distance]}.
    """
    found = auto_settings(engine, refs, base, count, seconds, passes, separator)
    chosen = variants(found.settings, seed, n, found.score)
    return {
        "auto": {
            **to_dict(found.settings),
            "distance_before": found.distance_before,
            "distance_after": found.distance_after,
            "at_edge": list(found.at_edge),
        },
        "variants": [
            {
                "settings": to_dict(v.settings),
                "axis": v.axis,
                "direction": v.direction,
                "value": v.value,
                "distance": v.distance,
            }
            for v in chosen
        ],
    }
