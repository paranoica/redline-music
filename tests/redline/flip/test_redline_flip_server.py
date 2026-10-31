import functools
import http.client
import json
import stat
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.flip import model as flip_model
from redline.flip import pipeline
from redline.record.session import Outcome
from redline.song import model
from redline.song.app import AppHost
from redline.song.rec import NotReady, TakeRunning
from redline.song.server import SongApp, job_argv, make_server

TOKEN = "test-token"
NAME = "demo"
RATE = 8000
STEM_RATE = 48000
BPM = 120.0
BAR_S = 2.0
BARS = 4
RESULT = (
    "import json, pathlib, sys; r = pathlib.Path(sys.argv[1]); "
    "r.write_text(json.dumps({'kind': sys.argv[2], 'flip': sys.argv[3]}))"
)
SLEEP = "import time; time.sleep(30)"


class FakeRec:
    def __init__(self, logged, work):
        self.logged = logged
        self.calls = []
        self.ready = False
        self.is_taking = False
        self.down = False
        self.reason = None
        self.open_error = None
        self.attempts = 0
        self.snapshots = 0

    def stage(self):
        return self.snapshot(count=False).stage

    def snapshot(self, count=True):
        from redline.song.rec import RecSnapshot

        self.snapshots += count

        stage = "ready" if self.ready else "closed" if self.reason else "idle"
        return RecSnapshot(
            stage,
            self.reason,
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
        self.attempts += 1
        if self.open_error is not None:
            raise self.open_error
        if self.is_taking:
            raise TakeRunning("a take is running")
        self.calls.append(("open", request))
        self.ready = True
        self.reason = None

    def toggle(self):
        if not self.ready:
            raise NotReady("the engine is not ready (idle)")
        self.calls.append(("toggle",))
        return Outcome("recording", {"take": "take-02"})

    def discard_last(self):
        return Outcome("nothing_to_discard")

    def close(self, reason=None):
        self.ready = False
        self.reason = reason

    def shutdown(self):
        self.down = True


def tone(seconds):
    t = np.arange(int(seconds * RATE)) / RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")


def draft_json(source="input/beats/x.wav", **changes):
    data = {
        "format": 1,
        "source": source,
        "grid": {"bpm": BPM, "downbeat_s": 0.0, "swing": 50},
        "bass808": {"mode": "original", "params": {}},
        "hats": {"mode": "original", "remove": [], "patterns": []},
        "gains": {"drums": 0, "other": 0, "bass": 0},
        "arrangement": [{"label": "all", "from_bar": 0, "to_bar": BARS}],
        "effects": [],
    }
    data.update(changes)
    return data


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    (base / "input" / "beats").mkdir()
    sf.write(base / "input" / "beats" / "x.wav", tone(BARS * BAR_S), RATE)
    sf.write(base / "input" / "ref.wav", tone(1), RATE)
    takes = base / "input" / "takes" / NAME
    takes.mkdir(parents=True)
    sf.write(takes / "take-01.wav", tone(1), RATE)
    return base


def analysis_json(sha8):
    return {
        "format": 1,
        "source": "input/beats/x.wav",
        "sha8": sha8,
        "rate": STEM_RATE,
        "frames": int(BARS * BAR_S * STEM_RATE),
        "duration_s": BARS * BAR_S,
        "bpm": BPM,
        "downbeat_s": 0.0,
        "bar_s": BAR_S,
        "bar_starts_s": [i * BAR_S for i in range(BARS)],
        "detune_cents": 13,
        "notes": [],
        "note_times": [],
        "counts": {"notes": 7, "low_confidence": 2},
    }


def folder_of(root):
    return root / "work" / "flips" / pipeline.source_sha8(root / "input" / "beats" / "x.wav")


def analyse_by_hand(root):
    folder = folder_of(root)
    folder.mkdir(parents=True)
    (folder / "analysis.json").write_text(json.dumps(analysis_json(folder.name)))
    (folder / "stems48").mkdir()
    for name in ("drums", "bass", "other"):
        sf.write(folder / "stems48" / f"{name}.wav", tone(1), STEM_RATE)
    flip_model.save_draft(folder, flip_model.parse(draft_json()))
    return folder


def render_by_hand(folder, seconds=1.0):
    n = flip_model.save_new(folder, flip_model.parse(draft_json()))
    sf.write(folder / f"flip-{n}.wav", np.column_stack([tone(seconds)] * 2), RATE, subtype="FLOAT")
    return n


@pytest.fixture
def made(root, tmp_path):
    model.save(model.new_song(NAME, {"file": "input/beats/x.wav"}, None, ("input/ref.wav",)), 0)

    def argv(kind, name, seed, result, flip=None):
        code = SLEEP if kind == "flip_render" and (root / "slow").exists() else RESULT
        return [sys.executable, "-c", code, str(result), kind, str(flip)]

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "daw.html").write_bytes(b"<!doctype html><title>daw</title>")
    host = AppHost(
        make_rec=FakeRec,
        make_app=functools.partial(SongApp, argv=argv, assets=lambda: (None, None, str)),
        kill_after=0.3,
    )
    host.open(NAME)
    httpd = make_server(host, TOKEN, port=0, site=dist)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    host.jobs.close(timeout=5)
    httpd.server_close()


