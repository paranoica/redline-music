"""Tonal balance, stereo width and the low end of a signal."""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, sosfilt, welch

from redline.loudness import gated_crest_db

FFT_SIZE = 16384
LOWEST_CENTER_HZ = 40.0
TILT_FROM_HZ = 100.0
TILT_TO_HZ = 12000.0
STEREO_BANDS_HZ = ((40.0, 200.0), (200.0, 4000.0), (4000.0, math.inf))
SIDE_FLOOR_DB = -60.0
SIDE_CEILING_DB = 60.0
LOW_END_HZ = 100.0
LOW_SHARE_FLOOR_DB = -60.0

Samples = npt.NDArray[np.floating]


@dataclass(frozen=True)
class Spectrum:
    """Band energy per 1/3 octave, normalised to the curve's own total, and its slope."""

    centers_hz: tuple[float, ...]
    curve_db: tuple[float, ...]
    tilt_db_per_oct: float | None


@dataclass(frozen=True)
class Stereo:
    """Side-to-mid energy in the low, mid and high bands, and the L-R correlation."""

    side_mid_db: tuple[float | None, float | None, float | None]
    correlation: float | None


@dataclass(frozen=True)
class LowEnd:
    """Share of energy below LOW_END_HZ and the crest factor of that band."""

    share_db: float | None
    crest_db: float | None


def _psd(signal: Samples, rate: int) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    freqs, power = welch(signal, fs=rate, nperseg=min(FFT_SIZE, len(signal)), axis=0)
    return freqs, power


def _band_energy(
    freqs: npt.NDArray[np.float64], power: npt.NDArray[np.float64], lo: float, hi: float
) -> float:
    return float(power[(freqs >= lo) & (freqs < hi)].sum())


def third_octave_centers(upper_hz: float) -> list[float]:
    """Centres 1000·2^(k/3) from LOWEST_CENTER_HZ whose upper edge stays under upper_hz."""
    k_low = math.ceil(3 * math.log2(LOWEST_CENTER_HZ / 1000))
    centers = []
    k = k_low
    while (center := 1000 * 2 ** (k / 3)) * 2 ** (1 / 6) <= upper_hz:
        centers.append(center)
        k += 1
    return centers


def tonal_curve(samples: Samples, rate: int, upper_hz: float) -> Spectrum:
    """1/3-octave band energy of both channels up to upper_hz, normalised to 0 dB total."""
    freqs, power = _psd(samples, rate)
    power = power.mean(axis=1)
    centers = third_octave_centers(min(upper_hz, rate / 2))
    energy = np.array(
        [_band_energy(freqs, power, c * 2 ** (-1 / 6), c * 2 ** (1 / 6)) for c in centers]
    )
    if energy.sum() <= 0:
        return Spectrum((), (), None)
    curve = 10 * np.log10(np.maximum(energy, 1e-30) / energy.sum())
    return Spectrum(tuple(centers), tuple(float(c) for c in curve), _tilt(centers, curve, upper_hz))


def _tilt(centers: list[float], curve: npt.NDArray[np.float64], upper_hz: float) -> float | None:
    top = min(upper_hz, TILT_TO_HZ)
    use = [i for i, c in enumerate(centers) if TILT_FROM_HZ <= c <= top]
    if len(use) < 3:
        return None
    octaves = np.log2(np.array([centers[i] for i in use]))
    return float(np.polyfit(octaves, curve[use], 1)[0])


def stereo_width(samples: Samples, rate: int, upper_hz: float) -> Stereo:
    """Side/mid energy ratio per band (high band capped at upper_hz) and L-R correlation."""
    left, right = samples[:, 0].astype(np.float64), samples[:, 1].astype(np.float64)
    freqs, mid = _psd((left + right) / 2, rate)
    _, side = _psd((left - right) / 2, rate)
    ratios = []
    for lo, hi in STEREO_BANDS_HZ:
        hi = min(hi, upper_hz)
        ratios.append(
            _ratio_db(_band_energy(freqs, side, lo, hi), _band_energy(freqs, mid, lo, hi))
        )
    norm = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
    correlation = float(np.dot(left, right)) / norm if norm > 0 else None
    return Stereo((ratios[0], ratios[1], ratios[2]), correlation)


def _ratio_db(side: float, mid: float) -> float | None:
    if side <= 0 and mid <= 0:
        return None
    if side <= 0:
        return SIDE_FLOOR_DB
    if mid <= 0:
        return SIDE_CEILING_DB
    return min(max(10 * math.log10(side / mid), SIDE_FLOOR_DB), SIDE_CEILING_DB)


def low_end(samples: Samples, rate: int) -> LowEnd:
    """Energy share below LOW_END_HZ in dB, and the gated crest factor of that band."""
    freqs, power = _psd(samples, rate)
    power = power.mean(axis=1)
    total = float(power.sum())
    if total <= 0:
        return LowEnd(None, None)
    low = _band_energy(freqs, power, 0.0, LOW_END_HZ)
    share = 10 * math.log10(low / total) if low > 0 else LOW_SHARE_FLOOR_DB
    return LowEnd(share, gated_crest_db(low_band(samples, rate), rate))


def low_band(samples: Samples, rate: int) -> npt.NDArray[np.float64]:
    """The signal below LOW_END_HZ (8th-order Butterworth)."""
    sos = butter(8, LOW_END_HZ, btype="lowpass", fs=rate, output="sos")
    return np.asarray(sosfilt(sos, samples, axis=0), dtype=np.float64)
