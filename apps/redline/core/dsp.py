"""Effects for the vocal chain: RBJ biquads, a stereo-linked compressor, drive, clip."""

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import numpy.typing as npt
from scipy.signal import resample_poly, sosfilt

Samples = npt.NDArray[np.floating]
Sos = npt.NDArray[np.float64]

BLOCK_SECONDS = 0.001
LEVEL_FLOOR = 1e-6
EXPANDER_RANGE_DB = 40.0
OVERSAMPLING_WINDOW = ("kaiser", 10.0)


def _sos(b: list[float], a: list[float]) -> Sos:
    return np.array([[*(v / a[0] for v in b), 1.0, a[1] / a[0], a[2] / a[0]]])


def peaking(freq: float, gain_db: float, q: float, rate: int) -> Sos:
    """Peaking EQ from the RBJ audio EQ cookbook."""
    amp = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * freq / rate
    alpha = math.sin(w0) / (2 * q)
    cos = math.cos(w0)
    return _sos(
        [1 + alpha * amp, -2 * cos, 1 - alpha * amp],
        [1 + alpha / amp, -2 * cos, 1 - alpha / amp],
    )


def _shelf(freq: float, gain_db: float, rate: int, high: bool) -> Sos:
    amp = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * freq / rate
    # the high shelf mirrors the low one: cos(w0) changes sign, so do b1 and a1
    cos = -math.cos(w0) if high else math.cos(w0)
    root = math.sqrt(2 * amp) * math.sin(w0)
    b = [
        amp * ((amp + 1) - (amp - 1) * cos + root),
        2 * amp * ((amp - 1) - (amp + 1) * cos),
        amp * ((amp + 1) - (amp - 1) * cos - root),
    ]
    a = [
        (amp + 1) + (amp - 1) * cos + root,
        -2 * ((amp - 1) + (amp + 1) * cos),
        (amp + 1) + (amp - 1) * cos - root,
    ]
    if high:
        b[1], a[1] = -b[1], -a[1]
    return _sos(b, a)


def low_shelf(freq: float, gain_db: float, rate: int) -> Sos:
    """Low shelf from the RBJ audio EQ cookbook, slope 1."""
    return _shelf(freq, gain_db, rate, high=False)


def high_shelf(freq: float, gain_db: float, rate: int) -> Sos:
    """High shelf from the RBJ audio EQ cookbook, slope 1."""
    return _shelf(freq, gain_db, rate, high=True)


def apply_sos(samples: Samples, sos: Sos) -> Samples:
    """Filter every channel."""
    return np.asarray(sosfilt(sos, samples, axis=0), dtype=np.float32)


def block_levels_db(samples: Samples, rate: int) -> npt.NDArray[np.float64]:
    """Peak level of the loudest channel in each 1 ms block, in dBFS."""
    block = max(1, round(rate * BLOCK_SECONDS))
    count = math.ceil(len(samples) / block)
    padded = np.zeros((count * block, samples.shape[1]), dtype=np.float32)
    padded[: len(samples)] = np.abs(samples)
    peaks = padded.reshape(count, block * samples.shape[1]).max(axis=1)
    return 20 * np.log10(np.maximum(peaks, LEVEL_FLOOR))


def compressor_gain_db(
    samples: Samples,
    rate: int,
    threshold_db: float,
    ratio: float,
    attack_ms: float,
    release_ms: float,
    gate_db: float,
) -> npt.NDArray[np.float64]:
    """Per-sample gain of a compressor above threshold and a 1:2 expander below the gate.

    The expander turns a gap down by EXPANDER_RANGE_DB at most. One detector for all
    channels (the loudest one per 1 ms block), so the stereo image does not move.
    """
    block = max(1, round(rate * BLOCK_SECONDS))
    blocks = block_levels_db(samples, rate)
    # the median of five neighbouring blocks: a click within two blocks (any click up to
    # 1 ms) does not reach the gain, a phrase start does
    padded = np.concatenate([blocks[:1], blocks[:1], blocks, blocks[-1:], blocks[-1:]])
    level = np.median(np.stack([padded[i : i + len(blocks)] for i in range(5)]), axis=0)
    count = len(level)
    squash = np.where(level > threshold_db, (threshold_db - level) * (1 - 1 / ratio), 0.0)
    expand = np.maximum(np.where(level < gate_db, level - gate_db, 0.0), -EXPANDER_RANGE_DB)
    attack = math.exp(-block / rate / (attack_ms / 1000))
    release = math.exp(-block / rate / (release_ms / 1000))
    smoothed = np.empty(count)
    squash_gain = expand_gain = 0.0
    # the compressor clamps down at attack speed; the expander opens at attack speed, so
    # the first syllable after a pause is not swallowed
    for i, (aim_squash, aim_expand) in enumerate(
        zip(squash.tolist(), expand.tolist(), strict=True)
    ):
        coeff = attack if aim_squash < squash_gain else release
        squash_gain = coeff * squash_gain + (1 - coeff) * aim_squash
        coeff = attack if aim_expand > expand_gain else release
        expand_gain = coeff * expand_gain + (1 - coeff) * aim_expand
        smoothed[i] = squash_gain + expand_gain
    centers = np.arange(count) * block + (block - 1) / 2
    return np.interp(np.arange(len(samples)), centers, smoothed)


def compress(
    samples: Samples,
    rate: int,
    threshold_db: float,
    ratio: float,
    attack_ms: float,
    release_ms: float,
    gate_db: float,
) -> Samples:
    """Apply compressor_gain_db to every channel."""
    gain = compressor_gain_db(samples, rate, threshold_db, ratio, attack_ms, release_ms, gate_db)
    return (samples * 10 ** (gain[:, None] / 20)).astype(np.float32)


def drive(samples: Samples, drive_db: float) -> Samples:
    """Saturate with tanh after drive_db of gain."""
    return np.tanh(samples * 10 ** (drive_db / 20)).astype(np.float32)


def hard_clip(samples: Samples, level: float) -> Samples:
    """Clip at +-level."""
    return np.clip(samples, -level, level).astype(np.float32)


def oversampled(stage: Callable[[Samples], Samples], samples: Samples, factor: int) -> Samples:
    """Run a nonlinear stage at factor times the rate, every channel on its own thread.

    The filters and resampling release the GIL, and each channel is computed alone, so the
    result is the same as one channel after the other.
    """

    def one(channel: int) -> Samples:
        up = resample_poly(samples[:, channel], factor, 1, window=OVERSAMPLING_WINDOW)
        out = stage(up[:, None].astype(np.float32))[:, 0]
        return resample_poly(out, 1, factor, window=OVERSAMPLING_WINDOW)

    with ThreadPoolExecutor(max_workers=samples.shape[1]) as pool:
        channels = list(pool.map(one, range(samples.shape[1])))
    return np.column_stack(channels)[: len(samples)].astype(np.float32)
