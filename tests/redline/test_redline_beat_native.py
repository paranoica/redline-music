import json

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
