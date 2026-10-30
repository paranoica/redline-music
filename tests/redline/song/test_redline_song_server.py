import http.client
import io
import json
import shlex
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.cli import main
from redline.engine.render import Engine
from redline.record.session import Outcome
from redline.song import model
from redline.song.rec import NotReady
from redline.song.server import SongApp, close_all, job_argv, make_server
from redline.studio.server import Studio
from redline_signals import engine_context, engine_stems

TOKEN = "song-t0k3n-for-tests"
NAME = "demo"
WRITE_RANK = (
    "import json, os, pathlib, sys; r = pathlib.Path(sys.argv[1]); "
    "(r.parent.parent / 'ranking.json').write_text(json.dumps({'takes': [{'name': 'take-01'}]})); "
    "r.write_text(json.dumps({'kind': 'rank'}))"
)


def tone(seconds=1.0):
    t = np.arange(int(seconds * 8000)) / 8000
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")


class FakeRec:
    def __init__(self, logged, work):
        self.logged = logged
        self.calls = []
        self.ready = False
        self.is_taking = False
        self.down = False

    def snapshot(self):
        from redline.song.rec import RecSnapshot

        stage = "ready" if self.ready else "idle"
        return RecSnapshot(
            stage,
            None,
            False,
            False,
            self.is_taking,
            None,
            0.0,
            0.5,
            0.0,
            0,
            0,
            0,
            0,
            48000,
            64,
            11.6,
            (),
            0.0,
            ("round trip 100 frames (calibrated)", "calibrated"),
        )

    def taking(self):
        return self.is_taking

    def open(self, request):
        self.calls.append(("open", request))
        self.ready = True

    def toggle(self):
        if not self.ready:
            raise NotReady("the engine is not ready (idle)")
        self.calls.append(("toggle",))
        return Outcome("recording", {"take": "take-02"})

    def discard_last(self):
        return Outcome("nothing_to_discard")

    def close(self):
        self.ready = False

    def shutdown(self):
        self.down = True


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    sf.write(base / "input" / "beat.wav", tone(2.0), 8000)
    sf.write(base / "input" / "ref.wav", tone(), 8000)
    takes = base / "input" / "takes" / NAME
    takes.mkdir(parents=True)
    sf.write(takes / "take-01.wav", tone(), 8000)
    sf.write(base / "input" / "outside.wav", tone(), 8000)
    return base


def fake_studio(inputs, work, name):
    vocal, beat = engine_stems(2.0)
    return Studio(Engine(vocal, beat, engine_context(vocal)), vocal + beat, work / "studio", name)


@pytest.fixture
def made(root, tmp_path):
    model.save(model.new_song(NAME, {"file": "input/beat.wav"}, None, ("input/ref.wav",)), 0)
    built = []

    def studio(inputs, work, name):
        built.append(inputs)
        return fake_studio(inputs, work, name)

    def argv(kind, name, seed, result):
        return [sys.executable, "-c", WRITE_RANK, str(result)]

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "song.html").write_bytes(b"<!doctype html><title>song</title>")
    (dist / "song.js").write_text("1", encoding="utf-8")
    app = SongApp(
        NAME,
        make_rec=FakeRec,
        argv=argv,
        build_studio=studio,
        assets=lambda: (None, None, str),
        kill_after=0.3,
    )
    app.built = built
    httpd = make_server(app, TOKEN, port=0, site=dist)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    app.jobs.close(timeout=5)
    httpd.server_close()


