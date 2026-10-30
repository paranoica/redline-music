import http.client
import io
import json
import threading
import time
from pathlib import Path

import numpy as np
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
    ref = (0.3 * vocal[: RATE * 3]).astype("float32")
    return Studio(engine, vocal + beat, tmp_path / "studio", "track-key", [("ref one", ref, RATE)])


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
    # the rack has its own card, built from /api/effects
    plain = set(Settings.__dataclass_fields__) - {"rack"}
    assert {row["key"] for row in data["settings"]} == plain
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


def test_a_broken_preset_or_settings_file_does_not_break_the_page(server, studio):
    (studio.folder / "presets").mkdir(parents=True)
    (studio.folder / "presets" / "broken.json").write_text("{not json", encoding="utf-8")
    (studio.folder / "presets" / "list.json").write_text("[1, 2]", encoding="utf-8")
    (studio.folder / "presets" / "fine.json").write_text('{"drive_db": 20}', encoding="utf-8")
    status, _, body = call(server, "GET", "/api/presets")
    assert status == 200 and json.loads(body) == {"fine": {"drive_db": 20}}
    (studio.folder / "track-key").mkdir(parents=True)
    (studio.folder / "track-key" / "settings.json").write_text("[1]", encoding="utf-8")
    status, _, body = call(server, "GET", "/api/schema")
    assert status == 200 and json.loads(body)["current"]["formant"] == 1.0


def test_the_browser_opens_a_private_file_not_an_address_with_the_token(tmp_path):
    import stat

    from redline.web.local import browser_file

    url = f"http://127.0.0.1:5555/?t={TOKEN}"
    old = tmp_path / "studio" / "open.html"
    old.parent.mkdir()
    old.write_text("old", encoding="utf-8")
    old.chmod(0o644)
    path = browser_file(url, tmp_path / "studio")
    assert path == old and TOKEN in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert TOKEN not in path.as_uri()


