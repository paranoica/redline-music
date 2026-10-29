import numpy as np
import pyloudnorm as pyln
import pytest
from redline.core import native
from redline_signals import make_rng, tone

RATE = 44100
pytest.importorskip("redline_dsp")


def noise(seconds, rate=RATE, amp=0.3, channels=2):
    rng = make_rng()
    return (amp * rng.standard_normal((int(seconds * rate), channels))).astype(np.float32)


@pytest.mark.parametrize(
    "signal",
    [
        lambda: noise(5.0),
        lambda: noise(5.0, rate=48000),
        lambda: tone(1000.0, 3.0, amp=0.5),
        lambda: np.concatenate([noise(3.0), np.zeros((RATE * 4, 2), np.float32)]),
        lambda: noise(0.41),
        lambda: noise(4.0, channels=1),
    ],
)
def test_loudness_matches_pyloudnorm(signal):
    x = signal()
    rate = 48000 if len(x) == 5 * 48000 else RATE
    want = pyln.Meter(rate).integrated_loudness(x)
    assert native.integrated_lufs(x, rate) == pytest.approx(want, abs=1e-5)


def test_loudness_of_blocks_all_under_the_gate_is_minus_infinity():
    x = noise(2.0, amp=1e-6)
    assert native.integrated_lufs(x, RATE) == -np.inf
    assert pyln.Meter(RATE).integrated_loudness(x) == -np.inf


def test_too_short_or_too_wide_is_left_to_pyloudnorm():
    assert native.integrated_lufs(noise(0.2), RATE) is None
    assert native.integrated_lufs(noise(1.0, channels=6), RATE) is None


def test_the_oversampled_clip_matches_the_numpy_path():
    from redline.master import clip

    x = noise(3.0, amp=0.4)
    want, want_trim = clip._clip_clean_numpy(x, RATE, 9.0)
    got, got_trim = clip._clip_clean(x, RATE, 9.0)
    assert got.shape == want.shape and got.dtype == np.float32
    assert np.abs(got - want).max() < 2e-6
    assert got_trim == pytest.approx(want_trim, abs=1e-5)
    assert np.abs(got).max() <= clip._ceiling() + 1e-7


def test_the_clip_is_used_only_where_it_can_filter():
    # three samples are 24 at the raised rate, under the filter's 27 of padding
    assert native.oversampled_clip(noise(3 / RATE), RATE, 1.0, 0.98, 8, 20000.0, 2) is None
    assert native.oversampled_clip(noise(4 / RATE), RATE, 1.0, 0.98, 8, 20000.0, 2) is not None


@pytest.mark.parametrize(("up", "down", "n"), [(8, 1, 1000), (1, 8, 8003), (2, 1, 777), (1, 2, 64)])
def test_resampling_matches_scipy(up, down, n):
    from scipy.signal import resample_poly

    x = noise(n / RATE, channels=1)[:, 0]
    want = resample_poly(x, up, down, window=native.OVERSAMPLING_WINDOW)
    got = native.resample_poly(x, up, down)
    assert got.shape == want.shape and got.dtype == np.float32
    assert np.abs(got - want).max() < 1e-6


def test_cascaded_sections_match_scipy():
    from redline.core.dsp import high_shelf, peaking
    from scipy.signal import sosfilt

    sos = np.vstack([peaking(1000, 6.0, 1.0, RATE), high_shelf(8000, -4.0, RATE)])
    # a long silent tail: the filter's state decays through numbers too small for floats
    x = np.concatenate([noise(2.0), np.zeros((RATE * 30, 2), np.float32)])
    want = sosfilt(sos, x, axis=0)
    got = native.sosfilt(x, sos)
    assert got.dtype == np.float32
    assert np.abs(got - want).max() < 1e-6
