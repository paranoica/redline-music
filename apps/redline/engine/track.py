"""A measured track's engine: its stems with the EQ target, tuning and loudness target."""

import statistics

import numpy as np

from redline.core.loudness import integrated_lufs
from redline.engine.render import Context, Engine
from redline.measure.run import FileResult, Inputs
from redline.vocal.chain import Target, check_vocal


def reference_target(refs: list[FileResult]) -> Target:
    """Per-band median of the references' vocal tonal curves."""
    for ref in refs:
        vocal, mix = ref.values.get("vocal.lufs"), ref.values.get("mix.lufs")
        if not ref.info["vocal.tonal_curve"]["db"] or vocal is None or mix is None:
            raise ValueError(f"{ref.name}: no vocal in the reference: the vocal stem is silent")
        try:
            check_vocal(vocal, mix, "the reference")
        except ValueError as error:
            raise ValueError(f"{ref.name}: {error}") from error
    curves = [r.info["vocal.tonal_curve"] for r in refs]
    centers = tuple(curves[0]["centers_hz"])
    if any(tuple(c["centers_hz"]) != centers for c in curves):
        raise ValueError("the references' vocal curves use different bands")
    median = np.median(np.array([c["db"] for c in curves], dtype=float), axis=0)
    return Target(centers, tuple(float(v) for v in median))


def reference_loudness(refs: list[FileResult]) -> list[float]:
    """The references' integrated loudness; a reference without one is refused."""
    levels = [r.values.get("mix.lufs") for r in refs]
    if any(level is None for level in levels):
        raise ValueError("a reference has no measured loudness")
    return [float(level) for level in levels if level is not None]


def track_engine(inputs: Inputs) -> Engine:
    """The engine of a measured track: its stems, EQ target, tuning and loudness target."""
    stems = inputs.track_stems
    rate = stems.rate
    original = stems.vocal + stems.beat
    check_vocal(integrated_lufs(stems.vocal, rate), integrated_lufs(original, rate))
    context = Context(
        rate,
        reference_target(inputs.refs),
        inputs.upper,
        float(inputs.track.info.get("vocal.tuning_cents") or 0.0),
        round(statistics.median(reference_loudness(inputs.refs)), 1),
    )
    return Engine(stems.vocal, stems.beat, context)