def test_an_idle_connection_is_dropped(studio, monkeypatch):
    import socket

    from redline.studio import server as studio_server

    assert studio_server.Handler.timeout is not None and studio_server.Handler.timeout <= 30
    monkeypatch.setattr(studio_server.Handler, "timeout", 0.5)
    httpd = make_server(studio, TOKEN, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(httpd.server_address, timeout=5) as idle:
            assert idle.recv(1) == b""
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_length_that_is_not_ascii_digits_is_refused(server):
    status = call(server, "POST", "/api/render", b"{}", headers={"Content-Length": "²"})[0]
    assert status in (400, 411)


def test_a_preview_does_not_wait_for_a_whole_render(studio):
    gate = threading.Event()
    original = studio.engine.render

    def slow(settings, cancelled=lambda: False):
        gate.wait(20)
        return original(settings, cancelled)

    studio.engine.render = slow
    whole = threading.Thread(target=lambda: studio.render(Settings(), 1))
    whole.start()
    try:
        done = []
        preview = threading.Thread(
            target=lambda: done.append(studio.preview(Settings(), 2, 0, RATE))
        )
        preview.start()
        preview.join(15)
        assert done and done[0] is not None and len(done[0]) == RATE
    finally:
        gate.set()
        whole.join(30)


def test_any_whole_render_made_can_be_saved(studio):
    first, second = from_dict({"formant": 1.2}), from_dict({"drive_db": 20.0})
    assert studio.render(first, 1) is not None
    assert studio.render(second, 2) is not None
    assert studio.save(first).exists()
    assert studio.save(second).exists()


def test_the_references_are_listed_and_served_by_index(server):
    status, _, body = call(server, "GET", "/api/references")
    listed = json.loads(body)
    assert status == 200 and [r["name"] for r in listed] == ["ref one"]
    assert listed[0]["seconds"] == pytest.approx(3.0, abs=0.01) and listed[0]["lufs"] < 0
    status, headers, data = call(server, "GET", "/api/reference?i=0")
    samples, rate = wav(data)
    assert status == 200 and rate == RATE and len(samples) == 3 * RATE
    assert float(headers["X-Lufs"]) == pytest.approx(listed[0]["lufs"], abs=0.01)


@pytest.mark.parametrize(
    "query", ["i=1", "i=-1", "i=x", "", "i=0&i=1", "i=00000000000000000000001"]
)
def test_a_reference_index_outside_the_list_is_refused(server, query):
    assert call(server, "GET", f"/api/reference?{query}")[0] == 400


def test_references_need_the_token(server):
    assert call(server, "GET", "/api/reference?i=0", token=None)[0] == 403


def test_a_reference_span_has_its_own_loudness(server, studio):
    from redline.core.loudness import integrated_lufs

    status, _, body = call(server, "GET", "/api/reference_span?i=0&start=0.5&end=2.5")
    _, samples, rate, _ = studio.references[0]
    want = integrated_lufs(samples[int(0.5 * rate) : int(2.5 * rate)], rate)
    assert status == 200 and json.loads(body)["lufs"] == pytest.approx(want, abs=0.01)
    for query in (
        "i=0&start=2&end=1",
        "i=0&start=-1&end=1",
        "i=0&start=0&end=9",
        "i=3&start=0&end=1",
        "i=0&start=x&end=1",
    ):
        assert call(server, "GET", f"/api/reference_span?{query}")[0] == 400


def test_the_browser_file_opens_from_a_relative_folder(tmp_path, monkeypatch):
    from redline.web.local import browser_file

    monkeypatch.chdir(tmp_path)
    path = browser_file("http://127.0.0.1:5555/", Path("work") / "studio")
    assert path.as_uri().startswith("file:///")
    assert path == tmp_path / "work" / "studio" / "open.html"


@pytest.mark.parametrize("classic", [False, True])
def test_the_exact_render_goes_through_the_racks_unless_classic(studio, monkeypatch, classic):
    pytest.importorskip("redline_dsp")
    from redline.engine import racks_render

    calls = []
    real = racks_render.render

    def spy(engine, settings, *args):
        calls.append(settings)
        return real(engine, settings, *args)

    monkeypatch.setattr(racks_render, "render", spy)
    studio.classic = classic
    rendered = studio.render(Settings(), 1)
    assert rendered is not None
    assert len(calls) == (0 if classic else 1)
    classic_final = studio.engine.render(Settings()).final
    assert bool(np.array_equal(rendered.final, classic_final)) is classic


def test_an_analysis_asked_for_after_another_drops_the_waiting_and_cancels_the_running(
    studio, monkeypatch
):
    from redline.studio import server as studio_server

    entered, release = threading.Event(), threading.Event()
    ran, outcomes = [], {}

    def fake(engine, settings, cancelled=lambda: False):
        ran.append(settings.drive_db)
        if len(ran) == 1:
            entered.set()
            release.wait(10)
            return {"cancelled": cancelled()}
        return {"cancelled": cancelled()}

    monkeypatch.setattr(studio_server.live, "analysis", fake)

    def ask(drive):
        try:
            outcomes[drive] = studio.analysis(from_dict({"drive_db": drive}))
        except studio_server.BadRequest as error:
            outcomes[drive] = error.status

    first = threading.Thread(target=ask, args=(10.0,))
    first.start()
    entered.wait(10)
    waiting = threading.Thread(target=ask, args=(12.0,))
    waiting.start()
    while studio._analysis_asked < 2:
        pass
    ask_last = threading.Thread(target=ask, args=(14.0,))
    ask_last.start()
    while studio._analysis_asked < 3:
        pass
    release.set()
    for thread in (first, waiting, ask_last):
        thread.join(30)
    assert outcomes[10.0] == {"cancelled": True}
    assert outcomes[12.0] == 409
    assert outcomes[14.0] == {"cancelled": False}
    assert ran == [10.0, 14.0]


@pytest.mark.parametrize("session_first", [False, True])
def test_the_session_and_an_analysis_do_not_cancel_each_other(
    server, studio, monkeypatch, session_first
):
    from redline.studio import server as studio_server

    real = studio_server.live.analysis
    entered, release = threading.Event(), threading.Event()
    seen = []

    def held(engine, settings, cancelled=lambda: False):
        if not seen:
            entered.set()
            release.wait(10)
        seen.append(settings.drive_db)
        return real(engine, settings, cancelled)

    monkeypatch.setattr(studio_server.live, "analysis", held)
    outcomes = {}

    def session():
        outcomes["session"] = call(server, "GET", "/api/session")[0]

    def analysis():
        try:
            studio.analysis(from_dict({"drive_db": 10.0}))
            outcomes["analysis"] = 200
        except studio_server.BadRequest as error:
            outcomes["analysis"] = error.status

    first, second = (session, analysis) if session_first else (analysis, session)
    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    threads[0].start()
    assert entered.wait(30)
    threads[1].start()
    # the second is in: it waits for the lock the first one holds
    time.sleep(0.5)
    release.set()
    for thread in threads:
        thread.join(120)
    assert outcomes == {"session": 200, "analysis": 200}
    assert len(seen) == 2


def test_a_silent_vocal_is_named_before_the_studio_opens():
    from redline.engine import live

    vocal, beat = stems(4.0)
    with pytest.raises(ValueError, match="vocal stem is silent"):
        live.check_vocal(Engine(np.zeros_like(vocal), beat, context(vocal)))
    live.check_vocal(Engine(vocal, beat, context(vocal)))
