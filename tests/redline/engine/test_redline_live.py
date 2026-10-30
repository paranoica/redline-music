import json
from dataclasses import fields

import numpy as np
import pytest
from redline.core.loudness import integrated_lufs
from redline.core.spectrum import tonal_curve
from redline.engine import live
from redline.engine.render import Context, Engine
from redline.engine.settings import KINDS, SPECS, Settings, from_dict, to_dict
from redline.master.clip import saturate_low
from redline.vocal.chain import Target
from redline_signals import make_rng

redline_dsp = pytest.importorskip("redline_dsp")

RATE = 44100
SECONDS = 10.0
UPPER = 16000.0


def track() -> tuple[np.ndarray, np.ndarray]:
    """A rap-like vocal in four phrases with pauses, over a kick, a snare and hats."""
    rng = make_rng()
    n = int(SECONDS * RATE)
    t = np.arange(n) / RATE
    f0 = 165 * (1 + 0.01 * np.sin(2 * np.pi * 5.5 * t)) * (1 + 0.06 * (np.floor(t * 2) % 3))
    phase = 2 * np.pi * np.cumsum(f0) / RATE
    voice = sum(np.sin(k * phase) / k**1.2 for k in range(1, 30))
    voice = voice + 0.3 * np.convolve(rng.standard_normal(n), np.ones(4) / 4, "same")
    syllables = 0.55 + 0.45 * np.abs(np.sin(2 * np.pi * 3.0 * t))
    phrase = ((t % 2.5) < 1.9).astype(float)
    ramp = np.convolve(phrase, np.hanning(int(0.03 * RATE)), "same")
    ramp /= ramp.max()
    voice = 0.12 * voice * syllables * ramp
    hit = t % 0.5
    # every hit the same 808, gliding from 104 Hz down to 52
    kick = 0.7 * np.sin(2 * np.pi * 52 * hit * (1 + np.exp(-hit / 0.02))) * np.exp(-hit / 0.18)
    hats = 0.05 * rng.standard_normal(n) * np.exp(-((t % 0.25) / 0.02))
    hats = hats - np.convolve(hats, np.ones(8) / 8, "same")
    snare_t = (t - 0.5) % 1.0
    snare = (0.15 * rng.standard_normal(n) + 0.2 * np.sin(2 * np.pi * 190 * t)) * np.exp(
        -snare_t / 0.06
    )
    beat = kick + hats + snare
    vocal = np.column_stack([voice, 0.97 * voice]).astype(np.float32)
    beat = np.column_stack([beat, beat]).astype(np.float32)
    return vocal, beat


@pytest.fixture(scope="module")
def stems():
    vocal, beat = track()
    # the references sound a little brighter and thinner than the track's voice
    brighter = vocal - 0.5 * np.vstack([vocal[:1], vocal[:-1]])
    curve = tonal_curve(brighter, RATE, UPPER)
    context = Context(RATE, Target(curve.centers_hz, curve.curve_db), UPPER, 0.0, -9.0)
    return vocal, beat, context


def streamed(vocal, beat, racks):
    """The vocal and beat stems after their racks, and the master's output of their sum."""
    beat_out = redline_dsp.rack_process(beat, float(RATE), racks["beat"])
    # the beat after its rack is the vocal rack's side signal, as in the engine
    vocal_out = redline_dsp.rack_process(vocal, float(RATE), racks["vocal"], side=beat_out)
    final = redline_dsp.rack_process(vocal_out + beat_out, float(RATE), racks["master"])
    return vocal_out, beat_out, final


def envelope(x):
    frame = int(0.05 * RATE)
    count = len(x) // frame
    mono = x[: count * frame].mean(axis=1).reshape(count, frame)
    return np.sqrt(np.mean(np.square(mono, dtype=np.float64), axis=1))