def call(server, method, path, body=None, token=TOKEN, host=None, raw=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    sent = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        sent["X-Token"] = token
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        sent["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=sent)
    response = conn.getresponse()
    return response.status, dict(response.getheaders()), response.read()


def api(server, method, path, body=None):
    status, _, data = call(server, method, path, body)
    return status, json.loads(data)


def until(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


GETS = [
    "/api/song",
    "/api/text",
    "/api/rec/state",
    "/api/takes",
    "/api/takes/audio?name=take-01.wav",
    "/api/jobs",
    "/api/mix/schema",
    "/api/mix/variants",
    "/api/beat/session",
]
POSTS = [
    "/api/song",
    "/api/text/save",
    "/api/rec/open",
    "/api/rec/toggle",
    "/api/rec/discard",
    "/api/rec/calibrate",
    "/api/rec/close",
    "/api/jobs/start",
    "/api/jobs/cancel",
    "/api/mix/render",
    "/api/beat/check",
]


@pytest.mark.parametrize("path", GETS)
def test_every_get_refuses_a_missing_token_and_a_foreign_host(made, path):
    assert call(made, "GET", path, token=None)[0] == 403
    assert call(made, "GET", path, token="wrong")[0] == 403
    assert call(made, "GET", path, host="evil.com")[0] == 403
    assert call(made, "GET", path, host="127.0.0.1:1")[0] == 403


@pytest.mark.parametrize("path", POSTS)
def test_every_post_refuses_a_missing_token_and_a_foreign_host(made, path):
    assert call(made, "POST", path, {}, token=None)[0] == 403
    assert call(made, "POST", path, {}, token="wrong")[0] == 403
    assert call(made, "POST", path, {}, host="evil.com")[0] == 403


def test_the_page_carries_the_studio_policy_and_needs_the_token(made):
    status, headers, body = call(made, "GET", f"/?t={TOKEN}", token=None)
    assert status == 200 and b"<title>song</title>" in body
    assert "wasm-unsafe-eval" in headers["Content-Security-Policy"]
    assert "media-src 'self' blob:" in headers["Content-Security-Policy"]
    assert call(made, "GET", "/", token=None)[0] == 403
    assert call(made, "GET", "/song.js", token=None)[0] == 200
    assert call(made, "GET", "/secret.py", token=None)[0] == 404


def test_the_song_comes_with_its_stations(made):
    status, data = api(made, "GET", "/api/song")
    assert status == 200 and data["song"]["rev"] == 1
    st = data["stations"]
    assert st["beat"]["kind"] == "file" and st["beat"]["file"] == "input/beat.wav"
    assert st["text"]["seed"] is None and "lyrics write" in st["text"]["command"]
    assert st["voice"] == {
        "kind": "takes",
        "folder": f"input/takes/{NAME}",
        "takes": ["take-01.wav"],
        "chosen": None,
    }
    assert st["mix"] == {"ready": False, "reason": "no voice chosen", "variant": None}
    assert st["result"] == {"folder": None}


def test_a_stale_rev_is_412_with_the_song_and_changes_nothing(made):
    assert (
        api(made, "POST", "/api/song", {"rev": 1, "set": {"voice_chosen": "take-01.wav"}})[0] == 200
    )
    status, data = api(made, "POST", "/api/song", {"rev": 1, "set": {"mix_variant": None}})
    assert status == 412 and data["code"] == "stale"
    assert data["song"]["rev"] == 2 and data["song"]["voice"]["chosen"] == "take-01.wav"
    assert model.load(NAME).rev == 2


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"voice_chosen": "take-09.wav"}, 404),
        ({"voice_chosen": "../outside.wav"}, 404),
        ({"voice_chosen": "notes.txt"}, 404),
        ({"text": {"seed": 3, "version": 1}}, 422),
        ({"beat": {"file": "../x.wav"}}, 400),
        ({"beat": {"own": -1}}, 400),
        ({"other": 1}, 400),
        ({}, 400),
        ({"mix_variant": 2}, 422),
    ],
)
def test_a_bad_change_is_refused_and_the_song_stays(made, changes, status):
    assert api(made, "POST", "/api/song", {"rev": 1, "set": changes})[0] == status
    assert model.load(NAME).rev == 1


def test_takes_audio_is_a_16_bit_wav_of_a_take_only(made, root):
    status, headers, body = call(made, "GET", "/api/takes/audio?name=take-01.wav")
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    info = sf.info(io.BytesIO(body))
    assert info.subtype == "PCM_16" and info.samplerate == 8000
    takes = root / "input" / "takes" / NAME
    (takes / "take-02.wav").symlink_to(root / "input" / "outside.wav")
    (takes / "notes.wav").write_bytes(b"x")
    for name in (
        "take-02.wav",
        "notes.wav",
        "take-01.mp3",
        "../beat.wav",
        "..%2Fbeat.wav",
        "take-09.wav",
        "",
    ):
        assert call(made, "GET", f"/api/takes/audio?name={name}")[0] == 404, name
    assert call(made, "GET", "/api/takes/audio")[0] == 404


