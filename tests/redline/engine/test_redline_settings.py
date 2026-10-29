import json
import math
from dataclasses import asdict, fields

import pytest
from redline.engine.settings import SPECS, Settings, from_dict, load, save, schema
from redline.master.run import BASS_DRIVE_DB, BASS_FREQ_HZ, OCTAVE_LEVEL_DB, VOCAL_DB
from redline.vocal.chain import LOUDEST
from redline.vocal.timbre import DOUBLE_DELAY_MS, DOUBLE_DEPTH_MS, DOUBLE_LEVEL_DB


def test_the_defaults_are_the_picks_made_by_ear():
    s = Settings()
    comp = LOUDEST.compressor
    assert (s.comp_threshold_db, s.comp_ratio, s.comp_attack_ms, s.comp_release_ms) == (
        comp.threshold_db,
        comp.ratio,
        comp.attack_ms,
        comp.release_ms,
    )
    assert (s.drive_db, s.clip_below_peak_db) == (LOUDEST.drive_db, LOUDEST.clip_below_peak_db)
    assert s.chain_on and s.clip_on and s.eq_fit and s.eq_offsets_db == (0.0,) * 6
    assert s.vocal_db == VOCAL_DB
    assert s.octave_down_on and s.octave_down_db == OCTAVE_LEVEL_DB and not s.octave_up_on
    assert s.doubles_on and s.doubles_db == DOUBLE_LEVEL_DB
    assert (s.doubles_left_ms, s.doubles_right_ms) == DOUBLE_DELAY_MS
    assert s.doubles_wander_ms == DOUBLE_DEPTH_MS
    assert (s.bass_freq_hz, s.bass_drive_db, s.beat_db) == (BASS_FREQ_HZ, BASS_DRIVE_DB, 0.0)
    assert s.hard_tune == 0 and s.formant == 1.0 and s.voice_octave == 0 and not s.nightcore
    assert not s.bitcrush_on and s.master_on and s.master_auto


def test_every_setting_has_a_spec_with_its_default_in_range():
    assert set(SPECS) == {f.name for f in fields(Settings)}
    for key, value in asdict(Settings()).items():
        spec = SPECS[key]
        assert spec.label and spec.help
        assert spec.low <= float(value) <= spec.high, key


@pytest.mark.parametrize(
    "data",
    [
        {"formant": 2.5},
        {"formant": 0.1},
        {"formant": math.nan},
        {"formant": "1.2"},
        {"chain_on": 1},
        {"bitcrush_bits": 4.5},
        {"voice_octave": 0.5},
        {"vocal_db": True},
        {"volume": 3},
    ],
)
def test_wrong_values_and_unknown_keys_are_refused(data):
    with pytest.raises(ValueError):
        from_dict(data)


def test_edges_are_allowed_and_left_out_keys_keep_their_defaults():
    s = from_dict({"formant": 0.6, "vocal_db": 6, "bitcrush_bits": 16, "voice_octave": -1})
    assert (s.formant, s.vocal_db, s.bitcrush_bits, s.voice_octave) == (0.6, 6.0, 16, -1)
    assert s.drive_db == Settings().drive_db


def test_a_saved_file_reads_back_the_same(tmp_path):
    s = from_dict({"formant": 1.35, "doubles_on": False})
    save(s, tmp_path / "s.json")
    assert load(tmp_path / "s.json") == s
    (tmp_path / "bad.json").write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ValueError):
        load(tmp_path / "bad.json")


def test_the_schema_lists_every_setting_once_with_its_group():
    out = schema()
    keys = [row["key"] for row in out["settings"]]
    assert sorted(keys) == sorted(SPECS) and len(keys) == len(set(keys))
    assert all(row["group"] in out["groups"] for row in out["settings"])


def test_about_ten_main_settings_are_marked_basic():
    basic = [key for key, spec in SPECS.items() if spec.basic]
    assert 8 <= len(basic) <= 16
    # a basic setting that greys out behind a switch needs that switch in view too
    assert {"master_auto", "octave_down_on", "doubles_on"} <= set(basic)
    assert {"vocal_db", "drive_db", "formant", "bass_drive_db"} <= set(basic)
    assert all(row["basic"] is (row["key"] in basic) for row in schema()["settings"])