def differences(render, stream):
    """Loudness in LU, spectral tilt in dB per octave, and the correlation of 50 ms levels."""
    assert stream.shape == render.shape
    return (
        integrated_lufs(stream, RATE) - integrated_lufs(render, RATE),
        tonal_curve(stream, RATE, UPPER).tilt_db_per_oct
        - tonal_curve(render, RATE, UPPER).tilt_db_per_oct,
        float(np.corrcoef(envelope(render), envelope(stream))[0, 1]),
    )


def within(diff, lu, tilt, corr):
    return abs(diff[0]) <= lu and abs(diff[1]) <= tilt and diff[2] > corr


# the final as the plan asks; the stems apart, so a stage the master's gain hides still
# shows. The vocal's octaves, formant, throws and reverb are other algorithms than the
# engine's (live.APPROXIMATED) and read up to about 0.9 LU off on their own
FINAL = (0.5, 1.0, 0.95)
BEAT = (0.5, 1.0, 0.95)
VOCAL = (1.0, 1.0, 0.9)
# the chain alone is the engine's own law, oversampling filter and EQ
CHAIN = (0.1, 0.1, 0.995)


CASES = {
    "default": {},
    "clean, manual eq, no 808 drive, quieter master": {
        "chain_on": False,
        "eq_200": 3.0,
        "eq_5000": -4.0,
        "octave_down_on": False,
        "doubles_on": False,
        "bass_drive_db": 0.0,
        "master_auto": False,
        "master_lufs": -12.0,
    },
    "lighter chain, octave up, throws, harder 808": {
        "comp_threshold_db": -24.0,
        "comp_ratio": 6.0,
        "comp_attack_ms": 5.0,
        "comp_release_ms": 80.0,
        "drive_db": 12.0,
        "clip_below_peak_db": 3.0,
        "vocal_db": -3.0,
        "octave_down_on": False,
        "octave_up_on": True,
        "throw_on": True,
        "bass_freq_hz": 90.0,
        "bass_drive_db": 24.0,
    },
    "no clip, no fit, formant, bitcrush, reverb, louder master": {
        "drive_db": 18.0,
        "clip_on": False,
        "eq_fit": False,
        "formant": 1.15,
        "bitcrush_on": True,
        "bitcrush_bits": 10,
        "bitcrush_hold": 2,
        "reverb_on": True,
        "beat_db": -2.0,
        "master_auto": False,
        "master_lufs": -8.0,
    },
}


def rendered_and_streamed(stems, case):
    vocal, beat, context = stems
    settings = from_dict(case)
    engine = Engine(vocal, beat, context)
    rendered = engine.render(settings)
    racks = live.racks(settings, live.analysis(engine, settings))
    return rendered, streamed(vocal, beat, racks)


@pytest.mark.parametrize("name", list(CASES))
def test_the_racks_sound_like_the_render(stems, name):
    rendered, (vocal, beat, final) = rendered_and_streamed(stems, CASES[name])
    assert within(differences(rendered.final, final), *FINAL), differences(rendered.final, final)
    assert within(differences(rendered.beat, beat), *BEAT), differences(rendered.beat, beat)
    assert within(differences(rendered.vocal, vocal), *VOCAL), differences(rendered.vocal, vocal)


CHAIN_ONLY = {"octave_down_on": False, "doubles_on": False, "master_on": False}


@pytest.mark.parametrize(
    "case",
    [
        {},
        {
            "comp_threshold_db": -24.0,
            "comp_ratio": 6.0,
            "drive_db": 12.0,
            "clip_below_peak_db": 3.0,
        },
        {"drive_db": 18.0, "clip_on": False, "eq_fit": False, "eq_2500": 4.0},
        {"chain_on": False, "eq_400": -3.0},
    ],
)
def test_the_chain_rack_follows_the_engines_chain_closely(stems, case):
    rendered, (vocal, _, _) = rendered_and_streamed(stems, {**CHAIN_ONLY, **case})
    assert within(differences(rendered.vocal, vocal), *CHAIN), differences(rendered.vocal, vocal)


