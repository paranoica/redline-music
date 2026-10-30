import json

import numpy as np
import pytest
from redline.core import native
from redline_signals import make_rng

RATE = 44100
pytest.importorskip("redline_dsp")


def noise(seconds, amp=0.3):
    rng = make_rng()
    return (amp * rng.standard_normal((int(seconds * RATE), 2))).astype(np.float32)


def _rack(*effects):
    return json.dumps({"effects": list(effects)})


def rms(x, start, end):
    part = x[int(start * RATE) : int(end * RATE)]
    return float(np.sqrt(np.mean(np.square(part, dtype=np.float64))))


def gain_db(y, x, start, end):
    return 20 * np.log10(rms(y, start, end) / rms(x, start, end))


GAIN = {"effect": "gain"}
STEP = [(0.0, 0.0), (1.0, 0.0), (1.0, -20.0)]


def test_a_flat_lane_renders_as_the_param_set_once():
    x = noise(1.0)
    fixed = native.rack_process(x, RATE, _rack({"effect": "gain", "params": {"db": -6}}))
    flat = native.rack_process_automated(x, RATE, _rack(GAIN), [(0, "db", [(0.3, -6.0)])])
    assert np.array_equal(flat, fixed)


def test_a_step_lane_changes_the_param_at_its_second():
    x = noise(2.0)
    y = native.rack_process_automated(x, RATE, _rack(GAIN), [(0, "db", STEP)])
    assert y.shape == x.shape
    assert gain_db(y, x, 0.1, 0.99) == pytest.approx(0.0, abs=0.01)
    assert gain_db(y, x, 1.15, 1.9) == pytest.approx(-20.0, abs=0.05)


def test_a_ramp_lane_is_linear_between_its_points():
    x = noise(2.0)
    lanes = [(0, "db", [(0.0, -20.0), (2.0, 0.0)])]
    y = native.rack_process_automated(x, RATE, _rack(GAIN), lanes)
    # halfway in time is halfway in decibels; blocks and smoothing lag it by a few ms
    assert gain_db(y, x, 0.98, 1.02) == pytest.approx(-10.0, abs=0.4)
    assert gain_db(y, x, 0.48, 0.52) == pytest.approx(-15.0, abs=0.4)


def _drop_at(y, x, level_db=-10.0):
    hop = RATE // 1000
    for start in range(0, len(x) - hop, hop):
        if gain_db(y, x, start / RATE, (start + 4 * hop) / RATE) < level_db:
            return start / RATE
    return None


@pytest.mark.parametrize("gain_first", [True, False])
def test_a_step_lands_on_its_second_behind_an_effect_with_latency(gain_first):
    x = noise(2.0)
    pitch = {"effect": "pitch"}
    rack = [GAIN, pitch] if gain_first else [pitch, GAIN]
    slot = 0 if gain_first else 1
    y = native.rack_process_automated(x, RATE, _rack(*rack), [(slot, "db", STEP)])
    drop = _drop_at(y, x)
    assert drop is not None and 1.0 <= drop <= 1.05


def test_a_lane_on_an_effect_switched_off_does_nothing():
    x = noise(0.5)
    off = _rack({"effect": "gain", "on": False})
    assert np.array_equal(native.rack_process_automated(x, RATE, off, [(0, "db", STEP)]), x)


def test_the_side_signal_reaches_the_effects_as_without_lanes():
    x, side = noise(1.0, 0.1), noise(1.0, 0.8)
    rack = _rack({"effect": "duck", "params": {"depth": 24}})
    plain = native.rack_process(x, RATE, rack, side)
    lanes = [(0, "threshold", [(0.0, -30.0)])]
    automated = native.rack_process_automated(x, RATE, rack, lanes, side)
    assert np.array_equal(automated, plain)
    assert not np.array_equal(plain, x)


@pytest.mark.parametrize(
    ("lanes", "named"),
    [
        ([(1, "db", [(0.0, 0.0)])], "1"),
        ([(0, "dbb", [(0.0, 0.0)])], "dbb"),
        ([(0, "db", [(0.0, 99.0)])], "99"),
        ([(0, "db", [])], "point"),
        ([(0, "db", [(2.0, 0.0), (1.0, 0.0)])], "order"),
        ([(0, "db", [(float("nan"), 0.0)])], "second"),
        ([(0, "db", [(float("inf"), 0.0)])], "second"),
    ],
)
def test_a_wrong_lane_raises_naming_what_is_wrong(lanes, named):
    with pytest.raises(ValueError, match=named):
        native.rack_process_automated(noise(0.1), RATE, _rack(GAIN), lanes)


def test_points_before_the_start_are_read_as_a_lane_shifted_in_time():
    # a preview starts mid-track and moves the lanes back by its start
    x = noise(1.0)
    early = [(0, "db", [(-1.0, 0.0), (0.0, 0.0), (0.0, -20.0)])]
    y = native.rack_process_automated(x, RATE, _rack(GAIN), early)
    assert gain_db(y, x, 0.1, 0.9) == pytest.approx(-20.0, abs=0.05)


def test_without_rust_automation_is_left_alone(monkeypatch):
    monkeypatch.setattr(native, "redline_dsp", None)
    assert native.rack_process_automated(noise(0.1), RATE, _rack(), []) is None


@pytest.mark.parametrize(("effect", "param"), [("reverse", "window"), ("limiter", "lookahead")])
def test_a_lane_on_a_param_fixed_while_playing_is_refused_by_rust_too(effect, param):
    with pytest.raises(ValueError, match=rf"{effect}\.{param} is fixed while playing"):
        native.rack_process_automated(
            noise(0.1), RATE, _rack({"effect": effect}), [(0, param, [(0.0, 50.0)])]
        )


def test_a_lane_of_more_than_4096_points_is_refused_by_rust_too():
    points = [(i / 1000, 0.0) for i in range(4096)]
    native.rack_process_automated(noise(0.1), RATE, _rack(GAIN), [(0, "db", points)])
    with pytest.raises(ValueError, match="4097 points; a lane takes at most 4096"):
        native.rack_process_automated(
            noise(0.1), RATE, _rack(GAIN), [(0, "db", [*points, (5.0, 0.0)])]
        )


def test_a_rack_of_more_than_32_effects_is_refused_by_rust_too():
    with pytest.raises(ValueError, match="at most 32 effects, not 33"):
        native.rack_process(noise(0.1), RATE, _rack(*[GAIN] * 33))
