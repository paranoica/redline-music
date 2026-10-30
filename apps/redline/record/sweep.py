"""The round trip of the singer's path, measured with an exponential sine sweep.

The sweep is played through the path (a headphone cup held to the mic, or a loopback cable)
and recorded on the engine's own frame counter. Convolving the recording with the sweep's
inverse filter (Farina's: the sweep reversed, its low end turned down 6 dB an octave) leaves
the path's impulse response; its peak sits at the round trip. Harmonic distortion lands
before that peak, so a speaker's distortion does not move it.
"""

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.signal import fftconvolve

SWEEP_SECONDS = 2.0
SWEEP_LOW_HZ = 20.0
SWEEP_HIGH_SHARE = 0.45
SWEEP_DB = -18.0
FADE_S = 0.01
# no singer's path takes this long; a longer lag is something else
MAX_ROUND_TRIP_S = 0.5
# the response's peak over its median; noise alone reaches about 23 dB by chance
MIN_PEAK_TO_NOISE_DB = 30.0
# the peak's own skirt is left out of the noise estimate
GUARD_S = 0.005

Mono = npt.NDArray[np.floating]


class CalibrationError(Exception):
    """The recording does not show the sweep clearly enough to trust a round trip."""


@dataclass(frozen=True)
class Sweep:
    """An exponential sine sweep, mono float32, and its band."""

    samples: npt.NDArray[np.float32]
    rate: int
    low_hz: float
    high_hz: float


@dataclass(frozen=True)
class Delay:
    """The round trip in frames and how clearly the sweep stood out to find it."""

    frames: int
    peak_to_noise_db: float


def exp_sweep(rate: int, seconds: float = SWEEP_SECONDS, level_db: float = SWEEP_DB) -> Sweep:
    """An exponential sweep from SWEEP_LOW_HZ to SWEEP_HIGH_SHARE of the rate, faded at the ends.

    Args:
        rate: Sample rate in Hz.
        seconds: Length of the sweep.
        level_db: Its peak level in dBFS.
    """
    low, high = SWEEP_LOW_HZ, SWEEP_HIGH_SHARE * rate
    n = round(seconds * rate)
    t = np.arange(n) / rate
    span = seconds / math.log(high / low)
    samples = np.sin(2 * np.pi * low * span * (np.exp(t / span) - 1))
    fade = min(round(FADE_S * rate), n // 2)
    ramp = 0.5 - 0.5 * np.cos(np.pi * np.arange(fade) / fade)
    samples[:fade] *= ramp
    samples[n - fade :] *= ramp[::-1]
    gain = 10 ** (level_db / 20)
    return Sweep((gain * samples).astype(np.float32), rate, low, high)


def _inverse(sweep: Sweep) -> npt.NDArray[np.float64]:
    n = len(sweep.samples)
    span = (n / sweep.rate) / math.log(sweep.high_hz / sweep.low_hz)
    inverse = sweep.samples[::-1].astype(np.float64) * np.exp(-np.arange(n) / sweep.rate / span)
    # scaled so the sweep itself comes out as a unit peak
    return inverse / np.abs(fftconvolve(sweep.samples, inverse)).max()


def find_delay(recorded: Mono, sweep: Sweep, max_frames: int | None = None) -> Delay:
    """The lag, in frames, at which the sweep comes back in the recording.

    Args:
        recorded: The mono recording, frame 0 at the sweep's first frame played.
        sweep: The sweep that was played.
        max_frames: The longest round trip searched for; MAX_ROUND_TRIP_S by default.

    Raises:
        CalibrationError: The recording is shorter than the sweep, or the response's peak
            stands less than MIN_PEAK_TO_NOISE_DB over its noise.
    """
    recorded = np.asarray(recorded, dtype=np.float64)
    n = len(sweep.samples)
    if len(recorded) < n:
        raise CalibrationError(
            f"the recording ({len(recorded)} frames) is shorter than the sweep ({n})"
        )
    if max_frames is None:
        max_frames = round(MAX_ROUND_TRIP_S * sweep.rate)
    response = np.abs(fftconvolve(recorded, _inverse(sweep)))
    # lag 0 sits at n - 1
    window = response[n - 1 : n + min(max_frames, len(recorded) - 1)]
    lag = int(np.argmax(window))
    peak = float(window[lag])
    guard = round(GUARD_S * sweep.rate)
    centre = n - 1 + lag
    rest = np.concatenate([response[: max(centre - guard, 0)], response[centre + guard + 1 :]])
    # the median of the magnitude: a few distortion peaks do not raise it
    noise = float(np.median(rest)) if rest.size else 0.0
    if peak <= 0:
        raise CalibrationError("the recording is silent: is the mic connected and the sweep heard?")
    ratio_db = math.inf if noise <= 0 else 20 * math.log10(peak / noise)
    if ratio_db < MIN_PEAK_TO_NOISE_DB:
        raise CalibrationError(
            f"the sweep's peak-to-noise is {ratio_db:.1f} dB, under {MIN_PEAK_TO_NOISE_DB:g} dB: "
            "hold the headphone cup closer to the mic, raise the volume a little, or use a "
            "loopback cable"
        )
    return Delay(lag, ratio_db)
