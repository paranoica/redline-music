"""Stage 3 effects on the whole mix: a crossover and a clipping master."""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, sosfilt

from redline.dsp import hard_clip, oversampled
from redline.loudness import integrated_lufs

Samples = npt.NDArray[np.floating]

CEILING_DB = -0.1
MAX_GAIN_DB = 30.0
OVERSAMPLING = 8
SEARCH_STEPS = 18
CORRECTIONS = 4
MIN_SLOPE = 0.05
LUFS_TOLERANCE = 0.1


@dataclass(frozen=True)
class MasterResult:
    """What the master did: the gain before the clip and whether the target was reached."""

    target_lufs: float
    gain_db: float
    reached: bool
    clipped_share: float


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


def _clip_clean(samples: Samples, gain_db: float) -> Samples:
    gained = samples * np.float32(10 ** (gain_db / 20))
    out = oversampled(lambda v: hard_clip(v, _ceiling()), gained, OVERSAMPLING)
    # the decimation filter rings a few tenths of a dB past the ceiling; turning the whole
    # result down keeps the peak under it without the aliasing a second clip would add
    peak = float(np.abs(out).max())
    return out * np.float32(_ceiling() / peak) if peak > _ceiling() else out


def master_clip(samples: Samples, rate: int, target_lufs: float) -> tuple[Samples, MasterResult]:
    """Raise the mix into a hard clip at CEILING_DB until it reads target_lufs.

    The gain is searched on a base-rate clip (cheap, the loudness differs by hundredths of
    a LU) and the result is rendered once at OVERSAMPLING times the rate.
    """
    loudness = integrated_lufs(samples, rate)
    if not math.isfinite(loudness):
        raise ValueError("nothing to master: the mix is silent")
    plain_gain = target_lufs - loudness
    peak = float(np.abs(samples).max())
    if peak * 10 ** (plain_gain / 20) <= _ceiling():
        out = (samples * np.float32(10 ** (plain_gain / 20))).astype(np.float32)
        return out, MasterResult(target_lufs, plain_gain, True, 0.0)
    low, high = plain_gain, MAX_GAIN_DB
    reached = integrated_lufs(_clip_cheap(samples, high), rate) >= target_lufs
    if reached:
        for _ in range(SEARCH_STEPS):
            middle = (low + high) / 2
            if integrated_lufs(_clip_cheap(samples, middle), rate) < target_lufs:
                low = middle
            else:
                high = middle
    out = _clip_clean(samples, high)
    if reached:
        # the turn-down above costs a few tenths of a LU; push the gain to win them back
        for _ in range(CORRECTIONS):
            short = target_lufs - integrated_lufs(out, rate)
            if abs(short) <= LUFS_TOLERANCE or high >= MAX_GAIN_DB:
                break
            # deep in the clip a dB of gain buys well under a LU: step by the local slope
            slope = integrated_lufs(_clip_cheap(samples, high + 1.0), rate) - integrated_lufs(
                _clip_cheap(samples, high), rate
            )
            high = min(high + short / max(slope, MIN_SLOPE), MAX_GAIN_DB)
            out = _clip_clean(samples, high)
    clipped = float(np.mean(np.abs(samples * 10 ** (high / 20)) > _ceiling()))
    return out, MasterResult(target_lufs, high, reached, clipped)
