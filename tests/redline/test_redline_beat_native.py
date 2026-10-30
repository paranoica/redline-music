import json

import numpy as np
import pytest

redline_dsp = pytest.importorskip("redline_dsp")

SCORE = {
    "version": 1,
    "bpm": 120,
    "seed": 3,
    "key": "A minor",
    "sounds": {"kick": {"instrument": "kick"}, "bass": {"instrument": "bass808"}},
    "patterns": {
        "p": {
            "bars": 1,
            "rows": {
                "kick": "x.......x.......",
                "bass": [{"step": 0, "len": 8, "note": "A1", "vel": 1.0}],
            },
        }
    },
    "sections": [{"name": "a", "bars": 2, "play": ["p"]}],
}


def test_a_valid_score_has_no_errors():
    assert redline_dsp.beat_check(json.dumps(SCORE)) == []


def test_every_error_comes_back_with_its_path():
    broken = json.loads(json.dumps(SCORE))
    broken["patterns"]["p"]["rows"]["kick"] = "x......."
    broken["bpm"] = 400
    errors = redline_dsp.beat_check(json.dumps(broken))
    assert any(e.startswith("bpm: ") for e in errors)
    assert any(e.startswith("patterns.p.rows.kick: ") for e in errors)
    assert len(errors) == 2


def test_text_that_is_not_json_is_an_error_not_an_exception():
    assert len(redline_dsp.beat_check("{")) == 1


def test_the_event_table_at_120_bpm():
    table = json.loads(redline_dsp.beat_events(json.dumps(SCORE), 48000.0))
    # two bars of 16 sixteenths of 6000 frames
    assert table["length"] == 2 * 16 * 6000
    assert table["key"] == {"tonic": 9, "mode": "minor"}
    kicks = [e["start"] for e in table["events"] if e["sound"] == 1]
    assert kicks == [0, 8 * 6000, 16 * 6000, 24 * 6000]
    names = [s["name"] for s in table["sounds"]]
    assert names == ["bass", "kick"]


def test_events_of_a_broken_score_raise_with_the_errors():
    with pytest.raises(ValueError, match="sections"):
        redline_dsp.beat_events(json.dumps({**SCORE, "sections": []}), 48000.0)


def test_the_instruments_list_has_every_param_spec():
    instruments = {i["instrument"]: i for i in json.loads(redline_dsp.beat_instruments())}
    assert instruments["bass808"]["mono"] is True
    assert "glide_ms" in [p["name"] for p in instruments["bass808"]["params"]]
    assert instruments["hat_open"]["choked"] is True


RATE = 48000.0
# a 16th at 120 bpm and 48 kHz
STEP = 6000


def _score(sounds: dict, rows: dict, bars: int, mix: dict | None = None) -> str:
    score = {
        "version": 1,
        "bpm": 120,
        "seed": 9,
        "key": "C minor",
        "sounds": sounds,
        "patterns": {"p": {"bars": bars, "rows": rows}},
        "sections": [{"name": "a", "bars": bars, "play": ["p"]}],
    }
    if mix is not None:
        score["mix"] = mix
    return json.dumps(score)


def _band(mix: dict | None = None) -> str:
    """Drums, a gliding 808 and notes on fm, saw and pluck over two bars."""
    return _score(
        {
            "kick": {"instrument": "kick"},
            "hat": {"instrument": "hat_closed"},
            "open": {"instrument": "hat_open"},
            "bass": {"instrument": "bass808", "params": {"glide_ms": 200}},
            "keys": {"instrument": "fm", "params": {"preset": "keys"}},
            "pad": {"instrument": "saw"},
            "pluck": {"instrument": "pluck"},
        },
        {
            "kick": "x.....x...x.....|x..x......x..x..",
            "hat": "x3x.x4x.x6xgx2x.|xxxxxxxx4xxxxx6x",
            "open": "..x.......x.....|..x.......x.....",
            "bass": [
                {"step": 0, "len": 8, "note": "C2", "vel": 1.0},
                {"step": 6, "len": 6, "note": "G2", "vel": 0.8},
                {"step": 16, "len": 16, "note": "D#2", "vel": 0.9},
            ],
            "keys": [
                {"step": 0, "len": 12, "note": "C4", "vel": 0.7},
                {"step": 4, "len": 20, "note": "D#4", "vel": 0.6},
            ],
            "pad": [{"step": 0, "len": 32, "note": "G3", "vel": 0.4}],
            "pluck": [{"step": 7, "len": 20, "note": "D#5", "vel": 0.9}],
        },
        2,
        mix,
    )


def _play(player, frames: int, chunk: int):
    mixes, stems = [], []
    done = 0
    while done < frames:
        n = min(chunk, frames - done)
        mix, part = player.process(n)
        mixes.append(mix)
        stems.append(part)
        done += n
    return np.concatenate(mixes), np.concatenate(stems, axis=1)


def test_the_player_in_chunks_is_the_render_after_its_latency():
    score = _band(
        {
            "sounds": {"kick": {"rack": [{"effect": "saturate", "params": {"drive": 12}}]}},
            "master": [{"effect": "limiter"}],
        }
    )
    want = redline_dsp.beat_render(score, RATE)
    player = redline_dsp.BeatPlayer(score, RATE)
    latency = player.latency
    assert latency > 0
    assert player.length == 2 * 16 * STEP
    assert want.dtype == np.float32 and want.shape[1] == 2
    assert want.shape[0] >= player.length
    mix, stems = _play(player, latency + want.shape[0], 1000)
    assert mix.dtype == np.float32
    assert mix.shape == (latency + want.shape[0], 2)
    assert stems.shape == (7, mix.shape[0], 2)
    assert np.abs(want).max() > 0.01
    assert np.array_equal(mix[latency:].view(np.uint32), want.view(np.uint32))
    assert player.position == mix.shape[0]


