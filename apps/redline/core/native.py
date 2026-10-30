"""The Rust inner loops (`apps/redline-dsp`), when they are built.

Every filter is designed here, by the same scipy and pyloudnorm calls the numpy path makes,
and handed to Rust: the two paths share their coefficients and differ only in the loops. A
function returns None where Rust does not apply, and the caller takes the numpy path.
"""

import json
from functools import cache, lru_cache
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pyloudnorm as pyln
from scipy.signal import butter, firwin, sosfilt_zi

try:
    import redline_dsp
except ImportError:  # not built: everything runs on numpy
    redline_dsp = None

Samples = npt.NDArray[np.floating]

# the window scipy's resample_poly designs its filter with, for every oversampled stage
OVERSAMPLING_WINDOW = ("kaiser", 10.0)
BLOCK_SECONDS = 0.4
OVERLAP = 0.75
MAX_CHANNELS = 5


@cache
def _k_weighting(rate: int) -> list[tuple[list[float], list[float], float]]:
    meter = pyln.Meter(rate)
    return [(list(f.b), list(f.a), float(f.passband_gain)) for f in meter._filters.values()]


def integrated_lufs(samples: Samples, rate: int) -> float | None:
    """Integrated loudness as pyloudnorm measures it, or None where pyloudnorm should."""
    if redline_dsp is None:
        return None
    if not np.issubdtype(np.asarray(samples).dtype, np.floating):
        return None
    x = np.asarray(samples, dtype=np.float32)
    x = x[:, None] if x.ndim == 1 else x
    if not 1 <= x.shape[1] <= MAX_CHANNELS or len(x) < BLOCK_SECONDS * rate:
        return None
    return redline_dsp.integrated_loudness(
        np.ascontiguousarray(x), float(rate), _k_weighting(rate), BLOCK_SECONDS, OVERLAP
    )


@lru_cache(maxsize=64)
def _poly_plan(n_in: int, up: int, down: int) -> tuple[list[float], int, int, int, int]:
    # scipy.signal.resample_poly's own filter and alignment, for float32 input
    max_rate = max(up, down)
    half = 10 * max_rate
    window = cast(Any, OVERSAMPLING_WINDOW)
    taps = np.asarray(firwin(2 * half + 1, 1.0 / max_rate, window=window), dtype=np.float32)
    taps *= np.float32(up)
    pre_pad = down - half % down
    skip = (half + pre_pad) // down
    n_out = n_in * up // down + bool(n_in * up % down)
    padded = [0.0] * pre_pad + [float(t) for t in taps]
    return padded, up, down, skip, n_out


@cache
def _band(rate: int, cutoff: float) -> tuple[list[list[float]], list[list[float]], int]:
    sos = np.asarray(butter(8, cutoff, fs=rate, output="sos"))
    # sosfiltfilt's default padding: three times its taps, less trailing zero coefficients
    taps = 2 * len(sos) + 1 - min(int((sos[:, 2] == 0).sum()), int((sos[:, 5] == 0).sum()))
    return sos.tolist(), sosfilt_zi(sos).tolist(), 3 * taps


def oversampled_clip(
    samples: Samples,
    rate: int,
    gain: float,
    ceiling: float,
    factor: int,
    band_hz: float,
    passes: int,
) -> Samples | None:
    """Gain, then at factor times the rate clip and band-limit passes times, clip, come down.

    The numpy path is `clip._clip_clean_numpy`; None where Rust does not apply.
    """
    x = np.ascontiguousarray(samples, dtype=np.float32)
    sos, zi, edge = _band(rate * factor, band_hz)
    if redline_dsp is None or len(x) * factor <= edge:
        return None
    return redline_dsp.oversampled_clip(
        x,
        float(np.float32(gain)),
        float(np.float32(ceiling)),
        _poly_plan(len(x), factor, 1),
        _poly_plan(len(x) * factor, 1, factor),
        sos,
        zi,
        edge,
        passes,
    )


def resample_poly(samples: Samples, up: int, down: int) -> Samples | None:
    """`scipy.signal.resample_poly` of one float32 channel with the oversampling window."""
    if redline_dsp is None:
        return None
    x = np.ascontiguousarray(samples, dtype=np.float32)
    return redline_dsp.resample(x, _poly_plan(len(x), up, down))


def sosfilt(samples: Samples, sos: npt.ArrayLike) -> Samples | None:
    """`scipy.signal.sosfilt` along the samples of every channel, from rest, as float32."""
    if redline_dsp is None:
        return None
    x = np.ascontiguousarray(samples, dtype=np.float32)
    sections = np.asarray(sos, dtype=np.float64)
    # Rust takes every a0 as one, as scipy's sosfilt requires
    if x.ndim != 2 or x.shape[1] == 0 or not np.all(sections[:, 3] == 1.0):
        return None
    return redline_dsp.sosfilt(x, sections.tolist())


def rack_process(
    samples: Samples, rate: int, rack_json: str, side: Samples | None = None
) -> Samples | None:
    """Stereo samples (rows, 2) through a rack of effects given as JSON; None without Rust.

    Args:
        samples: the main signal, (rows, 2).
        rate: the sample rate in Hz.
        rack_json: the rack, `{"effects": [...]}`.
        side: the side signal of the same shape, for a sidechain compressor, a ducker or a
            vocoder's carrier; without it they hear silence.

    Raises:
        ValueError: the rack names an unknown effect or param, a value is out of its range,
            the samples are not stereo, or the side is not the samples' shape.
    """
    if redline_dsp is None:
        return None
    x = np.ascontiguousarray(samples, dtype=np.float32)
    if side is None:
        return redline_dsp.rack_process(x, float(rate), rack_json)
    s = np.ascontiguousarray(side, dtype=np.float32)
    return redline_dsp.rack_process(x, float(rate), rack_json, side=s)


Lane = tuple[int, str, list[tuple[float, float]]]


def rack_process_automated(
    samples: Samples,
    rate: int,
    rack_json: str,
    lanes: list[Lane],
    side: Samples | None = None,
) -> Samples | None:
    """As `rack_process`, with params moving along lanes; None without Rust.

    Args:
        samples: the main signal, (rows, 2).
        rate: the sample rate in Hz.
        rack_json: the rack, `{"effects": [...]}`.
        lanes: (slot, param, [(seconds, value), ...]) for each moving param, points in order
            of time and read linearly between them; seconds count from the first sample.
        side: the side signal of the same shape, as `rack_process` takes it.

    Raises:
        ValueError: as `rack_process`, or a lane names a slot or param the rack lacks, has no
            points, a time that is not finite, points out of order or a value out of range.
    """
    if redline_dsp is None:
        return None
    x = np.ascontiguousarray(samples, dtype=np.float32)
    s = None if side is None else np.ascontiguousarray(side, dtype=np.float32)
    return redline_dsp.rack_process_automated(x, float(rate), rack_json, lanes, s)


def rack_alignment(rack_json: str, rate: int) -> tuple[int, int] | None:
    """The grid a piece of a rack render must start on, and the rack's latency in frames.

    Offline-only effects lay their windows from the first frame, so a piece matches the whole
    only from a multiple of the grid; its output at a frame depends on input up to the
    latency past it. None without Rust.

    Raises:
        ValueError: the rack is wrong, as `rack_process` says, or the rate is out of range.
    """
    if redline_dsp is None:
        return None
    return redline_dsp.rack_alignment(float(rate), rack_json)


def rack_effects() -> list[dict[str, Any]] | None:
    """The rack's effects with their params' ranges, defaults and units; None without Rust."""
    if redline_dsp is None:
        return None
    return json.loads(redline_dsp.rack_effects())