@pytest.mark.parametrize(
    ("freq", "drive"), [(120.0, 12.0), (90.0, 24.0), (250.0, 6.0), (60.0, 30.0)]
)
def test_the_808_drive_follows_saturate_low_wave_for_wave(stems, freq, drive):
    _, beat, _ = stems
    want = saturate_low(beat, RATE, freq, drive)
    settings = from_dict({"bass_freq_hz": freq, "bass_drive_db": drive})
    got = redline_dsp.rack_process(beat, float(RATE), live.racks(settings, fake_analysis())["beat"])
    # the stream learns the sub's level as the beat plays: from the first second on it holds
    # what the engine measures on the whole beat; a drive 3 dB off or a split 30 % off reads
    # 0.05 or more here
    rest = slice(RATE, None)
    error = np.sqrt(np.mean((got[rest] - want[rest]) ** 2)) / np.sqrt(np.mean(want[rest] ** 2))
    assert error < 0.035, error


def names(rack_json):
    return [e["effect"] for e in json.loads(rack_json)["effects"] if e.get("on", True)]


def fake_analysis(**changes):
    numbers = {
        "rate": RATE,
        "pre_gain_db": 4.0,
        "gate_db": -50.0,
        "makeup_db": 20.0,
        "peak_db": -3.0,
        "eq_fit_db": [1.0, -2.0, 0.5, 3.0, -1.0, 2.0],
        "post_gain_db": -5.0,
        "tuning_cents": 12.0,
        "bitcrush_peak_db": -2.0,
        "throw_threshold_db": -40.0,
        "master_gain_db": 8.0,
        "master_trim_db": -0.3,
        "settings": to_dict(Settings()),
    }
    return {**numbers, **changes}


def test_the_default_racks_run_the_engines_stages_in_its_order():
    racks = live.racks(Settings(), fake_analysis())
    assert names(racks["vocal"]) == [
        "gain",
        "chain_compressor",
        "chain_clip",
        *["eq"] * 6,
        "gain",
        "octaver",
        "doubler",
    ]
    assert names(racks["beat"]) == ["bass_drive"]
    assert names(racks["master"]) == ["master"]
    vocal = json.loads(racks["vocal"])["effects"]
    # the pre-gain, the chain's own loudness back plus the vocal against the beat
    assert vocal[0]["params"] == {"db": 4.0}
    assert vocal[9]["params"] == {"db": -5.0 + -6.0}
    assert vocal[1]["params"]["gate"] == -50.0 and vocal[1]["params"]["makeup"] == 20.0
    assert vocal[2]["params"] == {"drive": 30.0, "clip": "on", "below": 12.0, "peak": -3.0}
    assert [e["params"]["gain"] for e in vocal[3:9]] == [1.0, -2.0, 0.5, 3.0, -1.0, 2.0]
    assert [e["params"]["kind"] for e in vocal[3:9]] == ["lowshelf", *["peak"] * 4, "highshelf"]
    master = json.loads(racks["master"])["effects"][0]["params"]
    assert master == {"gain": 8.0, "trim": -0.3, "ceiling": -0.1}


def test_off_stages_leave_the_racks_and_the_user_rack_comes_after_the_fx():
    settings = from_dict(
        {
            "version": 2,
            "chain_on": False,
            "eq_fit": False,
            "octave_down_on": False,
            "doubles_on": False,
            "reverb_on": True,
            "bass_drive_db": 0.0,
            "master_on": False,
            "rack": [{"effect": "width", "params": {"width": 1.5}}],
        }
    )
    racks = live.racks(settings, fake_analysis())
    assert names(racks["vocal"]) == ["gain", "gain", "room", "gain", "width"]
    assert names(racks["beat"]) == []
    assert names(racks["master"]) == []