def call(server, method, path, body=None, token=TOKEN, host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    sent = {"Host": host or f"127.0.0.1:{port}"}
    if token is not None:
        sent["X-Token"] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
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


GETS = ["/api/flip/state", "/api/flip/peaks", "/api/flip/audio?n=1"]
POSTS = ["/api/flip/draft", "/api/flip/propose", "/api/flip/render", "/api/flip/use"]


@pytest.mark.parametrize("path", GETS)
def test_every_flip_get_refuses_a_missing_token_and_a_foreign_host(made, path):
    assert call(made, "GET", path, token=None)[0] == 403
    assert call(made, "GET", path, token="wrong")[0] == 403
    assert call(made, "GET", path, host="evil.com")[0] == 403


@pytest.mark.parametrize("path", POSTS)
def test_every_flip_post_refuses_a_missing_token_and_a_foreign_host(made, path):
    assert call(made, "POST", path, {}, token=None)[0] == 403
    assert call(made, "POST", path, {}, token="wrong")[0] == 403
    assert call(made, "POST", path, {}, host="evil.com")[0] == 403


def test_the_state_before_any_analysis(made, root):
    status, data = api(made, "GET", "/api/flip/state")
    assert status == 200
    assert data["source"] == "input/beats/x.wav" and len(data["sha8"]) == 8
    assert data["analysis"] is None and data["draft"] is None
    assert data["versions"] == [] and data["renders"] == [] and data["used"] is None
    assert data["takes_over_other_beat"] == 0
    assert data["presets"] == ["8ths", "16ths", "trip_roll", "roll32"]


def test_the_state_carries_the_analysis_the_draft_and_the_effect_ranges(made, root):
    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    data = api(made, "GET", "/api/flip/state")[1]
    assert data["analysis"] == {
        "bpm": BPM,
        "downbeat_s": 0.0,
        "bars": BARS,
        "bar_s": BAR_S,
        "detune_cents": 13,
        "notes": 7,
        "low_confidence": 2,
        "duration_s": BARS * BAR_S,
    }
    assert data["draft"]["arrangement"][0]["to_bar"] == BARS
    assert data["versions"] == [n] and data["renders"] == [n]
    assert set(data["effects"]) == {"reverse", "tape_stop", "tape_start", "stutter", "granular"}
    assert data["effects"]["reverse"] == {}
    duration = data["effects"]["tape_stop"]["duration"]
    assert duration == {"low": 0.05, "high": 4.0, "default": 0.5, "unit": "s"}


def test_an_own_beat_has_no_flip_routes(made):
    api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"own": 4}}})
    for path in ("/api/flip/state", "/api/flip/peaks"):
        status, data = api(made, "GET", path)
        assert status == 409 and data["code"] == "own_beat"
    assert api(made, "POST", "/api/flip/draft", {"flip": draft_json()})[1]["code"] == "own_beat"
    assert api(made, "POST", "/api/jobs/start", {"kind": "flip_stems"})[1]["code"] == "own_beat"


def test_a_flip_render_as_the_beat_opens_its_flip_and_source(made, root):
    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    rel = f"work/flips/{folder.name}/flip-{n}.wav"
    assert api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"file": rel}}})[0] == 200
    data = api(made, "GET", "/api/flip/state")[1]
    assert data["source"] == "input/beats/x.wav" and data["used"] == n
    assert data["sha8"] == folder.name


def test_a_flip_beat_without_its_json_is_409(made, root):
    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    (folder / f"flip-{n}.json").unlink()
    rel = f"work/flips/{folder.name}/flip-{n}.wav"
    assert api(made, "POST", "/api/song", {"rev": 1, "set": {"beat": {"file": rel}}})[0] == 200
    status, data = api(made, "GET", "/api/flip/state")
    assert status == 409 and data["code"] == "no_beat"


