import json
import math

import pytest
from redline.core import native
from redline.engine import settings as settings_module
from redline.engine.settings import (
    AutomationLane,
    RackEffect,
    Settings,
    from_dict,
    load,
    load_preset,
    rack_json,
    save,
    schema,
    to_dict,
)

pytest.importorskip("redline_dsp")

GAIN_RAMP = {
    "effect": "gain",
    "params": {"db": -3},
    "automation": [{"param": "db", "points": [[0, -12], [2.5, 0]]}],
}


def v2(*rack, **scalars):
    return {"version": 2, **scalars, "rack": list(rack)}


def test_a_file_without_a_version_or_of_version_one_loads_with_an_empty_rack(tmp_path):
    for data in ({"formant": 1.2}, {"version": 1, "formant": 1.2}):
        path = tmp_path / "old.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        s = load(path)
        assert s.rack == () and s.formant == 1.2
    assert Settings().rack == ()
    # a run's record spells out the empty rack without a version, and still loads
    assert from_dict({"formant": 1.2, "rack": []}) == Settings(formant=1.2)


def test_a_rack_reads_into_effects_with_sorted_params_and_lanes():
    pitch = {"effect": "pitch", "on": False, "params": {"semitones": 12, "cents": -50}}
    s = from_dict(v2(GAIN_RAMP, pitch, formant=1.1))
    assert s.formant == 1.1
    assert s.rack == (
        RackEffect(
            "gain",
            True,
            (("db", -3.0),),
            (AutomationLane("db", ((0.0, -12.0), (2.5, 0.0))),),
        ),
        RackEffect("pitch", False, (("cents", -50.0), ("semitones", 12.0)), ()),
    )


def test_params_given_in_another_order_are_the_same_settings_and_hash_alike():
    a = from_dict(v2({"effect": "pitch", "params": {"semitones": 12, "cents": -50}}))
    b = from_dict(v2({"effect": "pitch", "params": {"cents": -50, "semitones": 12}}))
    assert a == b and hash(a) == hash(b)
    assert len({a, b, Settings()}) == 2


def test_a_choice_param_takes_its_name_or_its_index():
    by_name = from_dict(v2({"effect": "pitch", "params": {"preserve": "on"}}))
    assert by_name.rack[0].params == (("preserve", "on"),)
    by_index = from_dict(v2({"effect": "pitch", "params": {"preserve": 1}}))
    assert by_index.rack[0].params == (("preserve", 1.0),)


def test_the_settings_file_spells_out_the_version_and_the_rack(tmp_path):
    s = from_dict(v2(GAIN_RAMP, {"effect": "pitch", "params": {"preserve": "on"}}))
    data = to_dict(s)
    assert data["version"] == 2
    assert data["rack"] == [
        {
            "effect": "gain",
            "on": True,
            "params": {"db": -3.0},
            "automation": [{"param": "db", "points": [[0.0, -12.0], [2.5, 0.0]]}],
        },
        {"effect": "pitch", "on": True, "params": {"preserve": "on"}, "automation": []},
    ]
    assert to_dict(Settings())["rack"] == [] and to_dict(Settings())["version"] == 2
    save(s, tmp_path / "s.json")
    assert json.loads((tmp_path / "s.json").read_text())["version"] == 2
    assert load(tmp_path / "s.json") == s


def test_a_preset_with_a_rack_loads_by_name(tmp_path):
    (tmp_path / "wide.json").write_text(json.dumps(v2(GAIN_RAMP)), encoding="utf-8")
    assert load_preset(tmp_path, "wide").rack[0].effect == "gain"


def test_the_rack_json_for_rust_holds_the_effects_without_automation():
    s = from_dict(v2(GAIN_RAMP, {"effect": "pitch", "on": False, "params": {"preserve": "on"}}))
    assert json.loads(rack_json(s.rack)) == {
        "effects": [
            {"effect": "gain", "on": True, "params": {"db": -3.0}},
            {"effect": "pitch", "on": False, "params": {"preserve": "on"}},
        ]
    }
    assert json.loads(rack_json(())) == {"effects": []}


def test_params_and_points_at_the_ends_of_their_ranges_are_allowed():
    s = from_dict(
        v2(
            {
                "effect": "gain",
                "params": {"db": -60},
                "automation": [{"param": "db", "points": [[0, -60], [0, 24], [9.5, 24]]}],
            }
        )
    )
    assert s.rack[0].params == (("db", -60.0),)
    assert s.rack[0].automation[0].points == ((0.0, -60.0), (0.0, 24.0), (9.5, 24.0))


