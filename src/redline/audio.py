"""Decoding, hot-master evidence at the native rate, and audio prepared for analysis."""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.signal import resample_poly, welch

ANALYSIS_RATE = 44100
HOT_LEVEL = 10 ** (-1 / 20)
LOSSY_CODECS = frozenset({"mp3", "ogg"})
CLIFF_DB = 25.0
CLIFF_SPAN_HZ = 500.0
CLIFF_SEARCH_FROM_HZ = 4000.0

Samples = npt.NDArray[np.float32]


class AudioError(Exception):
    """A file cannot be read as audio."""


@dataclass(frozen=True)
class Decoded:
    """Samples as decoded, shape (n, 2), at the file's own rate."""

    path: Path
    samples: Samples
    rate: int
    codec: str


@dataclass(frozen=True)
class HotMaster:
    """Peak evidence measured before any resampling."""

    sample_peak_db: float
    true_peak_db: float
    hot_share: float
    lossy: bool


@dataclass(frozen=True)
class Audio:
    """Stereo float32 at the analysis rate, with the band limits found in it."""

    samples: Samples
    rate: int
    band_limit_hz: float | None
    side_band_limit_hz: float | None


def decode(path: Path) -> Decoded:
    """Read an audio file as stereo float32 at its own rate."""
    if not path.is_file():
        raise AudioError(f"no such file: {path}")
    try:
        info = sf.info(path)
        data, rate = sf.read(path, always_2d=True, dtype="float32")
    except (sf.LibsndfileError, RuntimeError) as error:
        raise AudioError(f"cannot decode {path}: {error}") from error
    if len(data) == 0:
        raise AudioError(f"no audio in {path}")
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] != 2:
        raise AudioError(f"{path}: {data.shape[1]} channels, only mono and stereo are supported")
    return Decoded(
        path, np.ascontiguousarray(data, dtype=np.float32), int(rate), info.format.lower()
    )


def _db(amplitude: float) -> float:
    return 20 * math.log10(amplitude) if amplitude > 0 else -math.inf


def true_peak_db(samples: Samples) -> float:
    """Peak after 4x oversampling, as in ITU-R BS.1770 true peak."""
    return _db(float(np.abs(resample_poly(samples, 4, 1, axis=0)).max()))


def hot_master(decoded: Decoded) -> HotMaster:
    """Peaks and the share of samples above -1 dBFS, on the signal as decoded."""
    magnitude = np.abs(decoded.samples)
    return HotMaster(
        sample_peak_db=_db(float(magnitude.max())),
        true_peak_db=true_peak_db(decoded.samples),
        hot_share=float((magnitude > HOT_LEVEL).mean()),
        lossy=decoded.codec in LOSSY_CODECS,
    )


def band_limit_hz(signal: npt.NDArray[np.floating], rate: int) -> float | None:
    """Frequency of a codec-style cliff in the spectrum; Nyquist when there is none.

    A cliff is the largest fall of the power spectrum across CLIFF_SPAN_HZ above
    CLIFF_SEARCH_FROM_HZ, counted only when it is at least CLIFF_DB deep.
    """
    if not np.any(signal):
        return None
    freqs, power = welch(signal, fs=rate, nperseg=4096)
    level = 10 * np.log10(power + 1e-20)
    span = max(1, round(CLIFF_SPAN_HZ / (freqs[1] - freqs[0])))
    start = max(span, int(np.searchsorted(freqs, CLIFF_SEARCH_FROM_HZ)))
    best_drop, best_index = 0.0, -1
    for i in range(start, len(level) - span):
        drop = float(level[i - span : i].mean() - level[i + 1 : i + 1 + span].mean())
        if drop > best_drop:
            best_drop, best_index = drop, i
    if best_drop < CLIFF_DB:
        return rate / 2
    return float(freqs[best_index])


def to_analysis(decoded: Decoded) -> Audio:
    """Resample to ANALYSIS_RATE and find the band limits of mid and side."""
    samples = decoded.samples
    if decoded.rate != ANALYSIS_RATE:
        divisor = math.gcd(ANALYSIS_RATE, decoded.rate)
        samples = resample_poly(
            samples, ANALYSIS_RATE // divisor, decoded.rate // divisor, axis=0
        ).astype(np.float32)
    mid = samples.mean(axis=1)
    side = (samples[:, 0] - samples[:, 1]) / 2
    return Audio(
        samples=samples,
        rate=ANALYSIS_RATE,
        band_limit_hz=band_limit_hz(mid, ANALYSIS_RATE),
        side_band_limit_hz=band_limit_hz(side, ANALYSIS_RATE),
    )
