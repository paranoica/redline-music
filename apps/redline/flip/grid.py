"""The bar grid of a type beat in seconds, from its drums and bass stems."""

import math
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
from scipy.fft import next_fast_len
from scipy.signal import butter, hilbert, resample_poly, sosfiltfilt

from redline.takes.rank import beat_grid

Samples = npt.NDArray[np.float32]
Floats = npt.NDArray[np.float64]

ANALYSIS_RATE = 22050
LOW_CUTOFF_HZ = 200.0
ENV_SMOOTH_S = 0.006
ENV_HOP = 64
ONSET_LEVEL = 0.2
BEATS_PER_BAR = 4
STEPS_PER_BEAT = 4
STEPS_PER_BAR = BEATS_PER_BAR * STEPS_PER_BEAT
# a tempo guess is searched this far either side; a given tempo is trusted more
GUESS_SPAN = 0.03
GIVEN_SPAN = 0.005
COARSE_S = 30.0
FINE_SPAN = 0.0005
# the tracker sometimes lands on a third or a quarter off; these rivals are tried against it
RIVALS = (2 / 3, 3 / 4, 4 / 3, 3 / 2)
RIVAL_GAIN = 1.15
# rivals this close in score tie, and the one nearest the tracker's tempo wins
TIE_SHARE = 0.97
MIN_ONSETS = 8
# an onset farther than this from its sixteenth is off the grid and does not steer the fit
FIT_TOLERANCE_S = 0.02
DOWNBEAT_SHARE = 0.8
END_MARGIN_S = 0.01


class GridError(ValueError):
    """The audio gives no grid."""


@dataclass(frozen=True)
class Grid:
    """Where the bars of a file are.

    Attributes:
        bpm: Tempo in beats per minute (four beats to a bar).
        downbeat_s: Start of the first bar in seconds, within the first bar of the file.
        bars: Start of each bar in seconds, up to the file's end.
        duration_s: Length of the file in seconds.
    """

    bpm: float
    downbeat_s: float
    bars: tuple[float, ...]
    duration_s: float

    @property
    def step_s(self) -> float:
        """Length of a sixteenth in seconds."""
        return 60.0 / self.bpm / STEPS_PER_BEAT

    @property
    def bar_s(self) -> float:
        """Length of a bar in seconds."""
        return 60.0 / self.bpm * BEATS_PER_BAR

    def with_downbeat(self, downbeat_s: float) -> "Grid":
        """The same tempo with the bars starting at another time."""
        return replace(
            self,
            downbeat_s=downbeat_s,
            bars=_bar_starts(downbeat_s, self.bar_s, self.duration_s),
        )


def _bar_starts(downbeat_s: float, bar_s: float, duration_s: float) -> tuple[float, ...]:
    count = max(0, math.ceil((duration_s - END_MARGIN_S - downbeat_s) / bar_s))
    return tuple(downbeat_s + i * bar_s for i in range(count))


