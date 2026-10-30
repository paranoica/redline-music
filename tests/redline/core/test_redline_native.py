import json

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


def test_without_rust_every_path_is_scipy_and_pyloudnorm_exactly(monkeypatch):
    from redline.core import dsp, loudness
    from scipy.signal import resample_poly, sosfilt

    monkeypatch.setattr(native, "redline_dsp", None)
    x = noise(2.0)
    assert loudness.integrated_lufs(x, RATE) == pyln.Meter(RATE).integrated_loudness(x)
    sos = np.vstack([dsp.peaking(1000, 6.0, 1.0, RATE)])
    assert np.array_equal(dsp.apply_sos(x, sos), sosfilt(sos, x, axis=0).astype(np.float32))
    out = dsp.oversampled(lambda v: v, x, 2)
    want = [
        resample_poly(
            resample_poly(x[:, c], 2, 1, window=native.OVERSAMPLING_WINDOW).astype(np.float32),
            1,
            2,
            window=native.OVERSAMPLING_WINDOW,
        )
        for c in range(2)
    ]
    assert np.array_equal(out, np.column_stack(want)[: len(x)].astype(np.float32))


def test_inputs_scipy_would_treat_otherwise_are_left_to_it():
    assert native.integrated_lufs((noise(1.0) * 1000).astype(np.int16), RATE) is None
    assert native.integrated_lufs(np.zeros((RATE, 0), np.float32), RATE) is None
    unnormalised = np.array([[1.0, 0.0, 0.0, 2.0, 0.0, 0.0]])
    assert native.sosfilt(noise(0.5), unnormalised) is None
    assert native.sosfilt(np.zeros((100, 0), np.float32), unnormalised[:, :] / 2) is None


def test_a_rejected_plan_raises_and_leaves_the_threads_float_mode_alone():
    import redline_dsp

    with pytest.raises(ValueError):
        redline_dsp.resample(noise(0.01, channels=1)[:, 0], ([], 1, 1, 0, 5))
    with pytest.raises(ValueError):
        redline_dsp.resample(noise(0.01, channels=1)[:, 0], ([1.0], 0, 1, 0, 5))
    tiny = np.float32(1e-39)
    assert tiny * np.float32(1.0) != 0


def _rack(*effects):
    return json.dumps({"effects": list(effects)})


def test_a_gain_rack_of_minus_six_db_halves_the_samples():
    x = noise(0.5)
    got = native.rack_process(x, RATE, _rack({"effect": "gain", "on": True, "params": {"db": -6}}))
    assert got.shape == x.shape and got.dtype == np.float32
    assert np.abs(got - x * 10 ** (-6 / 20)).max() < 1e-6
    loud = np.abs(x) > 0.01
    assert np.abs(got[loud] / x[loud] - 0.5).max() < 0.002


def test_an_effect_turned_off_and_an_empty_rack_leave_the_samples():
    x = noise(0.2)
    off = _rack({"effect": "gain", "on": False, "params": {"db": -6}})
    assert np.array_equal(native.rack_process(x, RATE, off), x)
    assert np.array_equal(native.rack_process(x, RATE, _rack()), x)
    assert native.rack_process(x[:0], RATE, _rack()).shape == (0, 2)


@pytest.mark.parametrize(
    ("rack", "named"),
    [
        (_rack({"effect": "flanger9"}), "flanger9"),
        (_rack({"effect": "gain", "params": {"dbb": 1}}), "dbb"),
        (_rack({"effect": "gain", "params": {"db": 100}}), "100"),
        ("{not json", "rack"),
    ],
)
def test_a_bad_rack_raises_naming_what_is_wrong(rack, named):
    with pytest.raises(ValueError, match=named):
        native.rack_process(noise(0.1), RATE, rack)


def test_the_rack_takes_stereo_only():
    with pytest.raises(ValueError, match="stereo"):
        native.rack_process(noise(0.1, channels=1), RATE, _rack())


def test_rack_effects_lists_the_effects_with_ranges():
    effects = {e["effect"]: e for e in native.rack_effects()}
    assert list(effects) == ["gain", "pan", "width", "eq", "filter", "saturate", "bitcrush"]
    db = effects["gain"]["params"][0]
    assert db["name"] == "db" and db["unit"] == "dB" and db["default"] == 0
    assert db["low"] < 0 < db["high"]
    for e in effects.values():
        for p in e["params"]:
            assert p["low"] <= p["default"] <= p["high"]
    assert effects["eq"]["params"][0]["choices"][0] == "peak"


def test_without_rust_the_rack_is_left_alone(monkeypatch):
    monkeypatch.setattr(native, "redline_dsp", None)
    assert native.rack_process(noise(0.1), RATE, _rack()) is None
    assert native.rack_effects() is None