def test_a_text_save_without_the_index_is_422_with_the_command(made, root):
    seed = root / "private" / "lyrics" / "generated" / "3"
    seed.mkdir(parents=True)
    (seed / "text.txt").write_text("a line\n", encoding="utf-8")
    status, data = api(made, "POST", "/api/text/save", {"seed": 3, "base": 1, "text": "x"})
    assert status == 400
    assert (
        api(made, "POST", "/api/song", {"rev": 1, "set": {"text": {"seed": 3, "version": 1}}})[0]
        == 200
    )
    status, data = api(made, "POST", "/api/text/save", {"seed": 3, "base": 1, "text": "x"})
    assert status == 422 and data["errors"][0]["kind"] == "no index"
    assert "redline lyrics index" in data["errors"][0]["message"]
    assert not (seed / "text-2.txt").exists()
    status, data = api(made, "GET", "/api/text")
    assert status == 200 and data["text"] == "a line\n" and data["seed"] == 3


def test_the_mix_is_409_no_voice_until_a_voice_is_chosen_and_separated(made, root):
    for path in ("/api/mix/session", "/api/mix/schema"):
        status, data = api(made, "GET", path)
        assert status == 409 and data["code"] == "no_voice" and data["reason"] == "no voice chosen"
    api(made, "POST", "/api/song", {"rev": 1, "set": {"voice_chosen": "take-01.wav"}})
    status, data = api(made, "GET", "/api/mix/schema")
    assert status == 409 and data["code"] == "no_voice" and data["reason"] == "stems not ready"
    assert api(made, "GET", "/api/song")[1]["stations"]["mix"]["reason"] == "stems not ready"


def ready_mix(made, root):
    api(made, "POST", "/api/song", {"rev": 1, "set": {"voice_chosen": "take-01.wav"}})
    app = made.app
    inputs = model.mix_inputs(model.load(NAME))
    marker = app._result_path("separate")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"key": inputs.key()}), encoding="utf-8")


def test_a_separated_voice_builds_the_studio_once_and_a_new_voice_swaps_it(made, root):
    ready_mix(made, root)
    status, data = api(made, "GET", "/api/mix/schema")
    assert status == 200 and "settings" in data
    api(made, "GET", "/api/mix/schema")
    assert len(made.app.built) == 1
    first = made.app._studio
    takes = root / "input" / "takes" / NAME
    sf.write(takes / "take-02.wav", tone(), 8000)
    assert (
        api(made, "POST", "/api/song", {"rev": 2, "set": {"voice_chosen": "take-02.wav"}})[0] == 200
    )
    # the new take has no separation yet
    assert api(made, "GET", "/api/mix/schema")[1]["reason"] == "stems not ready"
    inputs = model.mix_inputs(model.load(NAME))
    made.app._result_path("separate").write_text(
        json.dumps({"key": inputs.key()}), encoding="utf-8"
    )
    assert api(made, "GET", "/api/mix/schema")[0] == 200
    assert len(made.app.built) == 2 and made.app._studio is not first
    assert first.latest_seq == sys.maxsize


def test_a_swap_is_refused_while_the_studio_renders_and_keeps_it(made, root):
    ready_mix(made, root)
    assert api(made, "GET", "/api/mix/schema")[0] == 200
    old = made.app._studio
    sf.write(root / "input" / "takes" / NAME / "take-02.wav", tone(), 8000)
    api(made, "POST", "/api/song", {"rev": 2, "set": {"voice_chosen": "take-02.wav"}})
    made.app._result_path("separate").write_text(
        json.dumps({"key": model.mix_inputs(model.load(NAME)).key()}), encoding="utf-8"
    )
    with old._render_lock:
        status, data = api(made, "GET", "/api/mix/schema")
    assert status == 409 and data["code"] == "busy"
    assert made.app._studio is old and old.latest_seq != sys.maxsize
    assert api(made, "GET", "/api/mix/schema")[0] == 200