def test_the_peaks_of_the_source_and_of_a_stem(made, root):
    status, data = api(made, "GET", "/api/flip/peaks?bars=1")
    assert status == 200 and data["bar_starts_s"] == []
    assert 0 < len(data["peaks"]) <= 1000 and all(lo <= hi for lo, hi in data["peaks"])
    analyse_by_hand(root)
    data = api(made, "GET", "/api/flip/peaks?bars=1")[1]
    assert data["bar_starts_s"] == [0.0, 2.0, 4.0, 6.0]
    assert api(made, "GET", "/api/flip/peaks")[1]["bar_starts_s"] == []
    stem = api(made, "GET", "/api/flip/peaks?stem=bass")[1]
    assert 0 < len(stem["peaks"]) <= 1000
    assert api(made, "GET", "/api/flip/peaks?stem=vocals")[0] == 400
    assert api(made, "GET", "/api/flip/peaks?stem=..%2Fx")[0] == 400


def test_a_stem_before_the_analysis_is_409(made):
    assert api(made, "GET", "/api/flip/peaks?stem=bass")[1]["code"] == "no_analysis"
    assert call(made, "GET", "/api/flip/audio?stem=bass")[0] == 409


def test_a_draft_is_saved_privately_and_read_back(made, root):
    folder = analyse_by_hand(root)
    edit = draft_json(arrangement=[{"label": "mine", "from_bar": 1, "to_bar": 3}])
    assert api(made, "POST", "/api/flip/draft", {"flip": edit}) == (200, {"ok": True})
    assert stat.S_IMODE((folder / "draft.json").stat().st_mode) == 0o600
    assert api(made, "GET", "/api/flip/state")[1]["draft"]["arrangement"][0]["label"] == "mine"


@pytest.mark.parametrize(
    "change",
    [
        {"extra": 1},
        {"grid": {"bpm": 1000, "downbeat_s": 0, "swing": 50}},
        {"source": "input/other.wav"},
        {"source": "../x.wav"},
        {"arrangement": [{"label": "a", "from_bar": 0, "to_bar": BARS + 1}]},
        {"arrangement": []},
    ],
)
def test_a_bad_flip_is_422_and_keeps_the_draft(made, root, change):
    folder = analyse_by_hand(root)
    before = (folder / "draft.json").read_bytes()
    status, data = api(made, "POST", "/api/flip/draft", {"flip": draft_json(**change)})
    assert status == 422 and data["code"] == "invalid_flip" and data["errors"]
    assert (folder / "draft.json").read_bytes() == before


def test_a_draft_that_is_no_flip_or_has_other_keys_is_refused(made, root):
    analyse_by_hand(root)
    assert api(made, "POST", "/api/flip/draft", {"flip": "x"})[0] == 422
    assert api(made, "POST", "/api/flip/draft", {"flip": None})[0] == 422
    assert api(made, "POST", "/api/flip/draft", {"flip": draft_json(), "x": 1})[0] == 400


def test_propose_needs_a_text_and_an_analysis(made, root):
    status, data = api(made, "POST", "/api/flip/propose", {})
    assert status == 409 and data["code"] == "no_text"
    seed = root / "private" / "lyrics" / "generated" / "3"
    seed.mkdir(parents=True)
    (seed / "text.txt").write_text("[Verse]\none\ntwo\n[Hook]\nthree\n", encoding="utf-8")
    song = model.load(NAME)
    from dataclasses import replace

    model.save(replace(song, text={"seed": 3, "version": 1}), song.rev)
    assert api(made, "POST", "/api/flip/propose", {})[1]["code"] == "no_analysis"
    analyse_by_hand(root)
    status, data = api(made, "POST", "/api/flip/propose", {})
    assert status == 200
    blocks = data["arrangement"]
    assert [b["label"] for b in blocks] == ["verse", "hook"]
    assert [b["to_bar"] - b["from_bar"] for b in blocks] == [2, 1]
    assert flip_model.parse(draft_json(arrangement=blocks)).play_bars == 3


def test_a_render_saves_the_next_version_and_runs_the_job_with_its_number(made, root):
    folder = analyse_by_hand(root)
    status, data = api(made, "POST", "/api/flip/render", {})
    assert status == 202 and data == {"n": 1}
    path = folder / "flip-1.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["source"] == "input/beats/x.wav"
    assert until(lambda: api(made, "GET", "/api/jobs")[1]["jobs"][0]["state"] == "done")
    job = api(made, "GET", "/api/jobs")[1]["jobs"][0]
    assert job["kind"] == "flip_render"
    assert json.loads(Path(job["result"]).read_text()) == {"kind": "flip_render", "flip": "1"}
    assert api(made, "POST", "/api/flip/render", {})[1] == {"n": 2}


