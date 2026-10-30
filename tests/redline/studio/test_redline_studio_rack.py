import json

import pytest
from redline.core import native
from redline.engine.settings import load, load_preset
from test_redline_studio import call, server, studio  # noqa: F401  (fixtures)

pytest.importorskip("redline_dsp")

QUIET = {"version": 2, "rack": [{"effect": "gain", "params": {"db": -12}}]}
LANE = {
    "version": 2,
    "rack": [
        {
            "effect": "gain",
            "automation": [{"param": "db", "points": [[0, 0], [2, -6]]}],
        }
    ],
}


def test_the_effects_list_is_served(server):  # noqa: F811
    status, _, body = call(server, "GET", "/api/effects")
    assert status == 200 and json.loads(body) == native.rack_effects()


def test_without_rust_the_effects_list_is_empty(server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(native, "redline_dsp", None)
    status, _, body = call(server, "GET", "/api/effects")
    assert status == 200 and json.loads(body) == []


def test_a_preview_and_a_render_take_the_rack(server):  # noqa: F811
    loudness = {}
    for name, settings in (("plain", {"master_on": False}), ("quiet", QUIET)):
        settings = {**settings, "master_on": False}
        body = {"settings": settings, "start": 1.0, "end": 3.0, "seq": len(loudness) + 1}
        status, headers, _ = call(server, "POST", "/api/preview", body)
        assert status == 200
        loudness[name] = float(headers["X-Lufs"])
    # the vocal 12 dB down under the same beat: the mix is quieter, by less than 12 dB
    assert -12 < loudness["quiet"] - loudness["plain"] < -1
    status, _, _ = call(server, "POST", "/api/render", {"settings": QUIET, "seq": 10})
    assert status == 200


@pytest.mark.parametrize(
    ("settings", "named"),
    [
        ({"version": 2, "rack": [{"effect": "flanger9"}]}, "flanger9"),
        ({"version": 2, "rack": [{"effect": "gain", "params": {"db": 99}}]}, "gain.db"),
        ({"rack": [{"effect": "gain"}]}, "version"),
    ],
)
def test_a_wrong_rack_is_a_bad_request_naming_it(server, settings, named):  # noqa: F811
    status, _, body = call(server, "POST", "/api/render", {"settings": settings, "seq": 1})
    assert status == 400 and named in json.loads(body)["error"]


def test_a_saved_rack_and_its_lanes_read_back_and_reopen_the_page(server, studio):  # noqa: F811
    call(server, "POST", "/api/render", {"settings": LANE, "seq": 1})
    assert call(server, "POST", "/api/save", {"settings": LANE})[0] == 200
    saved = studio.folder / "track-key" / "settings.json"
    data = json.loads(saved.read_text())
    assert data["version"] == 2
    assert data["rack"][0]["automation"] == LANE["rack"][0]["automation"]
    rack = load(saved).rack
    assert rack[0].automation[0].points == ((0.0, 0.0), (2.0, -6.0))
    _, _, body = call(server, "GET", "/api/schema")
    schema = json.loads(body)
    assert schema["current"]["version"] == 2
    assert schema["current"]["rack"] == data["rack"]
    assert "rack" not in {row["key"] for row in schema["settings"]}


def test_a_preset_keeps_its_rack_and_loads_by_name(server, studio):  # noqa: F811
    assert call(server, "POST", "/api/preset", {"name": "quiet", "settings": QUIET})[0] == 200
    _, _, body = call(server, "GET", "/api/presets")
    preset = json.loads(body)["quiet"]
    assert preset["version"] == 2 and preset["rack"][0]["params"] == {"db": -12.0}
    assert load_preset(studio.folder / "presets", "quiet").rack[0].effect == "gain"


def test_a_preset_without_a_rack_holds_only_what_changed(server):  # noqa: F811
    good = {"name": "dry", "settings": {"drive_db": 35}}
    assert call(server, "POST", "/api/preset", good)[0] == 200
    _, _, body = call(server, "GET", "/api/presets")
    assert json.loads(body)["dry"] == {"drive_db": 35}
