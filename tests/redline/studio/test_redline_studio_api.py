import io
import json
import threading

import numpy as np
import pytest
import soundfile as sf
from redline.core import native
from redline.core.loudness import integrated_lufs
from redline.engine import live
from redline.engine.settings import Settings, from_dict, schema, to_dict
from redline.studio.server import make_server
from redline_signals import RATE
from test_redline_studio import TOKEN, call, studio  # noqa: F401  (fixtures)

NOT_BUILT = "the studio page is not built: run `npm --prefix apps/studio run build`"
INDEX = b"<!doctype html><html><title>studio</title></html>"


def serve(studio, site):  # noqa: F811
    httpd = make_server(studio, TOKEN, port=0, site=site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / "dist"
    (root / "engine").mkdir(parents=True)
    (root / "index.html").write_bytes(INDEX)
    (root / "app.js").write_text("console.log(1)", encoding="utf-8")
    (root / "engine" / "redline_dsp.wasm").write_bytes(b"\0asm\1\0\0\0")
    (root / "secret.py").write_text("x = 1", encoding="utf-8")
    return root


@pytest.fixture
def server(studio, dist):  # noqa: F811
    httpd = serve(studio, dist)
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def bare(studio, tmp_path):  # noqa: F811
    httpd = serve(studio, tmp_path / "missing-dist")
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def as_json(value):
    return json.loads(json.dumps(value))


def expected_live(engine, settings):
    numbers = live.analysis(engine, settings)
    return as_json(numbers), as_json(live.racks(settings, numbers)), numbers


def test_the_session_carries_the_track_settings_and_the_live_numbers(server, studio):  # noqa: F811
    status, headers, body = call(server, "GET", "/api/session")
    assert status == 200 and headers["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["track"] == {
        "name": "track-key",
        "rate": RATE,
        "frames": 4 * RATE,
        "seconds": pytest.approx(4.0),
    }
    assert data["settings"] == to_dict(Settings())
    assert data["schema"]["settings"] == as_json(schema()["settings"])
    assert data["schema"]["groups"] == as_json(schema()["groups"])
    assert data["schema"]["effects"] == (native.rack_effects() or [])
    numbers, racks, _ = expected_live(studio.engine, Settings())
    assert data["analysis"] == numbers
    assert data["racks"] == racks
    assert set(data["racks"]) == {"vocal", "beat", "master"}
    assert data["stale"] == {k: sorted(v) for k, v in live.STALE_AFTER.items()}
    assert data["stale"]["pre_gain_db"] == []
    assert data["presets"] == {}
    [ref] = data["references"]
    assert ref["index"] == 0 and ref["name"] == "ref one"
    assert ref["seconds"] == pytest.approx(3.0, abs=0.01) and ref["lufs"] < 0


def test_the_session_starts_from_the_saved_settings_and_lists_presets(server, studio):  # noqa: F811
    saved = {**to_dict(Settings()), "formant": 1.2, "comp_threshold_db": -30.0}
    (studio.folder / "track-key").mkdir(parents=True)
    (studio.folder / "track-key" / "settings.json").write_text(json.dumps(saved))
    assert (
        call(server, "POST", "/api/preset", {"name": "p", "settings": {"drive_db": 12}})[0] == 200
    )
    data = json.loads(call(server, "GET", "/api/session")[2])
    assert data["settings"] == saved
    settings = from_dict(saved)
    numbers, racks, _ = expected_live(studio.engine, settings)
    assert data["analysis"] == numbers and data["analysis"]["settings"] == saved
    assert data["racks"] == racks
    assert data["presets"] == {"p": {"drive_db": 12}}


@pytest.mark.parametrize("name", ["vocal", "beat", "original"])
def test_a_stem_is_float_wav_of_the_whole_track(server, studio, name):  # noqa: F811
    status, headers, data = call(server, "GET", f"/api/stem?name={name}")
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    info = sf.info(io.BytesIO(data))
    assert info.subtype == "FLOAT" and info.samplerate == RATE and info.channels == 2
    samples, _ = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    engine = studio.engine
    want = {"vocal": engine.vocal, "beat": engine.beat, "original": engine.vocal + engine.beat}
    assert samples.shape == (4 * RATE, 2)
    np.testing.assert_array_equal(samples, want[name])
    assert float(headers["X-Lufs"]) == pytest.approx(integrated_lufs(want[name], RATE), abs=0.01)


def test_a_stem_louder_than_full_scale_is_not_clipped(server, studio):  # noqa: F811
    studio.engine.vocal[:10] = 1.5
    data = call(server, "GET", "/api/stem?name=vocal")[2]
    samples, _ = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    assert samples[0, 0] == pytest.approx(1.5)


@pytest.mark.parametrize(
    "query", ["name=mix", "name=", "", "name=vocal&name=beat", "name=VOCAL", "name=../vocal"]
)
def test_an_unknown_stem_is_refused(server, query):
    assert call(server, "GET", f"/api/stem?{query}")[0] == 400


@pytest.mark.parametrize(
    "body",
    [
        {"settings": {"volume": 1}},
        {"settings": {"formant": 9}},
        {"settings": [1]},
        {},
        {"settings": {}, "seq": 1},
    ],
)
def test_the_analysis_refuses_bad_settings(server, body):
    status, _, data = call(server, "POST", "/api/analysis", body)
    assert status == 400 and json.loads(data)["error"]


def test_the_analysis_is_fresh_after_a_stale_setting_changes(server, studio):  # noqa: F811
    first = json.loads(call(server, "POST", "/api/analysis", {"settings": {}})[2])
    changed = {"comp_threshold_db": -20.0}
    assert "makeup_db" in live.stale(first["analysis"], from_dict(changed))
    status, _, body = call(server, "POST", "/api/analysis", {"settings": changed})
    second = json.loads(body)
    assert status == 200 and set(second) == {"analysis", "racks", "lanes"}
    numbers, racks, raw = expected_live(studio.engine, from_dict(changed))
    assert second["analysis"] == numbers and second["racks"] == racks
    assert second["lanes"] == as_json(live.vocal_lanes(from_dict(changed), raw)) == []
    assert second["analysis"]["makeup_db"] != pytest.approx(first["analysis"]["makeup_db"])
    assert second["analysis"]["settings"]["comp_threshold_db"] == -20.0
    # a setting no number depends on leaves the track's own numbers as they were
    third = json.loads(call(server, "POST", "/api/analysis", {"settings": {"formant": 1.2}})[2])
    for name in ("pre_gain_db", "gate_db", "makeup_db", "peak_db", "eq_fit_db", "post_gain_db"):
        assert third["analysis"][name] == first["analysis"][name]


def test_the_analysis_places_the_user_racks_lanes(server, studio):  # noqa: F811
    pytest.importorskip("redline_dsp")
    lane = {"param": "db", "points": [[0, 0], [2, -6]]}
    settings = {"version": 2, "rack": [{"effect": "gain", "automation": [lane]}]}
    status, _, body = call(server, "POST", "/api/analysis", {"settings": settings})
    data = json.loads(body)
    assert status == 200
    _, _, raw = expected_live(studio.engine, from_dict(settings))
    slot = len(json.loads(live.racks(from_dict(settings), raw)["vocal"])["effects"]) - 1
    assert data["lanes"] == [[slot, "db", [[0.0, 0.0], [2.0, -6.0]]]]


def test_a_missing_page_says_how_to_build_it(bare):
    assert call(bare, "GET", "/", token=None)[0] == 403
    status, headers, body = call(bare, "GET", f"/?t={TOKEN}", token=None)
    assert status == 503 and body.decode() == NOT_BUILT
    assert headers["Content-Type"].startswith("text/plain")
    # the API works without the page
    assert call(bare, "GET", "/api/stem?name=beat")[0] == 200


def test_a_page_built_after_the_start_is_served_on_reload(bare, tmp_path):
    assert call(bare, "GET", f"/?t={TOKEN}", token=None)[0] == 503
    site = tmp_path / "missing-dist"
    site.mkdir()
    (site / "index.html").write_bytes(INDEX)
    status, _, body = call(bare, "GET", f"/?t={TOKEN}", token=None)
    assert status == 200 and body == INDEX


def test_the_built_page_is_served_behind_the_token(server):
    assert call(server, "GET", "/", token=None)[0] == 403
    assert call(server, "GET", "/?t=wrong", token=None)[0] == 403
    status, headers, body = call(server, "GET", f"/?t={TOKEN}", token=None)
    assert status == 200 and body == INDEX
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers
    status, headers, body = call(server, "GET", "/engine/redline_dsp.wasm", token=None)
    assert status == 200 and headers["Content-Type"] == "application/wasm"
    assert body == b"\0asm\1\0\0\0"
    assert call(server, "GET", "/app.js", token=None)[0] == 200
    for path in ("/secret.py", "/../index.html", "/engine/../../dist/index.html", "/nope.js"):
        assert call(server, "GET", path, token=None)[0] == 404


def test_the_old_page_is_no_longer_served(server):
    assert call(server, "GET", "/js/main.js", token=None)[0] == 404
    assert call(server, "GET", "/css/deck.css", token=None)[0] == 404


NEW_CALLS = [
    ("GET", "/api/session", None),
    ("GET", "/api/stem?name=vocal", None),
    ("POST", "/api/analysis", {"settings": {}}),
]


@pytest.mark.parametrize(("method", "path", "body"), NEW_CALLS)
@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x"])
def test_the_new_calls_need_the_token(server, method, path, body, token):
    status, _, data = call(server, method, path, body, token=token)
    assert status == 403 and b"RIFF" not in data


@pytest.mark.parametrize(("method", "path", "body"), NEW_CALLS)
@pytest.mark.parametrize("host", ["evil.com", "127.0.0.1.evil.com", "127.0.0.1:1", "localhost"])
def test_the_new_calls_refuse_a_foreign_host(server, method, path, body, host):
    assert call(server, method, path, body, host=host)[0] == 403


@pytest.mark.parametrize(
    "raw",
    [b'{"settings": {"vocal_db": 1' + b"0" * 400 + b"}}", b"[" * 60_000],
    ids=["huge number", "deep nesting"],
)
def test_the_analysis_answers_400_to_json_python_cannot_hold(server, raw):
    status, _, data = call(server, "POST", "/api/analysis", raw)
    assert status == 400 and json.loads(data)["error"]


def test_the_analysis_takes_only_small_json(server):
    plain = {"Content-Type": "text/plain"}
    assert call(server, "POST", "/api/analysis", b"{}", headers=plain)[0] == 415
    big = json.dumps({"settings": {}, "pad": "x" * 70_000}).encode()
    try:
        status = call(server, "POST", "/api/analysis", big)[0]
    except (ConnectionResetError, BrokenPipeError):
        status = 413
    assert status == 413


def policy(headers):
    rules = [rule.split() for rule in headers["Content-Security-Policy"].split(";")]
    return {rule[0]: rule[1:] for rule in rules if rule}


@pytest.mark.parametrize("path", [f"/?t={TOKEN}", "/app.js", "/api/stem?name=beat"])
def test_the_policy_lets_wasm_compile_and_nothing_external_but_fonts(server, path):
    rules = policy(call(server, "GET", path)[1])
    assert rules["default-src"] == ["'self'"]
    assert sorted(rules["script-src"]) == ["'self'", "'wasm-unsafe-eval'"]
    assert rules["worker-src"] == ["'self'"]
    sources = {s for values in rules.values() for s in values}
    assert "'unsafe-eval'" not in sources and "'unsafe-inline'" not in sources
    assert {s for s in sources if "." in s} == {
        "https://fonts.googleapis.com",
        "https://fonts.gstatic.com",
    }