def test_a_second_render_while_one_runs_is_409_busy_and_saves_nothing(made, root):
    folder = analyse_by_hand(root)
    (root / "slow").write_text("")
    assert api(made, "POST", "/api/flip/render", {})[0] == 202
    status, data = api(made, "POST", "/api/flip/render", {})
    assert status == 409 and data["code"] == "busy"
    assert flip_model.FLIP_FILE.fullmatch("flip-1.json") and not (folder / "flip-2.json").exists()


def test_a_render_needs_an_analysis_and_a_draft(made, root):
    assert api(made, "POST", "/api/flip/render", {})[1]["code"] == "no_analysis"
    folder = analyse_by_hand(root)
    (folder / "draft.json").unlink()
    assert api(made, "POST", "/api/flip/render", {})[1]["code"] == "no_draft"
    assert not list(folder.glob("flip-*.json"))


def test_the_flip_jobs_start_through_the_job_routes_but_a_render_only_with_a_number(made, root):
    analyse_by_hand(root)
    assert api(made, "POST", "/api/jobs/start", {"kind": "flip_stems"})[0] == 202
    assert api(made, "POST", "/api/jobs/start", {"kind": "flip_render"})[0] == 400
    assert until(lambda: api(made, "GET", "/api/jobs")[1]["jobs"][0]["state"] == "done")
    assert api(made, "POST", "/api/jobs/cancel", {"kind": "flip_render"})[0] == 404


def wav_info(data):
    import io

    return sf.info(io.BytesIO(data))


def test_audio_by_number_is_a_16_bit_wav(made, root):
    n = render_by_hand(analyse_by_hand(root), 1.0)
    status, headers, body = call(made, "GET", f"/api/flip/audio?n={n}")
    info = wav_info(body)
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    assert info.subtype == "PCM_16" and info.samplerate == RATE and info.frames == RATE


def test_audio_of_a_stem_is_a_wav(made, root):
    analyse_by_hand(root)
    status, _, body = call(made, "GET", "/api/flip/audio?stem=bass")
    assert status == 200 and wav_info(body).subtype == "PCM_16"
    assert call(made, "GET", "/api/flip/audio?stem=vocals")[0] == 400
    assert call(made, "GET", "/api/flip/audio?stem=..%2F..%2Fx")[0] == 400


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("n=..%2Fanalysis.json", 400),
        ("n=../flip-1", 400),
        ("n=1.5", 400),
        ("n=-1", 400),
        ("n=0", 400),
        ("n=%EF%BC%91", 400),
        ("n=99", 404),
        ("", 400),
        ("n=1&stem=bass", 400),
        ("n=1&n=2", 400),
    ],
)
def test_audio_takes_a_render_number_only(made, root, query, status):
    render_by_hand(analyse_by_hand(root))
    assert call(made, "GET", f"/api/flip/audio?{query}")[0] == status


def test_a_render_symlinked_out_of_work_is_refused(made, root):
    folder = analyse_by_hand(root)
    outside = root / "input" / "secret.wav"
    sf.write(outside, tone(1), RATE)
    (folder / "flip-5.wav").symlink_to(outside)
    status, data = api(made, "GET", "/api/flip/audio?n=5")
    assert status == 409 and data["code"] == "unreadable"


def test_a_stem_symlinked_out_of_work_is_refused(made, root):
    folder = analyse_by_hand(root)
    (folder / "stems48" / "bass.wav").unlink()
    (folder / "stems48" / "bass.wav").symlink_to(root / "input" / "ref.wav")
    assert call(made, "GET", "/api/flip/audio?stem=bass")[0] == 409
    assert api(made, "GET", "/api/flip/peaks?stem=bass")[0] == 409


def test_use_sets_the_beat_and_the_engine_follows(made, root):
    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    assert api(made, "POST", "/api/rec/open", {})[0] == 202
    status, data = api(made, "POST", "/api/flip/use", {"n": n, "rev": 1})
    rel = f"work/flips/{folder.name}/flip-{n}.wav"
    assert status == 200 and data["song"]["beat"] == {"file": rel} and data["song"]["rev"] == 2
    assert model.load(NAME).beat == {"file": rel}
    opened = [c[1].beat for c in made.app.rec.calls if c[0] == "open"]
    assert opened[-1] == (root / rel).resolve() and len(opened) == 2
    assert api(made, "GET", "/api/flip/state")[1]["used"] == n