def test_manual_eq_rides_on_the_fit_and_stops_where_the_engine_does():
    settings = from_dict({"eq_200": 12.0, "eq_400": -12.0, "eq_8000": 12.0})
    fit = [12.0, -12.0, 0.0, 0.0, 0.0, 15.0]
    vocal = json.loads(live.racks(settings, fake_analysis(eq_fit_db=fit))["vocal"])["effects"]
    gains = [e["params"]["gain"] for e in vocal if e["effect"] == "eq"]
    # bands with no gain are left out; the sum is held to 24 dB either way
    assert gains == [24.0, -24.0, 24.0]


def test_rack_lanes_move_to_the_user_racks_slots():
    settings = from_dict(
        {
            "version": 2,
            "rack": [
                {"effect": "gain", "params": {"db": 0}},
                {"effect": "pan", "automation": [{"param": "pan", "points": [[0, -1], [1, 1]]}]},
            ],
        }
    )
    numbers = fake_analysis()
    lanes = live.vocal_lanes(settings, numbers)
    vocal = json.loads(live.racks(settings, numbers)["vocal"])["effects"]
    ((slot, param, points),) = lanes
    assert vocal[slot]["effect"] == "pan" and slot == len(vocal) - 1
    assert param == "pan" and points == [(0.0, -1.0), (1.0, 1.0)]


ALL_ON = {
    "hard_tune": 50.0,
    "formant": 1.2,
    "voice_octave": 1,
    "nightcore": True,
    "octave_up_on": True,
    "bitcrush_on": True,
    "stutter_on": True,
    "throw_on": True,
    "reverb_on": True,
    "beat_db": -1.0,
    "eq_200": 1.0,
    "master_auto": False,
}


def other_value(key):
    spec, kind, now = SPECS[key], KINDS[key], getattr(from_dict(ALL_ON), key)
    if kind is bool:
        return not now
    for value in (spec.high, spec.low, (spec.low + spec.high) / 2):
        if value != now:
            return int(value) if kind is int else float(value)
    raise AssertionError(key)


@pytest.mark.parametrize("key", list(KINDS))
def test_every_setting_reaches_the_racks_or_says_why_not(key):
    base = from_dict(ALL_ON)
    changed = from_dict({**ALL_ON, key: other_value(key)})
    before = live.racks(base, fake_analysis())
    after = live.racks(changed, fake_analysis())
    if key in live.NOT_EXPRESSED:
        assert before == after
        assert live.NOT_EXPRESSED[key]
    elif key in live.THROUGH_ANALYSIS:
        # it moves the master's gain, which only a new analysis measures
        assert before == after
        assert "master_gain_db" in live.stale(fake_analysis(settings=to_dict(base)), changed)
    else:
        assert before != after, key


def test_a_change_names_the_numbers_it_makes_stale():
    numbers = fake_analysis()
    assert live.stale(numbers, Settings()) == []
    # the master's target moves only its gain
    master = live.stale(numbers, from_dict({"master_lufs": -7.0, "master_auto": False}))
    assert set(master) == {"master_gain_db", "master_trim_db"}
    # the beat reaches only the master
    assert set(live.stale(numbers, from_dict({"bass_drive_db": 3.0}))) == set(master)
    # the drive changes what the EQ is fitted to, but not the compressor's numbers
    drive = set(live.stale(numbers, from_dict({"drive_db": 10.0})))
    assert {"eq_fit_db", "post_gain_db", "master_gain_db"} <= drive
    assert not drive & {"makeup_db", "peak_db", "pre_gain_db", "gate_db", "tuning_cents"}
    # a manual band leaves the fit alone, and only the loudness after the EQ moves
    eq = set(live.stale(numbers, from_dict({"eq_1000": 2.0})))
    assert "eq_fit_db" not in eq and "post_gain_db" in eq


def test_every_setting_is_known_to_the_staleness_table():
    known = {name for keys in live.STALE_AFTER.values() for name in keys}
    assert {f.name for f in fields(Settings)} <= known