@pytest.mark.parametrize(
    ("data", "named"),
    [
        ({"rack": [{"effect": "gain"}]}, "version"),
        ({"version": 1, "rack": [{"effect": "gain"}]}, "version"),
        ({"version": 3}, "version"),
        ({"version": True}, "version"),
        ({"version": "2"}, "version"),
        (v2() | {"rack": {"effect": "gain"}}, "the rack is a list"),
        (v2("gain"), "rack effect 1 is an object"),
        (v2({"effect": "flanger9"}), "flanger9"),
        (v2({"params": {"db": 1}}), "needs an effect"),
        (v2({"effect": "gain", "wet": 1}), "not wet"),
        (v2({"effect": "gain", "on": 1}), "on takes true or false"),
        (v2({"effect": "gain", "params": {"dbb": 1}}), "no param dbb"),
        (v2({"effect": "gain", "params": {"db": 24.5}}), "db takes -60 to 24"),
        (v2({"effect": "gain", "params": {"db": -60.01}}), "db takes -60 to 24"),
        (v2({"effect": "gain", "params": {"db": math.nan}}), "db takes a number"),
        (v2({"effect": "gain", "params": {"db": True}}), "db takes a number"),
        (v2({"effect": "gain", "params": {"db": "loud"}}), "db takes a number"),
        (v2({"effect": "gain", "params": [["db", 1]]}), "params are an object"),
        (v2({"effect": "pitch", "params": {"preserve": "maybe"}}), "preserve takes off, on"),
        (v2({"effect": "pitch", "params": {"preserve": 0.5}}), "preserve takes a whole number"),
        (v2({"effect": "gain", "automation": {"param": "db"}}), "automation is a list"),
        (
            v2({"effect": "gain", "automation": [{"param": "dbb", "points": [[0, 1]]}]}),
            "no param dbb",
        ),
        (v2({"effect": "gain", "automation": [{"param": "db", "points": []}]}), "needs points"),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[0, 30]]}]}),
            "db takes -60 to 24",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[-1, 0]]}]}),
            "seconds from 0",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[2, 0], [1, 0]]}]}),
            "in order of time",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[math.inf, 0]]}]}),
            "seconds from 0",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[10**400, 0]]}]}),
            "seconds from 0",
        ),
        (v2({"effect": "gain", "params": {"db": -(10**400)}}), "db takes a number"),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[0]]}]}),
            r"\[seconds, value\]",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [["hook", 0]]}]}),
            "seconds from 0",
        ),
        (
            v2({"effect": "gain", "automation": [{"param": "db", "points": [[0, 0]], "x": 1}]}),
            "not x",
        ),
        (
            v2(
                {
                    "effect": "gain",
                    "automation": [
                        {"param": "db", "points": [[0, 0]]},
                        {"param": "db", "points": [[1, 0]]},
                    ],
                }
            ),
            "automated twice",
        ),
        (
            v2({"effect": "pitch", "automation": [{"param": "preserve", "points": [[0, 1]]}]}),
            "cannot be automated",
        ),
    ],
)
def test_a_wrong_rack_is_refused_naming_what_is_wrong(data, named):
    with pytest.raises(ValueError, match=named):
        from_dict(data)


def test_a_wrong_slot_is_named_by_its_place_in_the_rack():
    with pytest.raises(ValueError, match="rack effect 2"):
        from_dict(v2({"effect": "gain"}, {"effect": "gain", "params": {"db": 99}}))


def test_a_param_given_twice_in_a_file_is_refused(tmp_path):
    path = tmp_path / "twice.json"
    text = '{"version": 2, "rack": [{"effect": "gain", "params": {"db": -3, "db": 3}}]}'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="db"):
        load(path)


def test_without_rust_a_rack_is_refused_plainly_and_no_rack_still_loads(monkeypatch):
    monkeypatch.setattr(native, "redline_dsp", None)
    with pytest.raises(ValueError, match="redline_dsp"):
        from_dict(v2({"effect": "gain"}))
    assert from_dict(v2(formant=1.2)) == Settings(formant=1.2)
    assert from_dict({"formant": 1.2}) == Settings(formant=1.2)


@pytest.mark.parametrize("readme", ["README.md", "README.ru.md"])
def test_the_readme_s_rack_example_loads(readme):
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / readme).read_text(encoding="utf-8")
    blocks = [b for b in re.findall(r"```json\n(.*?)```", text, re.S) if '"rack"' in b]
    assert blocks
    for block in blocks:
        s = from_dict(json.loads(block))
        assert s.rack and any(effect.automation for effect in s.rack)


def test_the_rack_is_its_own_stage_and_not_a_studio_slider():
    assert settings_module.SPECS["rack"].stage == "rack"
    assert "rack" not in {row["key"] for row in schema()["settings"]}


@pytest.mark.parametrize(
    ("effect", "param", "value"),
    [("reverse", "window", 100), ("limiter", "lookahead", 2), ("granular", "seed", 7)],
)
def test_a_lane_on_a_param_fixed_while_playing_is_refused_naming_it(effect, param, value):
    lane = {"param": param, "points": [[0, value]]}
    with pytest.raises(ValueError, match=rf"{effect}\.{param} is fixed while playing"):
        from_dict(v2({"effect": effect, "automation": [lane]}))
    # set once, it is fine
    rack = from_dict(v2({"effect": effect, "params": {param: value}})).rack
    assert rack[0].params == ((param, float(value)),)


def test_the_catalogue_tells_which_params_are_fixed_while_playing():
    catalogue = {e["effect"]: {p["name"]: p for p in e["params"]} for e in native.rack_effects()}
    assert catalogue["reverse"]["window"]["fixed_while_playing"] is True
    assert "fixed_while_playing" not in catalogue["reverse"]["fade"]
    assert "fixed_while_playing" not in catalogue["gain"]["db"]


def test_a_rack_takes_at_most_32_effects():
    assert len(from_dict(v2(*[{"effect": "gain"}] * 32)).rack) == 32
    with pytest.raises(ValueError, match="at most 32 effects, not 33"):
        from_dict(v2(*[{"effect": "gain"}] * 33))


def test_a_lane_takes_at_most_4096_points():
    def lane(n):
        points = [[i / 100, 0] for i in range(n)]
        return {"effect": "gain", "automation": [{"param": "db", "points": points}]}

    assert len(from_dict(v2(lane(4096))).rack[0].automation[0].points) == 4096
    with pytest.raises(ValueError, match=r"gain\.db: a lane takes at most 4096 points, not 4097"):
        from_dict(v2(lane(4097)))