def test_use_is_409_during_a_take_and_412_when_stale(made, root):
    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    made.app.rec.is_taking = True
    status, data = api(made, "POST", "/api/flip/use", {"n": n, "rev": 1})
    assert status == 409 and data["code"] == "take_running"
    assert model.load(NAME).beat == {"file": "input/beats/x.wav"}
    made.app.rec.is_taking = False
    status, data = api(made, "POST", "/api/flip/use", {"n": n, "rev": 9})
    assert status == 412 and data["code"] == "stale" and data["song"]["rev"] == 1
    assert model.load(NAME).beat == {"file": "input/beats/x.wav"}


@pytest.mark.parametrize(
    "body",
    [
        {"n": 99, "rev": 1},
        {"n": "1", "rev": 1},
        {"n": 0, "rev": 1},
        {"n": True, "rev": 1},
        {"n": 1},
    ],
)
def test_use_refuses_a_missing_or_malformed_render(made, root, body):
    render_by_hand(analyse_by_hand(root))
    status, _ = api(made, "POST", "/api/flip/use", body)
    assert status in (400, 404)
    assert model.load(NAME).beat == {"file": "input/beats/x.wav"}


def test_takes_over_other_beat_counts_complete_takes_of_another_hash(made, root):
    from redline.record.takes import file_sha256

    folder = analyse_by_hand(root)
    n = render_by_hand(folder)
    takes = root / "input" / "takes" / NAME
    for name, sha, complete in (("take-01", "0" * 64, True), ("take-02", "1" * 64, True)):
        sf.write(takes / f"{name}.wav", tone(1), RATE)
        (takes / f"{name}.json").write_text(json.dumps({"complete": complete, "beat_sha256": sha}))
    sf.write(takes / "take-03.wav", tone(1), RATE)
    (takes / "take-03.json").write_text(
        json.dumps({"complete": True, "beat_sha256": file_sha256(folder / f"flip-{n}.wav")})
    )
    sf.write(takes / "take-04.wav", tone(1), RATE)
    (takes / "take-04.json").write_text(json.dumps({"complete": False, "beat_sha256": "2" * 64}))
    sf.write(takes / "take-05.wav", tone(1), RATE)
    assert api(made, "GET", "/api/flip/state")[1]["takes_over_other_beat"] == 2
    assert api(made, "GET", f"/api/flip/state?n={n}")[1]["takes_over_other_beat"] == 2
    assert api(made, "GET", "/api/flip/state?n=x")[0] == 400


def test_the_job_command_carries_the_flip_before_the_dashes():
    argv = job_argv("flip_render", "demo", 0, Path("/r/out.json"), 3)
    assert argv[argv.index("--flip") + 1] == "3"
    assert argv.index("--flip") < argv.index("--")
    assert argv[argv.index("--") + 1 :] == ["flip_render", "demo"]
    assert "--flip" not in job_argv("flip_stems", "demo", 0, Path("/r/out.json"))


def test_the_job_command_round_trips_through_the_parser_with_a_dash_name():
    from redline.cli.parser import build_parser

    argv = job_argv("flip_render", "-h", 0, Path("/r/out.json"), 12)
    args = build_parser().parse_args(argv[argv.index("song") :])
    assert (args.name, args.kind, args.flip) == ("-h", "flip_render", 12)


@pytest.mark.parametrize("bad", [0, -1, True, "3", "3; ls", 1.5, None.__class__])
def test_a_hostile_flip_never_reaches_a_command(bad):
    with pytest.raises(ValueError):
        job_argv("flip_render", "demo", 0, Path("/r/out.json"), bad)


def test_a_render_number_too_long_to_be_one_is_a_bad_request(made):
    assert call(made, "GET", "/api/flip/audio?n=" + "9" * 5000)[0] == 400


def test_a_refused_render_job_leaves_no_version_behind(made, root, monkeypatch):
    from redline.song.jobs import Busy

    folder = analyse_by_hand(root)
    before = sorted(p.name for p in folder.glob("flip-*.json"))

    def refuse(*args, **kwargs):
        raise Busy("held")

    monkeypatch.setattr(made.app, "_start", refuse)
    assert api(made, "POST", "/api/flip/render", {})[0] == 409
    assert sorted(p.name for p in folder.glob("flip-*.json")) == before


def test_a_draft_whose_own_parts_cannot_play_at_its_tempo_is_422(made, root):
    analyse_by_hand(root)
    flip = draft_json(bass808={"mode": "ours", "params": {}})
    flip["grid"]["bpm"] = 260
    status, data = api(made, "POST", "/api/flip/draft", {"flip": flip})
    assert status == 422 and "40..220" in data["error"]
