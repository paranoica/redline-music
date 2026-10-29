"""Stage 3 effects: a crossover, 808 saturation and a clipping master."""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, sosfilt, sosfiltfilt

from redline.core import native
from redline.core.dsp import drive, hard_clip, oversampled
from redline.core.loudness import integrated_lufs
from redline.core.spectrum import low_band

Samples = npt.NDArray[np.floating]

CEILING_DB = -0.1
MAX_GAIN_DB = 30.0
OVERSAMPLING = 8
# tanh harmonics of a band under 120 Hz fall off long before 2x Nyquist
LOW_OVERSAMPLING = 2
SEARCH_STEPS = 18
CORRECTIONS = 4
MIN_SLOPE = 0.05
LUFS_TOLERANCE = 0.1
REACH_TOLERANCE_LU = 0.5
CLIP_PASSES = 2
BAND_LIMIT_HZ = 20000.0


@dataclass(frozen=True)
class MasterResult:
    """What the master did: the gain on unclipped samples and whether the target was reached."""

    target_lufs: float
    gain_db: float
    reached: bool
    clipped_share: float
    trim_db: float


def crossover(samples: Samples, rate: int, freq: float) -> tuple[Samples, Samples]:
    """4th-order Linkwitz-Riley split; the two bands sum back to an allpass of the input."""
    low_sos = butter(2, freq, btype="lowpass", fs=rate, output="sos")
    high_sos = butter(2, freq, btype="highpass", fs=rate, output="sos")
    low = sosfilt(low_sos, sosfilt(low_sos, samples, axis=0), axis=0)
    high = sosfilt(high_sos, sosfilt(high_sos, samples, axis=0), axis=0)
    return np.asarray(low, dtype=np.float32), np.asarray(high, dtype=np.float32)


def _ceiling() -> float:
    return 10 ** (CEILING_DB / 20)


def _clip_cheap(samples: Samples, gain_db: float) -> Samples:
    return hard_clip(samples * np.float32(10 ** (gain_db / 20)), _ceiling())


def _clip_clean(samples: Samples, rate: int, gain_db: float) -> tuple[Samples, float]:
    out = native.oversampled_clip(
        samples, rate, 10 ** (gain_db / 20), _ceiling(), OVERSAMPLING, BAND_LIMIT_HZ, CLIP_PASSES
    )
    return _clip_clean_numpy(samples, rate, gain_db) if out is None else _trimmed(out)


def _clip_clean_numpy(samples: Samples, rate: int, gain_db: float) -> tuple[Samples, float]:
    gained = samples * np.float32(10 ** (gain_db / 20))
    band = butter(8, BAND_LIMIT_HZ, fs=rate * OVERSAMPLING, output="sos")

    def stage(v: Samples) -> Samples:
        # clipping, then removing what the clip put above the audio band, a few times over:
        # the flat tops the decimation would ring around are mostly gone before it runs
        for _ in range(CLIP_PASSES):
            v = np.asarray(sosfiltfilt(band, hard_clip(v, _ceiling()), axis=0), dtype=np.float32)
        return hard_clip(v, _ceiling())

    return _trimmed(oversampled(stage, gained, OVERSAMPLING))


def _trimmed(out: Samples) -> tuple[Samples, float]:
    # what still rings past the ceiling is taken off the whole file, not clipped again,
    # which would bring the aliasing back
    peak = float(np.abs(out).max())
    if peak <= _ceiling():
        return out, 0.0
    return out * np.float32(_ceiling() / peak), 20 * math.log10(_ceiling() / peak)


def clip_at(samples: Samples, gain_db: float, trim_db: float = 0.0) -> Samples:
    """The master's clip at a known gain and trim, at the base rate: a quick stand-in."""
    out = _clip_cheap(samples, gain_db)
    return out if trim_db == 0 else (out * np.float32(10 ** (trim_db / 20))).astype(np.float32)


def master_clip(samples: Samples, rate: int, target_lufs: float) -> tuple[Samples, MasterResult]:
    """Raise the mix into a hard clip at CEILING_DB until it reads target_lufs.

    The gain is searched on a base-rate clip, then the result is rendered at OVERSAMPLING
    times the rate and the gain corrected on that render, which reads quieter. A result
    more than REACH_TOLERANCE_LU off the target is reported as not reached.
    """
    loudness = integrated_lufs(samples, rate)
    if not math.isfinite(loudness):
        raise ValueError("nothing to master: the mix is silent")
    plain_gain = target_lufs - loudness
    peak = float(np.abs(samples).max())
    if peak * 10 ** (min(plain_gain, MAX_GAIN_DB) / 20) <= _ceiling():
        gain = min(plain_gain, MAX_GAIN_DB)
        out = (samples * np.float32(10 ** (gain / 20))).astype(np.float32)
        return out, MasterResult(target_lufs, gain, gain == plain_gain, 0.0, 0.0)
    low, high = plain_gain, MAX_GAIN_DB
    if integrated_lufs(_clip_cheap(samples, high), rate) >= target_lufs:
        for _ in range(SEARCH_STEPS):
            middle = (low + high) / 2
            if integrated_lufs(_clip_cheap(samples, middle), rate) < target_lufs:
                low = middle
            else:
                high = middle
    out, trim = _clip_clean(samples, rate, high)
    for _ in range(CORRECTIONS):
        short = target_lufs - integrated_lufs(out, rate)
        if abs(short) <= LUFS_TOLERANCE or high >= MAX_GAIN_DB:
            break
        # deep in the clip a dB of gain buys well under a LU: step by the local slope
        slope = integrated_lufs(_clip_cheap(samples, high + 1.0), rate) - integrated_lufs(
            _clip_cheap(samples, high), rate
        )
        high = min(high + short / max(slope, MIN_SLOPE), MAX_GAIN_DB)
        out, trim = _clip_clean(samples, rate, high)
    reached = abs(integrated_lufs(out, rate) - target_lufs) <= REACH_TOLERANCE_LU
    clipped = float(np.mean(np.abs(samples * 10 ** (high / 20)) > _ceiling()))
    return out, MasterResult(target_lufs, high + trim, reached, clipped, trim)


def saturate_low(samples: Samples, rate: int, freq: float, drive_db: float) -> Samples:
    """Drive the band under freq through tanh and rejoin it with the rest.

    The driven band is brought back to its level under 100 Hz, so the drive changes the
    808's shape, not how loud the sub is. Its harmonics stay in, which is the point.
    """
    low, high = crossover(samples, rate, freq)
    if drive_db <= 0:
        return (low + high).astype(np.float32)
    before = float(np.mean(low_band(low, rate) ** 2))
    driven = oversampled(lambda v: drive(v, drive_db), low, LOW_OVERSAMPLING)
    after = float(np.mean(low_band(driven, rate) ** 2))
    if before > 0 and after > 0:
        driven = driven * np.float32(math.sqrt(before / after))
    return (driven + high).astype(np.float32)