def resampled(mono: Samples, rate: int) -> Floats:
    """The signal as float64 at the analysis rate."""
    x = np.asarray(mono, dtype=np.float64)
    if rate == ANALYSIS_RATE:
        return x
    g = math.gcd(rate, ANALYSIS_RATE)
    return resample_poly(x, ANALYSIS_RATE // g, rate // g)


def low_band(mono: Samples, rate: int, cutoff: float = LOW_CUTOFF_HZ) -> Floats:
    """The signal low-passed at the cutoff, at the analysis rate."""
    sos = butter(4, cutoff, btype="low", fs=ANALYSIS_RATE, output="sos")
    return sosfiltfilt(sos, resampled(mono, rate))


def envelope(low: Floats, smooth_s: float = ENV_SMOOTH_S) -> Floats:
    """Amplitude envelope of a low band, smoothed, one value per ENV_HOP samples."""
    if low.size == 0:
        return np.zeros(0)
    size = next_fast_len(low.size)
    amplitude = np.abs(hilbert(low, size))[: low.size]
    width = max(1, int(smooth_s * ANALYSIS_RATE))
    kernel = np.ones(width) / width
    padded = np.pad(amplitude, (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, kernel, mode="valid")[::ENV_HOP]


def attacks(env: Floats, floor: float = 0.1) -> list[tuple[float, float, int]]:
    """The starts of the sharp rises of an envelope.

    Args:
        env: Envelope from `envelope`.
        floor: Smallest rise, as a share of the envelope's peak.

    Returns:
        Per attack its time in seconds, its strength (the rise) and the frame of the peak.
    """
    if env.size < 4:
        return []
    ref = float(env.max())
    if ref <= 0:
        return []
    ahead = 6
    gap = 8
    rise = np.zeros(env.size)
    rise[:-ahead] = np.maximum(0.0, env[ahead:] - env[:-ahead])
    found: list[tuple[float, float, int]] = []
    i = 0
    while i < env.size - 1:
        if rise[i] < floor * ref:
            i += 1
            continue
        top = int(np.argmax(env[i : i + ahead + 4])) + i
        base_at = max(0, i - gap) + int(np.argmin(env[max(0, i - gap) : i + 1]))
        base = float(env[base_at])
        level = base + ONSET_LEVEL * (float(env[top]) - base)
        j = base_at
        while j < top and env[j] < level:
            j += 1
        frac = 0.0
        if j > 0 and env[j] > env[j - 1]:
            frac = (level - env[j - 1]) / (env[j] - env[j - 1])
        t = (j - 1 + frac) * ENV_HOP / ANALYSIS_RATE if j > 0 else 0.0
        found.append((max(0.0, t), float(env[top]) - base, top))
        i = top + gap
    return found


def _coherence(times: Floats, weights: Floats, periods: Floats) -> Floats:
    out = np.empty(periods.size)
    for lo in range(0, periods.size, 256):
        p = periods[lo : lo + 256, None]
        out[lo : lo + 256] = np.abs((weights * np.exp(2j * np.pi * times / p)).sum(axis=1))
    return out


def _coarse(times: Floats, weights: Floats, guess: float, span: float) -> tuple[float, float]:
    cut = times < COARSE_S
    if cut.sum() < MIN_ONSETS or cut.all():
        cut = np.ones(times.size, dtype=bool)
    periods = guess * np.linspace(1 - span, 1 + span, int(span * 2 / 0.00005) + 1)
    score = _coherence(times[cut], weights[cut], periods)
    best = int(np.argmax(score))
    return float(periods[best]), float(score[best] / weights[cut].sum())


def _scan_period(times: Floats, weights: Floats, guess: float) -> float:
    fine = guess * np.linspace(1 - FINE_SPAN, 1 + FINE_SPAN, 401)
    return float(fine[np.argmax(_coherence(times, weights, fine))])


def _fit(times: Floats, weights: Floats, period: float) -> tuple[float, float, Floats]:
    phase = np.angle((weights * np.exp(-2j * np.pi * times / period)).sum())
    offset = float(-phase / (2 * np.pi) * period)
    slope = period
    index = np.round((times - offset) / slope)
    for _ in range(3):
        residual = times - (offset + slope * index)
        keep = np.abs(residual) < FIT_TOLERANCE_S
        if keep.sum() < 2:
            break
        slope, offset = np.polyfit(index[keep], times[keep], 1, w=np.sqrt(weights[keep]))
        index = np.round((times - offset) / slope)
    return float(offset), float(slope), index


def _downbeat(times: Floats, weights: Floats, index: Floats, offset: float, slope: float) -> float:
    on_grid = np.abs(times - (offset + slope * index)) < FIT_TOLERANCE_S
    t, w, k = times[on_grid], weights[on_grid], index[on_grid].astype(int) % STEPS_PER_BAR
    share = np.bincount(k, weights=w, minlength=STEPS_PER_BAR)
    strong = np.flatnonzero(share >= DOWNBEAT_SHARE * share.max())
    first = {int(d): float(t[k == d].min()) for d in strong if (k == d).any()}
    best = min(first, key=lambda d: first[d])
    return offset + slope * best


def _inside_file(downbeat: float, step_s: float) -> float:
    """The first downbeat inside the file; a bar line well before its start moves on a bar.

    Within the fit's own tolerance below 0 the file starts on the bar line, and loose timing
    put the fitted line a little early.
    """
    if downbeat < -FIT_TOLERANCE_S:
        return downbeat + step_s * STEPS_PER_BAR
    return max(0.0, downbeat)


def find_grid(
    drums_mono: Samples,
    bass_mono: Samples,
    rate: int,
    bpm: float | None = None,
    half: bool = False,
    double: bool = False,
) -> Grid:
    """The tempo and bar lines of a beat from its drums and bass stems.

    The tempo is the tracker's unless given, then refined so that the low-band onsets of the
    whole file sit on a straight line of sixteenths; the first downbeat is the position in the
    bar where the drums' low band hits hardest, the earliest of equals.

    Args:
        drums_mono: Mono drums stem.
        bass_mono: Mono bass stem, used when the drums have no low band.
        rate: Sample rate of both.
        bpm: Tempo to use instead of the tracker's.
        half: Halve the tempo found.
        double: Double the tempo found.

    Returns:
        The grid; the caller nudges the downbeat with `with_downbeat`.

    Raises:
        GridError: With no tempo or too few onsets to place a grid.
    """
    if half and double:
        raise ValueError("half and double cancel each other")
    duration = max(drums_mono.size, bass_mono.size) / rate
    guess = bpm
    span = GIVEN_SPAN
    if guess is None:
        source = drums_mono if np.any(drums_mono) else bass_mono
        guess = beat_grid(source, rate).bpm
        span = GUESS_SPAN
        if guess is None:
            raise GridError("no tempo found")
        guess *= 0.5 if half else 2.0 if double else 1.0
    if guess <= 0:
        raise GridError("the tempo must be positive")

    found = attacks(envelope(low_band(drums_mono, rate)))
    if len(found) < MIN_ONSETS:
        found = attacks(envelope(low_band(bass_mono, rate)))
    if len(found) < MIN_ONSETS:
        raise GridError("too few low-band onsets to place a grid")
    times = np.array([f[0] for f in found])
    weights = np.array([f[1] for f in found])

    period, score = _coarse(times, weights, 60.0 / guess / STEPS_PER_BEAT, span)
    if bpm is None:
        rivals = [_coarse(times, weights, 60.0 / guess / r / STEPS_PER_BEAT, span) for r in RIVALS]
        best = max(score_ for _, score_ in rivals)
        if best > RIVAL_GAIN * score:
            target = 60.0 / guess / STEPS_PER_BEAT
            tied = [p for p, sc in rivals if sc >= TIE_SHARE * best]
            period = min(tied, key=lambda p: abs(math.log(p / target)))
    period = _scan_period(times, weights, period)
    offset, slope, index = _fit(times, weights, period)
    downbeat = _inside_file(_downbeat(times, weights, index, offset, slope), slope)
    tempo = 60.0 / slope / STEPS_PER_BEAT
    grid = Grid(tempo, downbeat, (), duration)
    return grid.with_downbeat(downbeat)
