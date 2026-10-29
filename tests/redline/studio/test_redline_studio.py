import http.client
import io
import json
import threading

import pytest
import soundfile as sf
from redline.engine.render import Engine
from redline.engine.settings import Settings, from_dict
from redline.studio.server import Studio, make_server
from redline_signals import RATE
from redline_signals import engine_context as context
from redline_signals import engine_stems as stems

TOKEN = "t0k3n-for-tests"


@pytest.fixture
def studio(tmp_path):
    vocal, beat = stems(4.0)
    engine = Engine(vocal, beat, context(vocal))
    return Studio(engine, vocal + beat, tmp_path / "studio", "track-key")


@pytest.fixture
def server(studio):
    httpd = make_server(studio, TOKEN, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def call(server, method, path, body=None, token=TOKEN, host=None, headers=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    sent = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        sent["X-Token"] = token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    conn.request(method, path, body=data, headers=sent)
    response = conn.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def wav(data):
    samples, rate = sf.read(io.BytesIO(data), always_2d=True)
    return samples, rate


def test_the_page_needs_the_token_in_its_address(server):
    assert call(server, "GET", "/", token=None)[0] == 403
    status, headers, body = call(server, "GET", f"/?t={TOKEN}", token=None)
    assert status == 200 and b"<html" in body.lower()
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers


@pytest.mark.parametrize("token", [None, "wrong", TOKEN + "x", ""])
def test_the_api_refuses_a_missing_or_wrong_token(server, token):
    assert call(server, "GET", "/api/schema", token=token)[0] == 403


@pytest.mark.parametrize("host", ["evil.com", "127.0.0.1.evil.com", "127.0.0.1:1", "localhost"])
def test_a_foreign_host_is_refused(server, host):
    assert call(server, "GET", "/api/schema", host=host)[0] == 403


def test_localhost_with_the_right_port_is_let_in(server):
    port = server.server_address[1]
    assert call(server, "GET", "/api/schema", host=f"localhost:{port}")[0] == 200


@pytest.mark.parametrize(
    "path", ["/../pyproject.toml", "/static/../server.py", "/%2e%2e/x", "//etc/passwd", "/nope"]
)
def test_only_known_files_are_served(server, path):
    assert call(server, "GET", path)[0] == 404


def test_the_schema_carries_every_setting_and_the_track(server):
    status, _, body = call(server, "GET", "/api/schema")
    data = json.loads(body)
    assert status == 200
    assert {row["key"] for row in data["settings"]} == set(Settings.__dataclass_fields__)
    assert data["track"]["seconds"] == pytest.approx(4.0, abs=0.01)
    assert data["current"]["formant"] == 1.0


def test_a_preview_is_a_wav_of_the_loop(server):
    body = {"settings": {"formant": 1.2}, "start": 1.0, "end": 2.5, "seq": 1}
    status, headers, data = call(server, "POST", "/api/preview", body)
    samples, rate = wav(data)
    assert status == 200 and rate == RATE
    assert len(samples) == int(1.5 * RATE)
    assert float(headers["X-Original-Lufs"]) < 0


def test_an_exact_render_is_the_whole_track(server):
    status, headers, data = call(server, "POST", "/api/render", {"settings": {}, "seq": 1})
    samples, _ = wav(data)
    assert status == 200 and len(samples) == 4 * RATE
    assert float(headers["X-Render-Ms"]) >= 0


@pytest.mark.parametrize(
    "body",
    [
        {"settings": {"volume": 1}, "seq": 1},
        {"settings": {"formant": 9}, "seq": 1},
        {"settings": {}, "seq": "1"},
        {"settings": {}, "seq": 1, "start": 3.0, "end": 1.0},
        {"settings": {}, "seq": 1, "start": -1.0, "end": 1.0},
        {"settings": {}, "seq": 1, "start": 0.0, "end": 99.0},
    ],
)
def test_bad_requests_are_refused(server, body):
    path = "/api/preview" if "start" in body else "/api/render"
    assert call(server, "POST", path, body)[0] == 400


def test_a_post_must_be_json_and_small(server):
    plain = {"Content-Type": "text/plain"}
    assert call(server, "POST", "/api/render", b"{}", headers=plain)[0] == 415
    big = json.dumps({"settings": {}, "seq": 1, "pad": "x" * 70_000}).encode()
    try:
        status = call(server, "POST", "/api/render", big)[0]
    except (ConnectionResetError, BrokenPipeError):
        status = 413
    assert status == 413


def test_save_needs_an_exact_render_of_the_same_settings(server, studio):
    settings = {"formant": 1.35}
    assert call(server, "POST", "/api/save", {"settings": settings})[0] == 409
    call(server, "POST", "/api/render", {"settings": settings, "seq": 1})
    assert call(server, "POST", "/api/save", {"settings": {"formant": 1.2}})[0] == 409
    status, _, body = call(server, "POST", "/api/save", {"settings": settings})
    assert status == 200
    saved = studio.folder / "track-key" / "settings.json"
    assert json.loads(saved.read_text())["formant"] == 1.35
    assert json.loads(body)["path"] == str(saved)


def test_a_save_takes_no_path_from_the_page(server, studio):
    call(server, "POST", "/api/render", {"settings": {}, "seq": 1})
    body = {"settings": {}, "path": "/tmp/x.json"}
    assert call(server, "POST", "/api/save", body)[0] == 400


def test_presets_are_named_safely_and_listed(server, studio):
    good = {"name": "грязный 1", "settings": {"drive_db": 35}}
    assert call(server, "POST", "/api/preset", good)[0] == 200
    for name in ("../x", "a/b", "", "x" * 41):
        assert call(server, "POST", "/api/preset", {"name": name, "settings": {}})[0] == 400
    _, _, body = call(server, "GET", "/api/presets")
    assert json.loads(body) == {"грязный 1": {"drive_db": 35}}


def test_a_newer_request_cancels_an_older_one(studio):
    results = {}
    gate = threading.Event()
    original = studio.engine.render

    def slow(settings, cancelled=lambda: False):
        gate.wait(10)
        return original(settings, cancelled)

    studio.engine.render = slow
    first = threading.Thread(
        target=lambda: results.setdefault(1, studio.render(from_dict({"drive_db": 10.0}), 1))
    )
    first.start()
    while studio.latest_seq < 1:
        pass
    studio.latest_seq = 2
    gate.set()
    first.join(30)
    assert results[1] is None


def test_an_old_sequence_number_is_dropped_at_once(studio):
    assert studio.render(Settings(), 5) is not None
    assert studio.render(Settings(), 4) is None
