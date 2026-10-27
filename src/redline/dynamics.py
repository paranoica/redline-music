"""Saturation and reverb tails: what overload and wet or dry processing leave in a signal."""

import numpy as np
import numpy.typing as npt
from scipy.stats import kurtosis

from redline.loudness import HOP_SECONDS, WINDOW_SECONDS, gate_levels
from redline.spectrum import low_band

ENVELOPE_SECONDS = 0.02
ENVELOPE_HOP_SECONDS = 0.005
ENVELOPE_FLOOR_DB = -120.0
KNEE_DB = 3.0
TAIL_SPAN_DB = 20.0
TAIL_SEARCH_SECONDS = 1.0

Samples = npt.NDArray[np.floating]


def _windows(mono: npt.NDArray[np.float64], size: int, hop: int) -> npt.NDArray[np.float64]:
    if len(mono) < size:
        return np.empty((0, size))
    return np.lib.stride_tricks.sliding_window_view(mono, size)[::hop]


def waveform_kurtosis(samples: Samples, rate: int) -> float | None:
    """Median excess kurtosis of gated 400 ms windows; saturation and clipping lower it."""
    mono = np.asarray(samples, dtype=np.float64).mean(axis=1)
    frames = _windows(mono, int(WINDOW_SECONDS * rate), int(HOP_SECONDS * rate))
    power = np.mean(frames**2, axis=1)
    with np.errstate(divide="ignore"):
        levels = 10 * np.log10(power)
    keep = np.isin(levels, gate_levels(levels))
    if not keep.any():
        return None
    return float(np.median(kurtosis(frames[keep], axis=1, fisher=True)))


def low_end_kurtosis(samples: Samples, rate: int) -> float | None:
    """Waveform kurtosis of the band below the low-end cutoff (the 808's saturation)."""
    return waveform_kurtosis(low_band(samples, rate), rate)


def _envelope_db(samples: Samples, rate: int) -> npt.NDArray[np.float64]:
    mono = np.asarray(samples, dtype=np.float64).mean(axis=1)
    frames = _windows(mono, int(ENVELOPE_SECONDS * rate), int(ENVELOPE_HOP_SECONDS * rate))
    with np.errstate(divide="ignore"):
        return np.maximum(10 * np.log10(np.mean(frames**2, axis=1)), ENVELOPE_FLOOR_DB)


def tail_decay(samples: Samples, rate: int) -> tuple[float | None, int]:
    """Median decay rate at phrase ends in dB/s, and how many phrase ends were found.

    A phrase end is where the envelope of an active passage falls KNEE_DB under its peak;
    the rate is TAIL_SPAN_DB over the time the envelope then takes to fall that much more.
    An exponential tail of reverberation time RT60 reads 60 / RT60.
    """
    envelope = _envelope_db(samples, rate)
    active = gate_levels(envelope[envelope > ENVELOPE_FLOOR_DB])
    if active.size == 0:
        return None, 0
    threshold = float(active.min())
    hop = ENVELOPE_HOP_SECONDS
    reach = int(TAIL_SEARCH_SECONDS / hop)
    rates: list[float] = []
    i = 0
    while i < len(envelope):
        if envelope[i] < threshold:
            i += 1
            continue
        peak, knee = envelope[i], None
        for j in range(i, min(i + reach, len(envelope))):
            peak = max(peak, envelope[j])
            if envelope[j] <= peak - KNEE_DB:
                knee = j
                break
        if knee is None:
            i += reach
            continue
        below = np.nonzero(envelope[knee : knee + reach] <= peak - KNEE_DB - TAIL_SPAN_DB)[0]
        if below.size:
            rates.append(TAIL_SPAN_DB / (max(int(below[0]), 1) * hop))
            i = knee + int(below[0]) + 1
        else:
            i = knee + 1
    if not rates:
        return None, 0
    return float(np.median(rates)), len(rates)
