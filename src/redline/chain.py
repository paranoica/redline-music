"""The vocal chain: compression, drive and clip at a dirt level, then an EQ fitted to a curve."""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.signal import sosfreqz

from redline.dsp import (
    apply_sos,
    block_levels_db,
    compress,
    drive,
    hard_clip,
    high_shelf,
    low_shelf,
    oversampled,
    peaking,
)
from redline.loudness import integrated_lufs
from redline.spectrum import Spectrum, tonal_curve

Samples = npt.NDArray[np.floating]

PRE_GAIN_LUFS = -18.0
OVERSAMPLING = 8
GATE_BELOW_P95_DB = 30.0
# a vocal stem below 200 Hz is mostly bass and 808 bleeding through the separation
FIT_FROM_HZ = 200.0
GAIN_LIMIT_DB = 12.0
MAX_PASSES = 3
DAMPING = 0.7
PROBE_GAIN_DB = 6.0
EQ_BANDS = (
    ("low_shelf", 200.0),
    ("peak", 400.0),
    ("peak", 1000.0),
    ("peak", 2500.0),
    ("peak", 5000.0),
    ("high_shelf", 8000.0),
)
EQ_Q = 1.0
VOCAL_MIN_LU_BELOW_MIX = 25.0


@dataclass(frozen=True)
class Compressor:
    """Compressor settings of a dirt level."""

    threshold_db: float
    ratio: float
    attack_ms: float
    release_ms: float


@dataclass(frozen=True)
class DirtLevel:
    """One step on the dirt axis: compression, then drive, then a clip below the driven peak."""

    name: str
    compressor: Compressor | None
    drive_db: float
    clip_below_peak_db: float | None


DIRT_LEVELS = (
    DirtLevel("eq", None, 0.0, None),
    DirtLevel("light", Compressor(-18.0, 3.0, 10.0, 100.0), 6.0, None),
    DirtLevel("medium", Compressor(-24.0, 6.0, 5.0, 80.0), 12.0, 3.0),
    DirtLevel("heavy", Compressor(-30.0, 10.0, 2.0, 60.0), 18.0, 6.0),
)


@dataclass(frozen=True)
class Target:
    """The tonal curve the EQ aims at, on the same bands as tonal_curve."""

    centers_hz: tuple[float, ...]
    curve_db: tuple[float, ...]


@dataclass(frozen=True)
class ChainResult:
    """What the chain did to one vocal, for chain.json and the report."""

    level: str
    gate_db: float | None
    clip_level: float | None
    eq_gains_db: tuple[float, ...]
    passes_error_db: tuple[float, ...]


def check_vocal(vocal_lufs: float, mix_lufs: float) -> None:
    """Refuse a vocal stem that is only separation residue."""
    if not math.isfinite(vocal_lufs) or vocal_lufs < mix_lufs - VOCAL_MIN_LU_BELOW_MIX:
        raise ValueError(
            f"no vocal in the track: the vocal stem reads {vocal_lufs:.1f} LUFS "
            f"against {mix_lufs:.1f} LUFS for the mix"
        )


def gate_level_db(samples: Samples, rate: int) -> float:
    """Expander threshold: under the loud part of the vocal, above the residue between phrases."""
    return float(np.percentile(block_levels_db(samples, rate), 95)) - GATE_BELOW_P95_DB


def render_dirt(samples: Samples, rate: int, level: DirtLevel) -> tuple[Samples, float | None]:
    """Compression, drive and clip of one level; returns the signal and the clip level."""
    out = samples
    if level.compressor is not None:
        settings = level.compressor
        before = integrated_lufs(out, rate)
        out = compress(
            out,
            rate,
            settings.threshold_db,
            settings.ratio,
            settings.attack_ms,
            settings.release_ms,
            gate_level_db(out, rate),
        )
        out = out * np.float32(10 ** ((before - integrated_lufs(out, rate)) / 20))
    clip = None
    if level.drive_db > 0:
        if level.clip_below_peak_db is not None:
            peak = float(np.abs(out).max())
            driven_peak = math.tanh(peak * 10 ** (level.drive_db / 20))
            clip = driven_peak * 10 ** (-level.clip_below_peak_db / 20)

        def stage(v: Samples) -> Samples:
            driven = drive(v, level.drive_db)
            return driven if clip is None else hard_clip(driven, clip)

        out = oversampled(stage, out, OVERSAMPLING)
    return out, clip


