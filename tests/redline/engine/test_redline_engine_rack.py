import numpy as np
import pytest
from redline.core import native
from redline.engine import render as engine_module
from redline.engine.render import Engine
from redline.engine.settings import RackEffect, Settings, from_dict
from redline_signals import RATE
from redline_signals import engine_context as context
from redline_signals import engine_stems as stems

pytest.importorskip("redline_dsp")


@pytest.fixture
def setup():
    vocal, beat = stems()
    return vocal, beat, context(vocal)


def with_rack(*rack, **scalars):
    return from_dict({"version": 2, **scalars, "rack": list(rack)})


def rms(x, start=0.0, end=None):
    part = x[int(start * RATE) : None if end is None else int(end * RATE)]
    return float(np.sqrt(np.mean(np.square(part, dtype=np.float64))))


def counted(monkeypatch, *names):
    calls = dict.fromkeys(names, 0)
    for name in names:
        owner = native if name.startswith("rack_") else engine_module
        original = getattr(owner, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)
    return calls


def test_no_rack_never_calls_rust_and_renders_as_before(setup, monkeypatch):
    vocal, beat, ctx = setup
    plain = Engine(vocal, beat, ctx).render(Settings())
    calls = counted(monkeypatch, "rack_process", "rack_process_automated")
    for settings in (Settings(), with_rack(), with_rack({"effect": "gain", "on": False})):
        out = Engine(vocal, beat, ctx).render(settings)
        assert np.array_equal(out.final, plain.final) and np.array_equal(out.vocal, plain.vocal)
    assert calls == {"rack_process": 0, "rack_process_automated": 0}


def test_a_gain_rack_scales_the_vocal_after_the_effects_and_leaves_the_beat(setup):
    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    plain = engine.render(Settings())
    quieter = engine.render(with_rack({"effect": "gain", "params": {"db": -6}}))
    np.testing.assert_allclose(quieter.vocal, plain.vocal * 10 ** (-6 / 20), rtol=1e-5, atol=1e-7)
    assert np.array_equal(quieter.beat, plain.beat)
    assert not np.array_equal(quieter.final, plain.final)


def test_a_rack_change_re_runs_only_the_rack_and_the_master(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counted(monkeypatch, "process_vocal", "saturate_low", "master_clip", "rack_process")
    engine = Engine(vocal, beat, ctx)
    a = with_rack({"effect": "gain", "params": {"db": -6}})
    b = with_rack({"effect": "gain", "params": {"db": -3}})
    for settings in (Settings(), a, b, a):
        engine.render(settings)
    assert calls == {"process_vocal": 1, "saturate_low": 1, "master_clip": 3, "rack_process": 2}


def test_a_lane_moves_its_param_over_the_track_in_seconds(setup):
    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    plain = engine.render(Settings()).vocal
    lane = {"param": "db", "points": [[0, 0], [3, 0], [3, -20]]}
    stepped = engine.render(with_rack({"effect": "gain", "automation": [lane]})).vocal
    assert rms(stepped, 0.5, 2.9) / rms(plain, 0.5, 2.9) == pytest.approx(1.0, abs=0.01)
    assert rms(stepped, 3.2, 5.8) / rms(plain, 3.2, 5.8) == pytest.approx(0.1, abs=0.005)


def test_the_rack_hears_the_beat_as_its_side_signal(setup):
    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    plain = engine.render(Settings()).vocal
    ducked = engine.render(with_rack({"effect": "duck", "params": {"depth": 24}})).vocal
    # the kick is loud on every beat, so a ducker listening to it pulls the vocal down
    assert rms(ducked) < rms(plain) * 10 ** (-3 / 20)


def test_a_preview_reads_the_lanes_at_the_track_s_seconds(monkeypatch):
    monkeypatch.setattr(engine_module, "PREVIEW_PAD_S", 1.0)
    monkeypatch.setattr(engine_module, "MAX_PREVIEW_PAD_S", 1.0)
    vocal, beat = stems(9.0)
    engine = Engine(vocal, beat, context(vocal))
    lane = {"param": "db", "points": [[6, 0], [6, -20]]}
    settings = with_rack({"effect": "gain", "automation": [lane]}, master_on=False)
    plain = with_rack(master_on=False)
    start, end = 5 * RATE, 7 * RATE
    # the loop is 5-7 s and the step at 6 s: the rack leaves the first half alone and pulls
    # the vocal down in the second; read against the same preview without the rack
    with_lane, without = engine.preview(settings, start, end), engine.preview(plain, start, end)
    first = 20 * np.log10(rms(with_lane, 0.2, 0.9) / rms(without, 0.2, 0.9))
    second = 20 * np.log10(rms(with_lane, 1.2, 1.9) / rms(without, 1.2, 1.9))
    assert first == pytest.approx(0.0, abs=0.01)
    assert second < -1.0


def test_without_rust_a_rack_in_the_engine_is_refused_plainly(setup, monkeypatch):
    vocal, beat, ctx = setup
    monkeypatch.setattr(native, "redline_dsp", None)
    settings = Settings(rack=(RackEffect("gain"),))
    with pytest.raises(ValueError, match="redline_dsp"):
        Engine(vocal, beat, ctx).render(settings)


@pytest.mark.parametrize(
    "effect",
    [
        {"effect": "reverse", "params": {"window": 250, "fade": 5}},
        {"effect": "reverse_reverb", "params": {"mix": 0.5}},
    ],
)
def test_a_preview_with_an_offline_effect_is_that_span_of_the_whole_render(effect, monkeypatch):
    # windows laid from the track's first frame: a piece starting between two of them would
    # reverse other stretches than the whole render does
    monkeypatch.setattr(engine_module, "PREVIEW_PAD_S", 1.0)
    monkeypatch.setattr(engine_module, "MAX_PREVIEW_PAD_S", 1.0)
    monkeypatch.setattr(engine_module, "process_vocal", lambda vocal, *args: (vocal, None))
    vocal, beat = stems(16.0)
    engine = Engine(vocal, beat, context(vocal))
    plain = {"octave_down_on": False, "doubles_on": False, "bass_drive_db": 0}
    settings = with_rack(effect, master_on=False, **plain)
    start, end = int(5.1 * RATE), int(6.1 * RATE)
    whole = engine.render(settings).final[start:end]
    preview = engine.preview(settings, start, end)
    assert not np.allclose(
        whole, engine.render(with_rack(master_on=False, **plain)).final[start:end]
    )
    np.testing.assert_allclose(preview, whole, atol=1e-6)
