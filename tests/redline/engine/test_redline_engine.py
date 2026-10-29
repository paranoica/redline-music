import numpy as np
import pytest
from redline.engine import render as engine_module
from redline.engine.render import Cancelled, Context, Engine
from redline.engine.settings import Settings, from_dict
from redline.master.clip import master_clip, saturate_low
from redline.vocal.chain import LOUDEST, process_vocal
from redline.vocal.timbre import double, octave_layer
from redline_signals import RATE
from redline_signals import engine_context as context
from redline_signals import engine_stems as stems


@pytest.fixture
def setup():
    vocal, beat = stems()
    return vocal, beat, context(vocal)


def test_default_settings_render_the_chain_the_rounds_chose(setup):
    vocal, beat, ctx = setup
    chained, _ = process_vocal(vocal, RATE, LOUDEST, ctx.target, ctx.upper)
    chained = chained * np.float32(10 ** (-6.0 / 20))
    voiced = double(octave_layer(chained, RATE, -1, -9.5), RATE)
    driven = saturate_low(beat, RATE, 120.0, 12.0)
    expected, _ = master_clip(voiced + driven, RATE, -9.0)
    out = Engine(vocal, beat, ctx).render(Settings())
    assert np.array_equal(out.final, expected)
    assert np.array_equal(out.vocal, voiced) and np.array_equal(out.beat, driven)


def counting(monkeypatch, *names):
    calls = dict.fromkeys(names, 0)
    for name in names:
        original = getattr(engine_module, name)

        def wrapped(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(engine_module, name, wrapped)
    return calls


def test_a_master_change_re_runs_only_the_master(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counting(monkeypatch, "process_vocal", "octave_layer", "saturate_low", "master_clip")
    engine = Engine(vocal, beat, ctx)
    engine.render(Settings())
    engine.render(from_dict({"master_auto": False, "master_lufs": -7.0}))
    assert calls == {"process_vocal": 1, "octave_layer": 1, "saturate_low": 1, "master_clip": 2}


def test_a_voice_change_keeps_the_chain_and_the_beat(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counting(monkeypatch, "process_vocal", "formant_shift", "saturate_low", "master_clip")
    engine = Engine(vocal, beat, ctx)
    engine.render(Settings())
    engine.render(from_dict({"formant": 1.3}))
    assert calls == {"process_vocal": 1, "formant_shift": 1, "saturate_low": 1, "master_clip": 2}


def test_going_back_to_earlier_settings_takes_them_from_the_cache(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counting(monkeypatch, "process_vocal", "master_clip")
    engine = Engine(vocal, beat, ctx)
    first = engine.render(Settings())
    engine.render(from_dict({"drive_db": 10.0}))
    again = engine.render(Settings())
    assert calls == {"process_vocal": 2, "master_clip": 2}
    assert np.array_equal(first.final, again.final)


def test_the_cache_stays_under_its_byte_cap(setup):
    vocal, beat, ctx = setup
    cap = vocal.nbytes * 6
    engine = Engine(vocal, beat, ctx, cache_bytes=cap)
    for drive in (10.0, 14.0, 18.0):
        engine.render(from_dict({"drive_db": drive}))
        assert engine.cached_bytes <= cap


def test_a_cancelled_render_stops_between_stages(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counting(monkeypatch, "process_vocal", "master_clip")
    with pytest.raises(Cancelled):
        Engine(vocal, beat, ctx).render(Settings(), cancelled=lambda: True)
    assert calls["master_clip"] == 0


def test_hard_tune_gets_the_tracks_tuning_and_strength(setup, monkeypatch):
    vocal, beat, ctx = setup
    seen = []
    monkeypatch.setattr(
        engine_module,
        "hard_tune",
        lambda x, rate, cents, strength: seen.append((cents, strength)) or x,
    )
    tuned = Context(ctx.rate, ctx.target, ctx.upper, -17.0, ctx.master_lufs)
    Engine(vocal, beat, tuned).render(from_dict({"hard_tune": 50}))
    assert seen == [(-17.0, 0.5)]


def test_switches_turn_their_stage_off(setup):
    vocal, beat, ctx = setup
    out = Engine(vocal, beat, ctx).render(
        from_dict({"octave_down_on": False, "doubles_on": False, "master_on": False})
    )
    chained, _ = process_vocal(vocal, RATE, LOUDEST, ctx.target, ctx.upper)
    assert np.array_equal(out.vocal, chained * np.float32(10 ** (-6.0 / 20)))
    assert np.array_equal(out.final, out.vocal + out.beat)


def test_a_preview_has_the_loops_length_and_is_close_to_the_whole_render(setup):
    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    start, end = 2 * RATE, 4 * RATE
    preview = engine.preview(Settings(), start, end)
    assert preview.shape == (end - start, 2)
    whole = engine.render(Settings()).final[start:end]
    level = np.sqrt(np.mean(whole**2))
    assert np.sqrt(np.mean((preview - whole) ** 2)) < 0.5 * level


def test_a_preview_after_an_exact_render_takes_its_master_gain(setup, monkeypatch):
    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    whole = engine.render(Settings())
    calls = counting(monkeypatch, "master_clip")
    start, end = 2 * RATE, 4 * RATE
    preview = engine.preview(Settings(), start, end)
    assert calls["master_clip"] == 0
    level = np.sqrt(np.mean(whole.final[start:end] ** 2))
    assert np.sqrt(np.mean((preview - whole.final[start:end]) ** 2)) < 0.25 * level


def test_a_preview_after_a_louder_vocal_keeps_the_masters_loudness(setup):
    from redline.core.loudness import integrated_lufs

    vocal, beat, ctx = setup
    engine = Engine(vocal, beat, ctx)
    engine.render(Settings())
    louder = from_dict({"vocal_db": 3.0})
    start, end = 1 * RATE, 5 * RATE
    preview = engine.preview(louder, start, end)
    whole = engine.render(louder).final[start:end]
    assert abs(integrated_lufs(preview, RATE) - integrated_lufs(whole, RATE)) < 0.5


def test_an_effect_change_re_runs_only_the_effects_and_the_master(setup, monkeypatch):
    vocal, beat, ctx = setup
    calls = counting(monkeypatch, "process_vocal", "octave_layer", "reverb", "master_clip")
    engine = Engine(vocal, beat, ctx)
    engine.render(Settings())
    engine.render(from_dict({"reverb_on": True}))
    engine.render(from_dict({"reverb_on": True, "reverb_mix_db": -8.0}))
    assert calls == {"process_vocal": 1, "octave_layer": 1, "reverb": 2, "master_clip": 3}


def test_effects_off_leave_the_vocal_as_the_layers_made_it(setup):
    vocal, beat, ctx = setup
    plain = Engine(vocal, beat, ctx).render(Settings())
    on = Engine(vocal, beat, ctx).render(from_dict({"reverb_on": True}))
    assert not np.array_equal(plain.vocal, on.vocal)
    assert np.array_equal(Engine(vocal, beat, ctx).render(Settings()).vocal, plain.vocal)
