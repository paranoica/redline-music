import math

import numpy as np
import pytest
from redline_signals import lowpass_brickwall, make_rng, tone

from redline.spectrum import SIDE_CEILING_DB, SIDE_FLOOR_DB, low_end, stereo_width, tonal_curve

RATE = 44100


def pink(n: int, rng: np.random.Generator) -> np.ndarray:
    """Noise with power density proportional to 1/f."""
    spec = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    spec[1:] /= np.sqrt(freqs[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n=n)
    return x / np.abs(x).max() * 0.5


def stereo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.column_stack([left, right]).astype(np.float32)


def test_white_noise_rises_three_db_per_octave():
    # band energy of white noise grows with bandwidth, and 1/3-octave bandwidth is ∝ f:
    # +10·log10(2) = 3.01 dB per octave
    x = make_rng().standard_normal(RATE * 30) * 0.1
    assert tonal_curve(stereo(x, x), RATE, 20000).tilt_db_per_oct == pytest.approx(3.01, abs=0.3)


def test_pink_noise_is_flat_per_octave():
    x = pink(RATE * 30, make_rng())
    assert tonal_curve(stereo(x, x), RATE, 20000).tilt_db_per_oct == pytest.approx(0.0, abs=0.3)


def test_curve_is_normalised_so_level_does_not_matter():
    x = make_rng().standard_normal(RATE * 10) * 0.1
    loud = tonal_curve(stereo(x, x), RATE, 20000)
    quiet = tonal_curve(stereo(x * 0.01, x * 0.01), RATE, 20000)
    total = 10 * math.log10(sum(10 ** (c / 10) for c in loud.curve_db))
    assert total == pytest.approx(0.0, abs=1e-6)
    assert np.allclose(loud.curve_db, quiet.curve_db, atol=1e-6)


def test_curve_stops_at_the_upper_limit():
    x = lowpass_brickwall(make_rng().standard_normal((RATE * 10, 2)) * 0.1, RATE, 16000)
    curve = tonal_curve(x, RATE, 16000)
    assert curve.centers_hz[0] >= 40
    assert max(c * 2 ** (1 / 6) for c in curve.centers_hz) <= 16000


def test_silence_has_no_curve():
    curve = tonal_curve(np.zeros((RATE * 5, 2), dtype=np.float32), RATE, 20000)
    assert curve.curve_db == ()
    assert curve.tilt_db_per_oct is None


def test_identical_channels_are_mono():
    x = make_rng().standard_normal(RATE * 20) * 0.1
    width = stereo_width(stereo(x, x), RATE, 20000)
    assert width.correlation == pytest.approx(1.0, abs=1e-6)
    assert width.side_mid_db == (SIDE_FLOOR_DB, SIDE_FLOOR_DB, SIDE_FLOOR_DB)


def test_independent_channels_have_equal_side_and_mid():
    rng = make_rng()
    width = stereo_width(
        stereo(rng.standard_normal(RATE * 20) * 0.1, rng.standard_normal(RATE * 20) * 0.1),
        RATE,
        20000,
    )
    low, mid, high = width.side_mid_db
    assert width.correlation == pytest.approx(0.0, abs=0.05)
    assert low == pytest.approx(0.0, abs=1.0)
    assert mid == pytest.approx(0.0, abs=0.5)
    assert high == pytest.approx(0.0, abs=0.5)


def test_opposite_channels_are_all_side():
    x = make_rng().standard_normal(RATE * 20) * 0.1
    width = stereo_width(stereo(x, -x), RATE, 20000)
    assert width.correlation == pytest.approx(-1.0, abs=1e-6)
    assert width.side_mid_db == (SIDE_CEILING_DB, SIDE_CEILING_DB, SIDE_CEILING_DB)


def test_silence_has_no_width():
    width = stereo_width(np.zeros((RATE * 5, 2), dtype=np.float32), RATE, 20000)
    assert width.correlation is None
    assert width.side_mid_db == (None, None, None)


def test_low_end_share_of_equal_low_and_mid_sines_is_half():
    x = tone(50, 10, amp=0.3) + tone(1000, 10, amp=0.3)
    result = low_end(x, RATE)
    assert result.share_db == pytest.approx(10 * math.log10(0.5), abs=0.2)
    assert result.crest_db == pytest.approx(10 * math.log10(2), abs=0.2)


def test_no_low_end_in_a_mid_sine():
    result = low_end(tone(1000, 10, amp=0.3), RATE)
    assert result.share_db < -40


def test_silence_has_no_low_end():
    result = low_end(np.zeros((RATE * 5, 2), dtype=np.float32), RATE)
    assert result.share_db is None
    assert result.crest_db is None


def test_high_stereo_band_stops_at_the_side_band_limit():
    # independent mid and side, side cut at 8 kHz like a joint-stereo codec:
    # measured up to 8 kHz the high band is balanced, past it mid alone would dominate
    rng = make_rng()
    n = RATE * 20
    mid = rng.standard_normal(n) * 0.1
    side = lowpass_brickwall(rng.standard_normal((n, 1)) * 0.1, RATE, 8000)[:, 0]
    width = stereo_width(stereo(mid + side, mid - side), RATE, 8000)
    assert width.side_mid_db[2] == pytest.approx(0.0, abs=0.5)