def _eq_sos(gains_db: npt.ArrayLike, rate: int) -> npt.NDArray[np.float64]:
    sections = []
    for (kind, freq), gain in zip(EQ_BANDS, np.asarray(gains_db, dtype=float), strict=True):
        if kind == "low_shelf":
            sections.append(low_shelf(freq, gain, rate))
        elif kind == "high_shelf":
            sections.append(high_shelf(freq, gain, rate))
        else:
            sections.append(peaking(freq, gain, EQ_Q, rate))
    return np.vstack(sections)


def eq_matrix(
    centers_hz: tuple[float, ...], rate: int
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.bool_]]:
    """Change in each fitted 1/3-octave band per dB of each EQ band's gain, plus an offset column.

    The curves are normalised to their own total, so any gain also shifts every band; the
    offset column takes that shift instead of the shelves.
    """
    centers = np.asarray(centers_hz)
    used = centers >= FIT_FROM_HZ
    columns = []
    for index in range(len(EQ_BANDS)):
        probe = np.zeros(len(EQ_BANDS))
        probe[index] = PROBE_GAIN_DB
        column = []
        for center in centers[used]:
            freqs = np.geomspace(center * 2 ** (-1 / 6), center * 2 ** (1 / 6), 16)
            _, response = sosfreqz(_eq_sos(probe, rate), worN=freqs, fs=rate)
            column.append(10 * np.log10(np.mean(np.abs(response) ** 2)) / PROBE_GAIN_DB)
        columns.append(column)
    columns.append([1.0] * int(used.sum()))
    return np.array(columns).T, used


def solve_gains(diff_db: npt.NDArray[np.float64], matrix: npt.NDArray[np.float64]) -> npt.NDArray:
    """Least-squares EQ gains for a curve difference, offset dropped, no limit."""
    solution, *_ = np.linalg.lstsq(matrix, diff_db, rcond=None)
    return solution[:-1]


def curve_distance(curve: Spectrum, target: Target) -> float:
    """Mean absolute band difference from FIT_FROM_HZ up, after removing the level offset."""
    if tuple(curve.centers_hz) != tuple(target.centers_hz):
        raise ValueError("the curve and the target use different bands")
    centers = np.asarray(curve.centers_hz)
    diff = (np.asarray(target.curve_db) - np.asarray(curve.curve_db))[centers >= FIT_FROM_HZ]
    return float(np.mean(np.abs(diff - diff.mean())))


def _fit_eq(
    dirty: Samples, rate: int, target: Target, upper_hz: float
) -> tuple[Samples, npt.NDArray[np.float64], list[float]]:
    matrix, used = eq_matrix(target.centers_hz, rate)
    gains = np.zeros(len(EQ_BANDS))
    errors = []
    for attempt in range(MAX_PASSES + 1):
        out = apply_sos(dirty, _eq_sos(gains, rate)) if np.any(gains) else dirty
        curve = tonal_curve(out, rate, upper_hz)
        errors.append(curve_distance(curve, target))
        if attempt == MAX_PASSES:
            return out, gains, errors
        # the EQ comes after the nonlinear stages, so the passes only correct the
        # band-averaging error of the linear model and do not oscillate
        diff = np.asarray(target.curve_db) - np.asarray(curve.curve_db)
        step = solve_gains(diff[used], matrix) * (1.0 if attempt == 0 else DAMPING)
        gains = np.clip(gains + step, -GAIN_LIMIT_DB, GAIN_LIMIT_DB)
    raise AssertionError("unreachable")


def process_vocal(
    vocal: Samples, rate: int, level: DirtLevel, target: Target, upper_hz: float
) -> tuple[Samples, ChainResult]:
    """Run the chain at one dirt level; the result keeps the vocal's loudness."""
    loudness = integrated_lufs(vocal, rate)
    if not math.isfinite(loudness):
        raise ValueError("no vocal to process: the vocal stem is silent")
    staged = vocal * np.float32(10 ** ((PRE_GAIN_LUFS - loudness) / 20))
    gate = gate_level_db(staged, rate) if level.compressor is not None else None
    dirty, clip = render_dirt(staged, rate, level)
    out, gains, errors = _fit_eq(dirty, rate, target, upper_hz)
    out = out * np.float32(10 ** ((loudness - integrated_lufs(out, rate)) / 20))
    result = ChainResult(
        level.name,
        gate,
        clip,
        tuple(float(g) for g in gains),
        tuple(errors),
    )
    return out.astype(np.float32), result
