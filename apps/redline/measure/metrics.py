"""The vocal metrics both the measuring run and the auto search read, computed one way."""

import math

import numpy as np
import numpy.typing as npt

from redline.core.dynamics import waveform_kurtosis
from redline.core.loudness import gated_crest_db, integrated_lufs, short_term_spread

Samples = npt.NDArray[np.floating]

METRICS = (
    "pair.vocal_minus_beat_lu",
    "vocal.crest_db",
    "vocal.short_term_spread_lu",
    "vocal.kurtosis",
)


def finite(value: float | None) -> float | None:
    """The value, or None when it is missing or not a finite number."""
    return value if value is not None and math.isfinite(value) else None


def vocal_metrics(vocal: Samples, beat: Samples, rate: int) -> dict[str, float | None]:
    """The METRICS of a vocal over its beat.

    Args:
        vocal: The vocal stem, shape (n, 2).
        beat: The beat stem, shape (n, 2).
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
    return {key: finite(value) for key, value in values.items()}
