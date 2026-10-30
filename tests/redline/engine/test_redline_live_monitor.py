import json
import math
from dataclasses import fields, replace

import pytest
from redline.engine import live
from redline.engine.settings import AutomationLane, RackEffect, Settings
from redline.vocal.chain import EQ_BANDS, PRE_GAIN_LUFS

EVERYTHING_ON = replace(
    Settings(),
    hard_tune=60.0,
    bitcrush_on=True,
    throw_on=True,
    reverb_on=True,
    doubles_on=True,
)


def test_the_neutral_numbers_are_the_ones_named():
    neutral = live.NEUTRAL_NUMBERS
    assert neutral["gate_db"] == live.GATE_RANGE_DB[0]
    assert neutral["makeup_db"] == 0.0
    assert neutral["peak_db"] == 0.0
    assert neutral["eq_fit_db"] == [0.0] * len(EQ_BANDS)
    assert neutral["tuning_cents"] == 0.0
    assert neutral["bitcrush_peak_db"] == 0.0
    assert neutral["throw_threshold_db"] == live.THROW_THRESHOLD_RANGE_DB[0]
    assert neutral["master_gain_db"] is None and neutral["master_trim_db"] is None


def test_a_level_check_sets_the_pre_gain_and_gives_the_level_back_after_the_chain():
    numbers = live.level_numbers(-30.0, 48000)
    assert numbers["pre_gain_db"] == pytest.approx(PRE_GAIN_LUFS + 30.0)
    assert numbers["post_gain_db"] == pytest.approx(-(PRE_GAIN_LUFS + 30.0))
    assert numbers["rate"] == 48000
    for name, value in live.NEUTRAL_NUMBERS.items():
        assert numbers[name] == value
    # the same keys as a full analysis, so the racks and `stale` read them alike
    assert set(live.STALE_AFTER) <= set(numbers)
    assert numbers["settings"]["chain_on"] is True


def test_the_neutral_numbers_are_a_copy_not_the_table():
    numbers = live.level_numbers(-24.0, 48000)
    numbers["eq_fit_db"][0] = 5.0
    assert live.NEUTRAL_NUMBERS["eq_fit_db"][0] == 0.0


@pytest.mark.parametrize("loudness", [-math.inf, math.nan])
def test_a_silent_level_check_is_refused(loudness):
    with pytest.raises(ValueError, match="mic"):
        live.level_numbers(loudness, 48000)


def test_the_neutral_numbers_build_every_stage_of_the_vocal_rack():
    numbers = live.level_numbers(-26.0, 48000, EVERYTHING_ON)
    effects = json.loads(live.monitor_rack(EVERYTHING_ON, numbers))["effects"]
    names = [e["effect"] for e in effects]
    for name in ("chain_compressor", "chain_clip", "tune", "bitcrush", "throws", "room"):
        assert name in names
    assert effects[0] == {"effect": "gain", "on": True, "params": {"db": 8.0}}
    assert "eq" not in names


def test_the_monitor_compressor_looks_ahead_zero_and_nothing_else_changes():
    user = RackEffect(
        "chain_compressor",
        True,
        (("ratio", 4.0),),
        (AutomationLane("threshold", ((0.0, -30.0), (2.0, -10.0))),),
    )
    gain = RackEffect("gain", True, (("db", -3.0),), (AutomationLane("db", ((0.0, -12.0),)),))
    settings = replace(Settings(), rack=(user, gain))
    numbers = live.level_numbers(-20.0, 48000, settings)
    monitor = json.loads(live.monitor_rack(settings, numbers))["effects"]
    numbers_with_master = {**numbers, "master_gain_db": 0.0, "master_trim_db": 0.0}
    full = json.loads(live.racks(settings, numbers_with_master)["vocal"])["effects"]
    compressors = [e for e in monitor if e["effect"] == "chain_compressor"]
    assert len(compressors) == 2
    assert all(e["params"]["lookahead"] == live.MONITOR_LOOKAHEAD == 0 for e in compressors)
    for e in monitor:
        e["params"].pop("lookahead", None)
    assert monitor == full
    assert not any("automation" in e for e in monitor)
    assert monitor[-1] == {"effect": "gain", "on": True, "params": {"db": -3.0}}


def test_level_numbers_cover_every_setting_field():
    numbers = live.level_numbers(-20.0, 48000)
    assert set(numbers["settings"]) >= {f.name for f in fields(Settings)} - {"rack"}