def test_the_stems_add_up_to_the_mix_without_a_master_rack():
    player = redline_dsp.BeatPlayer(_band(), RATE)
    assert player.sounds == ["bass", "hat", "keys", "kick", "open", "pad", "pluck"]
    assert player.latency == 0 and player.stem_latency == 0
    mix, stems = _play(player, 2 * 16 * STEP, 1000)
    total = np.zeros_like(mix)
    # in the player's order, so the float sums are the same
    for stem in stems:
        total += stem
    assert all(np.abs(stem).max() > 0 for stem in stems)
    assert np.array_equal(total.view(np.uint32), mix.view(np.uint32))


def test_seek_plays_the_span_of_the_whole_run():
    score = _band()
    whole, whole_stems = _play(redline_dsp.BeatPlayer(score, RATE), 2 * 16 * STEP, 4096)
    player = redline_dsp.BeatPlayer(score, RATE)
    # play elsewhere first: what rings there must not leak past the seek
    player.process(5000)
    # inside the 808's glide, the pad's and the fm notes' bodies
    at = 9 * STEP + 123
    player.seek(at)
    assert player.position == at
    mix, stems = _play(player, 3 * STEP, 1000)
    assert np.abs(mix).max() > 0.01
    assert np.array_equal(mix.view(np.uint32), whole[at : at + 3 * STEP].view(np.uint32))
    assert np.array_equal(stems, whole_stems[:, at : at + 3 * STEP])


def _peak_hz(x: np.ndarray, rate: float) -> float:
    window = np.hanning(len(x))
    spectrum = np.abs(np.fft.rfft(x * window))
    k = int(np.argmax(spectrum[1:])) + 1
    a, b, c = np.log(spectrum[k - 1 : k + 2] + 1e-20)
    return (k + 0.5 * (a - c) / (a - 2 * b + c)) * rate / len(x)


def test_a_loaded_zone_plays_an_octave_up_at_double_frequency():
    score = _score(
        {"keys": {"instrument": "sampler", "library": "sine"}},
        {"keys": [{"step": 0, "len": 8, "note": "C5", "vel": 1.0}]},
        1,
    )
    t = np.arange(int(RATE) * 2) / RATE
    sine = np.round(0.5 * 32767 * np.sin(2 * np.pi * 440.0 * t)).astype(np.int16)

    silent = redline_dsp.BeatPlayer(score, RATE)
    mix, _ = silent.process(4096)
    assert not mix.any(), "no library loaded: the sampler is silent"

    player = redline_dsp.BeatPlayer(score, RATE)
    player.load_zone("sine", 60, 0, 127, RATE, sine)
    player.rebuild_samplers()
    mix, stems = player.process(24000)
    assert stems.shape == (1, 24000, 2)
    left = mix[4800:, 0].astype(np.float64)
    assert np.sqrt(np.mean(left**2)) > 0.05
    assert abs(_peak_hz(left, RATE) - 880.0) < 0.005 * 880.0

    # a stereo zone of the same shape is taken as (frames, 2)
    stereo = redline_dsp.BeatPlayer(score, RATE)
    stereo.load_zone("sine", 60, 0, 127, RATE, np.stack([sine, sine], axis=1))
    stereo.rebuild_samplers()
    mix, _ = stereo.process(24000)
    assert abs(_peak_hz(mix[4800:, 1].astype(np.float64), RATE) - 880.0) < 0.005 * 880.0


def test_a_zone_past_the_sample_budget_is_refused():
    score = _score(
        {"keys": {"instrument": "sampler", "library": "big"}},
        {"keys": [{"step": 0, "len": 8, "note": "C4", "vel": 1.0}]},
        1,
    )
    player = redline_dsp.BeatPlayer(score, RATE)
    # 48 Mi i16 values is the bank's budget; one stereo frame more
    too_big = np.zeros((24 * 1024 * 1024 + 1, 2), dtype=np.int16)
    with pytest.raises(ValueError, match="budget"):
        player.load_zone("big", 60, 0, 127, RATE, too_big)
    # the refusal leaves the bank usable
    player.load_zone("big", 60, 0, 127, RATE, np.ones(1000, dtype=np.int16))
    with pytest.raises(ValueError, match=r"channels|shape"):
        player.load_zone("big", 60, 0, 127, RATE, np.ones((10, 3), dtype=np.int16))
    with pytest.raises(ValueError, match="velocity"):
        player.load_zone("big", 60, 90, 10, RATE, np.ones(10, dtype=np.int16))


def test_a_broken_score_is_refused_with_every_path():
    broken = json.loads(json.dumps(SCORE))
    broken["patterns"]["p"]["rows"]["kick"] = "x......."
    broken["bpm"] = 400
    with pytest.raises(ValueError) as caught:
        redline_dsp.BeatPlayer(json.dumps(broken), RATE)
    lines = str(caught.value).splitlines()
    assert any(line.startswith("bpm: ") for line in lines)
    assert any(line.startswith("patterns.p.rows.kick: ") for line in lines)
    with pytest.raises(ValueError, match=r"patterns\.p\.rows\.kick: "):
        redline_dsp.beat_render(json.dumps(broken), RATE)
    with pytest.raises(ValueError, match="rate"):
        redline_dsp.BeatPlayer(json.dumps(SCORE), 0.0)