def test_the_mix_routes_are_the_studios(made, root):
    ready_mix(made, root)
    status, data = api(made, "GET", "/api/mix/session")
    assert status == 200 and data["track"]["name"] == NAME
    assert api(made, "GET", "/api/mix/nothing")[0] == 404
    assert api(made, "GET", "/api/mix/variants")[0] == 404


def test_keeping_a_variant_stores_its_settings_and_none_clears_the_slot(made, root):
    state = made.app._state_dir()
    variants = {
        "auto": {"formant": 1.0, "distance_before": 3.0, "distance_after": 1.0, "at_edge": []},
        "variants": [{"settings": {"formant": 1.2}, "axis": "formant", "distance": 2.0}],
        "seed": 7,
    }
    (state / "variants.json").write_text(json.dumps(variants), encoding="utf-8")
    assert api(made, "GET", "/api/mix/variants")[1]["seed"] == 7
    assert api(made, "POST", "/api/song", {"rev": 1, "set": {"mix_variant": 1}})[0] == 200
    song = model.load(NAME)
    assert song.mix == {"settings": f"private/songs/{NAME}/mix.json", "variant": 1}
    mix = root / "private" / "songs" / NAME / "mix.json"
    assert json.loads(mix.read_text())["formant"] == 1.2
    assert mix.stat().st_mode & 0o777 == 0o600
    assert api(made, "POST", "/api/song", {"rev": 2, "set": {"mix_variant": None}})[0] == 200
    assert model.load(NAME).mix["variant"] is None and model.load(NAME).mix["settings"]


def test_recording_goes_through_the_owner(made, root):
    status, data = api(made, "POST", "/api/rec/toggle", {})
    assert status == 409 and data["code"] == "not_ready"
    status, _ = api(made, "POST", "/api/rec/open", {})
    assert status == 202
    request = made.app.rec.calls[0][1]
    assert request.beat == (root / "input" / "beat.wav").resolve()
    assert request.folder == (root / "input" / "takes" / NAME).resolve()
    assert request.refs == ((root / "input" / "ref.wav").resolve(),)
    status, data = api(made, "POST", "/api/rec/toggle", {})
    assert status == 200 and data == {"code": "recording", "values": {"take": "take-02"}}
    state = api(made, "GET", "/api/rec/state")[1]
    assert state["stage"] == "ready" and state["round_trip"]["source"] == "calibrated"
    assert api(made, "POST", "/api/rec/calibrate", {"mode": "air"})[0] == 400


def test_a_beat_change_while_a_take_runs_is_409(made):
    made.app.rec.is_taking = True
    status, data = api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"own": 4}}})
    assert status == 409 and data["code"] == "take_running"
    assert model.load(NAME).beat == {"file": "input/beat.wav"}
    made.app.rec.is_taking = False
    assert api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"own": 4}}})[0] == 200


def test_a_suno_voice_is_not_recorded(made, root):
    model.save(model.new_song("sun", {"own": 1}, "input/beat.wav", ("input/ref.wav",)), 0)
    made.app.name = "sun"
    status, data = api(made, "POST", "/api/rec/open", {})
    assert status == 409 and data["code"] == "no_recording"


def test_a_logged_take_starts_a_rank_job_whose_result_shows_in_takes(made, root):
    assert api(made, "GET", "/api/takes")[1] == {"ranking": None, "job": None}
    made.app.rec.logged(root / "input" / "takes" / NAME / "take-01.wav")
    assert until(lambda: api(made, "GET", "/api/takes")[1]["job"]["state"] == "done")
    data = api(made, "GET", "/api/takes")[1]
    assert data["ranking"] == {"takes": [{"name": "take-01"}]}
    jobs = api(made, "GET", "/api/jobs")[1]["jobs"]
    assert [j["kind"] for j in jobs] == ["rank"]


