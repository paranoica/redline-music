"""Loudness and dynamics: BS.1770 loudness, EBU loudness range, short-term spread, crest."""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyloudnorm as pyln

MIN_SECONDS = 3.0
WINDOW_SECONDS = 0.4
HOP_SECONDS = 0.1
ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -20.0

Samples = npt.NDArray[np.floating]


@dataclass(frozen=True)
class Loudness:
    """Loudness numbers of one signal; None where the signal gives nothing to measure."""

    integrated_lufs: float
    lra_lu: float | None
    short_term_spread_lu: float | None
    crest_db: float | None


def integrated_lufs(samples: Samples, rate: int) -> float:
    """Integrated loudness per ITU-R BS.1770-4; -inf for digital silence."""
    if not np.any(samples):
        return -math.inf
    return float(pyln.Meter(rate).integrated_loudness(samples))


def loudness_range(samples: Samples, rate: int) -> float | None:
    """Loudness range per EBU Tech 3342, in LU."""
    if not np.any(samples):
        return None
    value = float(pyln.Meter(rate).loudness_range(samples))
    return None if math.isnan(value) else value


def _gated(levels: npt.NDArray[np.floating]) -> npt.NDArray[np.floating]:
    """Keep levels above the absolute gate and within RELATIVE_GATE of their power mean."""
    above = levels[np.isfinite(levels) & (levels >= ABSOLUTE_GATE)]
    if above.size == 0:
        return above
    mean = 10 * np.log10(np.mean(10 ** (above / 10)))
    return above[above >= mean + RELATIVE_GATE]


def short_term_spread(samples: Samples, rate: int) -> float | None:
    """Interquartile range of momentary (400 ms) loudness over gated windows, in LU."""
    if not np.any(samples):
        return None
    meter = pyln.Meter(rate, block_size=WINDOW_SECONDS, overlap=1 - HOP_SECONDS / WINDOW_SECONDS)
    meter.integrated_loudness(samples)
    levels = _gated(np.asarray(meter.blockwise_loudness, dtype=np.float64))
    if levels.size < 2:
        return None
    low, high = np.percentile(levels, [25, 75])
    return float(high - low)


def gated_crest_db(samples: Samples, rate: int) -> float | None:
    """Median peak-to-RMS ratio of 400 ms windows that pass the loudness gates, in dB."""
    window, hop = int(WINDOW_SECONDS * rate), int(HOP_SECONDS * rate)
    if len(samples) < window:
        return None
    starts = range(0, len(samples) - window + 1, hop)
    peaks = np.array([np.abs(samples[s : s + window]).max() for s in starts])
    power = np.array([np.mean(np.square(samples[s : s + window])) for s in starts])
    with np.errstate(divide="ignore"):
        levels = 10 * np.log10(power)
    keep = np.isin(levels, _gated(levels))
    if not keep.any():
        return None
    crest = 20 * np.log10(peaks[keep]) - 10 * np.log10(power[keep])
    return float(np.median(crest))


def measure_loudness(samples: Samples, rate: int) -> Loudness:
    """All loudness numbers of one signal; refuses signals shorter than MIN_SECONDS."""
    if len(samples) < round(MIN_SECONDS * rate):
        raise ValueError(f"signal too short: {len(samples) / rate:.2f} s, need {MIN_SECONDS} s")
    return Loudness(
        integrated_lufs=integrated_lufs(samples, rate),
        lra_lu=loudness_range(samples, rate),
        short_term_spread_lu=short_term_spread(samples, rate),
        crest_db=gated_crest_db(samples, rate),
    )
