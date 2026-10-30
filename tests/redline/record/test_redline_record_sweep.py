import numpy as np
import pytest
from redline.record.sweep import (
    MIN_PEAK_TO_NOISE_DB,
    CalibrationError,
    exp_sweep,
    find_delay,
)
from redline_signals import make_rng
from scipy.signal import butter, sosfilt

RATE = 48000


def played(sweep: np.ndarray, delay: int, tail: int = RATE // 2) -> np.ndarray:
    """What the mic hears: the sweep `delay` frames late, then silence."""
    out = np.zeros(delay + len(sweep) + tail)
    out[delay : delay + len(sweep)] = sweep
    return out


@pytest.fixture(scope="module")
def sweep():
    return exp_sweep(RATE)


def test_the_sweep_covers_its_band_and_stays_under_full_scale(sweep):
    assert sweep.rate == RATE
    assert sweep.low_hz < 50 and sweep.high_hz >= 0.4 * RATE
    assert 0.05 < np.abs(sweep.samples).max() < 0.5
    # starts and ends quiet, so it does not click
    assert abs(sweep.samples[0]) < 1e-3 and abs(sweep.samples[-1]) < 1e-3


@pytest.mark.parametrize("delay", [0, 1, 517, 4801])
def test_a_clean_delay_is_found_to_the_frame(sweep, delay):
    found = find_delay(played(sweep.samples, delay), sweep)
    assert found.frames == delay
    assert found.peak_to_noise_db > MIN_PEAK_TO_NOISE_DB


def test_a_delay_through_a_band_limited_noisy_path_is_found_within_a_frame(sweep):
    rng = make_rng()
    delay = 517
    # a headphone cup held to a mic: band limited, 20 dB down, noise as loud as the sweep
    path = butter(2, [80, 10000], btype="bandpass", fs=RATE, output="sos")
    heard = 0.1 * sosfilt(path, played(sweep.samples, delay))
    heard += rng.standard_normal(len(heard)) * np.sqrt(np.mean(heard**2))
    found = find_delay(heard, sweep)
    assert abs(found.frames - delay) <= 1
    assert found.peak_to_noise_db > MIN_PEAK_TO_NOISE_DB


def test_noise_alone_is_refused(sweep):
    noise = make_rng().standard_normal(len(sweep.samples) + RATE) * 0.1
    with pytest.raises(CalibrationError, match="peak-to-noise"):
        find_delay(noise, sweep)


def test_silence_is_refused(sweep):
    with pytest.raises(CalibrationError):
        find_delay(np.zeros(len(sweep.samples) + RATE), sweep)


def test_a_round_trip_past_the_search_window_is_refused_not_misread(sweep):
    rng = make_rng()
    window = RATE // 10
    heard = played(sweep.samples, window + 2000)
    heard += 1e-3 * rng.standard_normal(len(heard))
    assert find_delay(played(sweep.samples, window), sweep, max_frames=window).frames == window
    with pytest.raises(CalibrationError):
        find_delay(heard, sweep, max_frames=window)


def test_a_recording_shorter_than_the_sweep_is_refused(sweep):
    with pytest.raises(CalibrationError, match="shorter"):
        find_delay(sweep.samples[: len(sweep.samples) // 2], sweep)