def test_a_job_start_needs_its_inputs_and_a_second_one_is_busy(made):
    status, data = api(made, "POST", "/api/jobs/start", {"kind": "auto", "seed": 3})
    assert status == 409 and data["code"] == "no_voice"
    assert api(made, "POST", "/api/jobs/start", {"kind": "tea"})[0] == 400
    assert api(made, "POST", "/api/jobs/start", {"kind": "auto", "seed": -1})[0] == 400
    assert api(made, "POST", "/api/jobs/cancel", {"kind": "rank"})[0] == 404
    made.app._argv = lambda kind, name, seed, result: [
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
    ]
    assert api(made, "POST", "/api/jobs/start", {"kind": "rank"})[0] == 202
    status, data = api(made, "POST", "/api/jobs/start", {"kind": "rank"})
    assert status == 409 and data["code"] == "busy"
    assert api(made, "POST", "/api/jobs/cancel", {"kind": "rank"})[0] == 200
    assert until(lambda: api(made, "GET", "/api/jobs")[1]["jobs"][0]["state"] == "cancelled")


def test_job_results_are_private(made, root):
    made.app.rec.logged(root / "x")
    assert until(lambda: api(made, "GET", "/api/takes")[1]["job"]["state"] == "done")
    state = root / "work" / "songs" / NAME
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / "jobs").stat().st_mode & 0o777 == 0o700


def test_the_beat_routes_follow_the_song_and_a_file_beat_has_none(made):
    status, data = api(made, "GET", "/api/beat/session")
    assert status == 409 and data["code"] == "no_beat_seed"


def test_the_beat_studio_swaps_on_a_seed_change_unless_it_renders(made, root):
    running = {"state": "idle"}

    class Beat:
        def __init__(self, seed, beats, work):
            self.seed = seed

        def session(self):
            return {"seed": self.seed}

        def render_status(self):
            return {**running, "error": None}

    made.app._make_beat = Beat
    api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"own": 1}}})
    assert api(made, "GET", "/api/beat/session")[1] == {"seed": 1}
    api(made, "POST", "/api/song", {"rev": 2, "set": {"beat": {"own": 2}}})
    running["state"] = "running"
    status, data = api(made, "GET", "/api/beat/session")
    assert status == 409 and data["code"] == "busy"
    running["state"] = "idle"
    assert api(made, "GET", "/api/beat/session")[1] == {"seed": 2}


def test_a_beat_render_is_refused_while_a_take_runs(made):
    class Beat:
        seed = 1

        def render_status(self):
            return {"state": "idle", "error": None}

    made.app._make_beat = lambda *a: Beat()
    api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"own": 1}}})
    made.app.rec.is_taking = True
    status, data = api(made, "POST", "/api/beat/render", {"score_file": "score-1.json"})
    assert status == 409 and data["code"] == "take_running"


def declared(server, path, length):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.putrequest("POST", path, skip_host=True)
    for name, value in (
        ("Host", f"127.0.0.1:{port}"),
        ("X-Token", TOKEN),
        ("Content-Type", "application/json"),
        ("Content-Length", str(length)),
    ):
        conn.putheader(name, value)
    conn.endheaders()
    return conn.getresponse().status


def test_the_body_limit_is_4_mib_for_the_beat_and_small_elsewhere(made):
    assert declared(made, "/api/song", 70_000) == 413
    assert declared(made, "/api/beat/check", 4 * 1024 * 1024 + 1) == 413
    big = json.dumps({"x": "a" * 200_000}).encode()
    assert call(made, "POST", "/api/beat/check", raw=big)[0] == 409


def test_an_unexpected_failure_is_one_json_500(made):
    made.app.song = lambda: 1 / 0
    status, data = api(made, "GET", "/api/song")
    assert status == 500 and "ZeroDivisionError" in data["error"]
    status, data = api(made, "GET", "/api/takes/audio?name=take-01.wav")
    assert status == 500 and "error" in data


