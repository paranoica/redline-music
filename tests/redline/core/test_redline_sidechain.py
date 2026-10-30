import json

import numpy as np
import pytest
from redline.core import native
from redline_signals import tone

RATE = 44100
pytest.importorskip("redline_dsp")


def _rack(*effects):
    return json.dumps({"effects": list(effects)})


def _square(freq, seconds, amp):
    t = np.arange(int(seconds * RATE)) / RATE
    mono = np.where((freq * t) % 1.0 < 0.5, amp, -amp)
    return np.column_stack([mono, mono]).astype(np.float32)


def _amplitude(x, freq):
    window = np.hanning(len(x))
    spectrum = np.fft.rfft(x * window)
    k = round(freq * len(x) / RATE)
    return 2 * np.abs(spectrum[k]) / window.sum()


COMPRESSOR = _rack(
    {
        "effect": "compressor",
        "params": {"threshold": -20, "ratio": 4, "knee": 0, "detector": "side"},
    }
)


def test_a_sidechain_compressor_turns_the_main_down_by_the_side_level():
    main = tone(1000.0, 1.0, amp=0.05)
    side = _square(100.0, 1.0, 0.5)
    y = native.rack_process(main, RATE, COMPRESSOR, side=side)
    assert y.shape == main.shape and y.dtype == np.float32
    # the side's level is -6.02 dB: (20 - 6.02) * (1 - 1/4) dB of reduction
    want = -(20 + 20 * np.log10(0.5)) * 0.75
    steady = slice(RATE * 3 // 4, None)
    got = 20 * np.log10(np.abs(y[steady, 0]).max() / np.abs(main[steady, 0]).max())
    assert got == pytest.approx(want, abs=0.5)
    # no side: nothing to react to
    alone = native.rack_process(main, RATE, COMPRESSOR)
    assert np.array_equal(alone, main)


def test_a_side_of_another_shape_is_refused():
    main = tone(1000.0, 0.2)
    with pytest.raises(ValueError, match="side"):
        native.rack_process(main, RATE, COMPRESSOR, side=main[:-1])
    with pytest.raises(ValueError, match="side"):
        native.rack_process(main, RATE, COMPRESSOR, side=main[:, :1])


def test_a_ring_modulator_rack_gives_the_sum_and_difference():
    x = tone(1000.0, 1.0, amp=0.5)
    y = native.rack_process(x, RATE, _rack({"effect": "ring_mod", "params": {"frequency": 300}}))
    steady = y[RATE // 2 :, 0].astype(np.float64)
    for f in (700.0, 1300.0):
        assert _amplitude(steady, f) == pytest.approx(0.25, rel=0.03)
    assert _amplitude(steady, 1000.0) < 1e-3


def test_a_ducker_behind_a_limiter_hears_the_side_as_late_as_the_vocal():
    # under its ceiling the limiter only delays; the rack takes its latency back out, so the
    # ducker behind it must give what it gives alone, dip for dip
    main = tone(300.0, 1.0, amp=0.2)
    side = np.zeros_like(main)
    side[RATE // 2 : RATE // 2 + 200] = 0.9
    duck = {"effect": "duck", "params": {"depth": 24, "attack": 0.1, "release": 20}}
    limiter = {"effect": "limiter", "params": {"lookahead": 10}}
    alone = native.rack_process(main, RATE, _rack(duck), side=side)
    behind = native.rack_process(main, RATE, _rack(limiter, duck), side=side)
    assert np.abs(alone - main).max() > 0.05
    np.testing.assert_allclose(behind, alone, atol=1e-6)