def test_shutdown_stops_recording_then_jobs_then_the_server():
    order = []

    class Part:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        def shutdown(self):
            order.append("rec")

        def close(self):
            order.append("jobs")

        def server_close(self):
            order.append("server")

    close_all(Part("s"), Part("r"), Part("j"))
    assert order == ["rec", "jobs", "server"]

    order.clear()

    class Failing(Part):
        def shutdown(self):
            raise RuntimeError("stuck")

    with pytest.raises(RuntimeError):
        close_all(Part("s"), Failing("r"), Part("j"))
    assert order == ["jobs", "server"]


def test_python_dash_m_redline_runs_the_command_line():
    import subprocess

    done = subprocess.run(
        [sys.executable, "-m", "redline", "--version"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0 and done.stdout.startswith("redline ")


def test_a_job_without_its_inputs_fails_and_writes_no_result(root, tmp_path, capsys):
    model.save(model.new_song(NAME, {"file": "input/beat.wav"}, None, ("input/ref.wav",)), 0)
    result = tmp_path / "r.json"
    for kind in ("separate", "auto", "result"):
        assert main(["song", "job", kind, NAME, "--result", str(result)]) == 2
    assert not result.exists()
    assert "no voice chosen" in capsys.readouterr().err


def test_a_text_chosen_by_its_seed_alone_takes_the_latest_version(made, root):
    seed = root / "private" / "lyrics" / "generated" / "5"
    seed.mkdir(parents=True)
    (seed / "text.txt").write_text("a line\n", encoding="utf-8")
    (seed / "text-2.txt").write_text("another line\n", encoding="utf-8")
    status, data = api(made, "POST", "/api/song", {"rev": 1, "set": {"text": {"seed": 5}}})
    assert status == 200
    assert data["song"]["text"] == {"seed": 5, "version": 2}
    empty = {"rev": 2, "set": {"text": {"seed": 6}}}
    assert api(made, "POST", "/api/song", empty)[0] == 422
    assert model.load(NAME).text == {"seed": 5, "version": 2}


def test_the_text_command_quotes_a_beat_path_a_shell_would_read(made, root):
    odd = "input/b $(touch x).wav"
    sf.write(root / odd, tone(2.0), 8000)
    status, data = api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"file": odd}}})
    assert status == 200
    command = data["stations"]["text"]["command"]
    assert shlex.split(command)[-1] == odd
    assert "'input/b $(touch x).wav'" in command


def test_a_job_command_keeps_a_dash_name_a_name():
    from redline.cli.parser import build_parser

    argv = job_argv("auto", "-h", 7, Path("/r/out.json"))
    args = build_parser().parse_args(argv[argv.index("song") :])
    assert (args.name, args.kind, args.seed, args.result) == ("-h", "auto", 7, Path("/r/out.json"))


def test_a_take_plays_over_the_beat_from_its_first_frame(made, root):
    rate = 8000
    beat = np.zeros((2 * rate, 2), dtype=np.float32)
    beat[0, :], beat[rate // 2, :] = 0.8, 0.8
    sf.write(root / "input" / "beat.wav", beat, rate)
    take = np.zeros(rate, dtype=np.float32)
    take[100] = 0.9
    sf.write(root / "input" / "takes" / NAME / "take-01.wav", take, rate)
    status, _, body = call(made, "GET", "/api/takes/audio?name=take-01.wav&with=beat")
    assert status == 200
    mixed, got = sf.read(io.BytesIO(body), always_2d=True)
    assert got == rate and len(mixed) == rate
    assert np.max(np.abs(mixed)) <= 1.0
    # the beat's clicks at 0 and 0.5 s and the take's at frame 100, nothing else
    loud = sorted(set(np.flatnonzero(np.abs(mixed[:, 0]) > 0.05)))
    assert loud == [0, 100, rate // 2]
    alone = sf.read(io.BytesIO(call(made, "GET", "/api/takes/audio?name=take-01.wav")[2]))[0]
    assert np.flatnonzero(np.abs(np.atleast_2d(alone.T)[0]) > 0.05).tolist() == [100]
    assert call(made, "GET", "/api/takes/audio?name=take-01.wav&with=other")[0] == 400
