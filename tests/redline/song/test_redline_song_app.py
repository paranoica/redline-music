import dataclasses
import functools
import json
import sys
import threading
import time
from datetime import datetime

import pytest
import soundfile as sf
from redline.cli import main
from redline.song import model, rec, server
from redline.song.app import AppHost, Gate
from redline.song.jobs import JobRunner
from redline.song.server import SongApp, make_server
from test_redline_song_server import (
    TOKEN,
    WRITE_RANK,
    FakeRec,
    api,
    fake_studio,
    tone,
    until,
)

SLEEP = "import time; time.sleep(60)"


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    sf.write(base / "input" / "beat.wav", tone(2.0), 8000)
    sf.write(base / "input" / "ref.wav", tone(), 8000)
    return base


def add_song(root, name, beat=None, text=None, takes=0):
    song = model.new_song(name, beat or {"file": "input/beat.wav"}, None, ("input/ref.wav",))
    if text is not None:
        song = dataclasses.replace(song, text=text)
    model.save(song, 0)
    folder = root / "input" / "takes" / name
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, takes + 1):
        sf.write(folder / f"take-{n:02d}.wav", tone(), 8000)
    return song


def add_score(root, seed):
    folder = root / "private" / "beats" / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "score-1.json").write_text(json.dumps({"bpm": 140}), encoding="utf-8")


def add_text(root, seed, versions=1, titles=None):
    folder = root / "private" / "lyrics" / "generated" / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    for version in range(1, versions + 1):
        name = "text.txt" if version == 1 else f"text-{version}.txt"
        (folder / name).write_text(f"secret line {seed} {version}\n", encoding="utf-8")
    for version, title in (titles or {}).items():
        name = "title.txt" if version == 1 else f"title-{version}.txt"
        (folder / name).write_text(title + "\n", encoding="utf-8")
    return folder


@pytest.fixture
def served(root, tmp_path):
    sleepers = set()

    def argv(kind, name, seed, result):
        if (kind, name) in sleepers:
            return [sys.executable, "-c", SLEEP]
        return [sys.executable, "-c", WRITE_RANK, str(result)]

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "daw.html").write_bytes(b"<!doctype html><title>daw</title>")
    make_app = functools.partial(
        SongApp, argv=argv, build_studio=fake_studio, assets=lambda: (None, None, str)
    )
    host = AppHost(make_rec=FakeRec, make_app=make_app, kill_after=0.3, switch_timeout=0.5)
    httpd = make_server(host, TOKEN, port=0, site=dist)
    httpd.sleepers = sleepers
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    host.jobs.close(timeout=5)
    httpd.server_close()


def free(name):
    """Whether no server holds the song: a second app for it opens."""
    runner = JobRunner()
    try:
        app = SongApp(name, FakeRec(None, None), runner, assets=lambda: (None, None, str))
    except model.SongError:
        return False
    app.close()
    runner.close(timeout=5)
    return True


# list


def test_the_list_is_empty_with_no_songs_and_no_active_one(served):
    assert api(served, "GET", "/api/songs") == (200, {"songs": [], "active": None})


def test_the_list_shows_each_beat_kind_the_takes_the_text_and_the_time(served, root):
    before = time.time()
    add_song(root, "a", {"own": 3}, {"seed": 12, "version": 1})
    add_song(root, "b", takes=2)
    (root / "input" / "takes" / "b" / "notes.txt").write_text("x", encoding="utf-8")
    add_song(root, "c", {"file": "work/flips/0123abcd/flip-2.wav"})
    status, data = api(served, "GET", "/api/songs")
    assert status == 200 and data["active"] is None
    songs = data["songs"]
    assert [s["name"] for s in songs] == ["a", "b", "c"]
    assert [s["beat"] for s in songs] == ["own", "file", "flip"]
    assert [s["takes"] for s in songs] == [0, 2, 0]
    assert [s["text"] for s in songs] == [True, False, False]
    assert "3" in songs[0]["beat_label"] and songs[1]["beat_label"] == "beat.wav"
    assert "2" in songs[2]["beat_label"]
    for song in songs:
        assert set(song) == {"name", "beat", "beat_label", "takes", "text", "modified"}
        when = datetime.fromisoformat(song["modified"])
        assert when.tzinfo is not None and before - 2 <= when.timestamp() <= time.time() + 2
    served.host.open("b")
    assert api(served, "GET", "/api/songs")[1]["active"] == "b"


# create


def test_a_song_on_a_new_beat_gets_a_starter_on_a_free_seed(served, root):
    pytest.importorskip("redline_dsp")
    body = {"name": "fresh", "beat": {"new": {"genre": "trap", "kit": "synth"}}, "text_seed": None}
    assert api(served, "POST", "/api/songs", body) == (201, {"name": "fresh"})
    seed = model.load("fresh").beat["own"]
    folder = root / "private" / "beats" / str(seed)
    assert (folder / "score-1.json").is_file() and (folder / "recipe.json").is_file()
    status, data = api(
        served, "POST", "/api/songs", {**body, "name": "bad", "beat": {"new": {"genre": "polka"}}}
    )
    assert status == 422 and "polka" in json.dumps(data)
    status, _ = api(
        served, "POST", "/api/songs", {**body, "name": "bad2", "beat": {"new": {"kit": "drums"}}}
    )
    assert status == 422
    status, _ = api(
        served, "POST", "/api/songs", {**body, "name": "bad3", "beat": {"new": {"x": 1}}}
    )
    assert status == 422
    # a create that fails leaves no starter behind
    before = sorted(p.name for p in (root / "private" / "beats").iterdir())
    assert api(served, "POST", "/api/songs", {**body, "name": "fresh"})[0] == 409
    assert api(served, "POST", "/api/songs", {**body, "name": "other", "text_seed": 999})[0] == 422
    assert sorted(p.name for p in (root / "private" / "beats").iterdir()) == before
    # nor does one stopped by the disk
    from unittest import mock

    with mock.patch.object(model, "create", side_effect=OSError("disk full")):
        assert api(served, "POST", "/api/songs", {**body, "name": "third"})[0] == 500
    assert sorted(p.name for p in (root / "private" / "beats").iterdir()) == before


def test_a_song_is_created_with_every_reference_and_the_latest_text(served, root):
    refs = root / "input" / "refs"
    refs.mkdir()
    sf.write(refs / "r1.wav", tone(), 8000)
    (refs / "r2.MP3").write_bytes(b"not decoded here")
    (refs / "notes.txt").write_text("x", encoding="utf-8")
    (root / "input" / "beats").mkdir()
    sf.write(root / "input" / "beats" / "x.wav", tone(), 8000)
    add_text(root, 12, versions=2)
    body = {"name": "new", "beat": {"file": "input/beats/x.wav"}, "text_seed": 12}
    assert api(served, "POST", "/api/songs", body) == (201, {"name": "new"})
    song = model.load("new")
    assert song.beat == {"file": "input/beats/x.wav"}
    assert song.references == ("input/refs/r1.wav", "input/refs/r2.MP3")
    assert song.text == {"seed": 12, "version": 2}
    assert song.voice == {"takes": "input/takes/new", "chosen": None} and song.rev == 1
    add_score(root, 3)
    body = {"name": "own", "beat": {"own": 3}, "text_seed": None}
    assert api(served, "POST", "/api/songs", body) == (201, {"name": "own"})
    assert model.load("own").beat == {"own": 3} and model.load("own").text is None
    listed = api(served, "GET", "/api/songs")[1]
    assert [s["name"] for s in listed["songs"]] == ["new", "own"] and listed["active"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"name": "a/b", "beat": {"own": 3}, "text_seed": None},
        {"name": "..", "beat": {"own": 3}, "text_seed": None},
        {"name": "../up", "beat": {"own": 3}, "text_seed": None},
        {"name": "", "beat": {"own": 3}, "text_seed": None},
        {"name": 5, "beat": {"own": 3}, "text_seed": None},
        {"name": "x", "beat": {"file": "work/x.wav"}, "text_seed": None},
        {"name": "x", "beat": {"file": "input/../work/x.wav"}, "text_seed": None},
        {"name": "x", "beat": {"file": "../outside.wav"}, "text_seed": None},
        {"name": "x", "beat": {"file": "/etc/passwd"}, "text_seed": None},
        {"name": "x", "beat": {"file": "input/beats/missing.wav"}, "text_seed": None},
        {"name": "x", "beat": {"file": "input/beats/link.wav"}, "text_seed": None},
        {"name": "x", "beat": {"own": 4}, "text_seed": None},
        {"name": "x", "beat": {"own": -1}, "text_seed": None},
        {"name": "x", "beat": {"own": True}, "text_seed": None},
        {"name": "x", "beat": {}, "text_seed": None},
        {"name": "x", "beat": {"own": 3, "file": "input/beat.wav"}, "text_seed": None},
        {"name": "x", "beat": {"own": 3}, "text_seed": 99},
        {"name": "x", "beat": {"own": 3}, "text_seed": "12"},
    ],
)
def test_a_bad_name_beat_or_text_is_422_and_saves_nothing(served, root, tmp_path, body):
    add_score(root, 3)
    (root / "private" / "beats" / "4").mkdir()
    sf.write(root / "work" / "x.wav", tone(), 8000)
    sf.write(tmp_path / "outside.wav", tone(), 8000)
    (root / "input" / "beats").mkdir()
    (root / "input" / "beats" / "link.wav").symlink_to(tmp_path / "outside.wav")
    status, data = api(served, "POST", "/api/songs", body)
    assert status == 422, data
    assert model.list_songs() == []
    assert not (root / "private" / "songs" / "x").exists()


def test_an_existing_name_is_409_and_the_song_stays(served, root):
    add_song(root, "a", {"own": 1})
    add_score(root, 3)
    status, data = api(served, "POST", "/api/songs", {"name": "a", "beat": {"own": 3}})
    assert status == 409 and data["code"] == "exists"
    assert model.load("a").beat == {"own": 1} and model.load("a").rev == 1


def test_an_unknown_field_in_a_new_song_is_refused(served, root):
    add_score(root, 3)
    body = {"name": "x", "beat": {"own": 3}, "refs": ["input/ref.wav"]}
    assert api(served, "POST", "/api/songs", body)[0] == 400
    assert model.list_songs() == []


# sources


def test_sources_list_only_what_exists_and_no_lyric_text(served, root):
    status, empty = api(served, "GET", "/api/songs/sources")
    assert status == 200 and empty["pack_kit"] is False
    from redline.beat import pack

    pack.CATALOG.parent.mkdir(parents=True, exist_ok=True)
    pack.CATALOG.write_text("[]")
    status, broken = api(served, "GET", "/api/songs/sources")
    assert status == 200 and broken["pack_kit"] is False
    pack.CATALOG.unlink()
    assert {k: empty[k] for k in ("beats", "seeds", "texts")} == {
        "beats": [],
        "seeds": [],
        "texts": [],
    }
    assert "trap" in empty["genres"] and "uk_drill" in empty["genres"]
    beats = root / "input" / "beats"
    (beats / "sub").mkdir(parents=True)
    sf.write(beats / "b.wav", tone(), 8000)
    (beats / "a.mp3").write_bytes(b"x")
    (beats / "notes.txt").write_text("x", encoding="utf-8")
    sf.write(beats / ".hidden.wav", tone(), 8000)
    sf.write(beats / "sub" / "c.wav", tone(), 8000)
    add_score(root, 10)
    add_score(root, 3)
    (root / "private" / "beats" / "4").mkdir()
    (root / "private" / "beats" / "4" / "draft.json").write_text("{}", encoding="utf-8")
    add_score(root, "x")
    add_text(root, 12, titles={1: "First"})
    add_text(root, 13, versions=2, titles={1: "Old", 2: "Second"})
    add_text(root, 14, versions=0, titles={1: "No text"})
    add_text(root, 15)
    status, data = api(served, "GET", "/api/songs/sources")
    assert status == 200
    assert data["beats"] == ["input/beats/a.mp3", "input/beats/b.wav"]
    assert data["seeds"] == [3, 10]
    assert data["texts"] == [
        {"seed": 12, "title": "First"},
        {"seed": 13, "title": "Second"},
        {"seed": 15, "title": ""},
    ]
    assert "secret line" not in json.dumps(data)


def test_a_title_that_is_not_utf8_still_lists_its_seed(served, root):
    add_text(root, 12, titles={1: "First"})
    folder = add_text(root, 13)
    (folder / "title.txt").write_bytes(b"Ab\xffc\n")
    status, data = api(served, "GET", "/api/songs/sources")
    assert status == 200
    assert data["texts"] == [{"seed": 12, "title": "First"}, {"seed": 13, "title": "Ab\ufffdc"}]


# names


def test_a_name_of_64_characters_is_taken(served, root):
    add_score(root, 3)
    name = "n" * 64
    assert api(served, "POST", "/api/songs", {"name": name, "beat": {"own": 3}}) == (
        201,
        {"name": name},
    )
    assert model.list_songs() == [name]


@pytest.mark.parametrize("name", ["n" * 65, "n" * 300])
def test_a_name_over_64_characters_is_422_naming_the_limit(served, root, name):
    add_score(root, 3)
    status, data = api(served, "POST", "/api/songs", {"name": name, "beat": {"own": 3}})
    assert status == 422 and "64 characters" in data["error"]
    assert str(root) not in data["error"]
    assert model.list_songs() == []
    status, data = api(served, "POST", "/api/songs/open", {"name": name})
    assert status == 422 and "64 characters" in data["error"]


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("a\x01b", "control"),
        ("a\x7fb", "control"),
        ("a\x85b", "control"),
        ("a\u202ab", "direction"),
        ("a\u202eb", "direction"),
        ("a\u2066b", "direction"),
        ("a\u2069b", "direction"),
    ],
)
def test_a_name_with_control_or_direction_characters_is_422_naming_the_rule(
    served, root, name, rule
):
    add_score(root, 3)
    status, data = api(served, "POST", "/api/songs", {"name": name, "beat": {"own": 3}})
    assert status == 422 and rule in data["error"]
    assert model.list_songs() == []


# switch


def test_opening_a_song_switches_and_ends_the_old_ones_job_and_session(served, root):
    add_song(root, "a", takes=1)
    add_song(root, "b")
    served.sleepers.add(("rank", "a"))
    assert api(served, "POST", "/api/songs/open", {"name": "a"}) == (200, {"active": "a"})
    old = served.host.active
    assert api(served, "POST", "/api/jobs/start", {"kind": "rank"})[0] == 202
    assert until(lambda: served.host.jobs.state("a")["rank"]["state"] == "running")
    assert api(served, "POST", "/api/rec/open", {})[0] == 202
    assert served.host.rec.ready
    old._studio, old._beat = object(), object()
    assert api(served, "POST", "/api/songs/open", {"name": "b"}) == (200, {"active": "b"})
    assert served.host.jobs.state("a")["rank"]["state"] == "cancelled"
    assert not served.host.rec.ready
    assert old._studio is None and old._beat is None
    assert api(served, "GET", "/api/song")[1]["song"]["name"] == "b"
    assert api(served, "GET", "/api/songs")[1]["active"] == "b"
    assert free("a") and not free("b")


def test_opening_a_song_while_a_take_records_is_409_and_the_old_one_stays(served, root):
    add_song(root, "a")
    add_song(root, "b")
    served.host.open("a")
    old = served.host.active
    served.host.rec.is_taking = True
    status, data = api(served, "POST", "/api/songs/open", {"name": "b"})
    assert status == 409 and data["code"] == "take_running"
    assert served.host.active is old
    assert api(served, "GET", "/api/song")[1]["song"]["name"] == "a"
    assert free("b") and not free("a")
    served.host.rec.is_taking = False
    assert api(served, "POST", "/api/songs/open", {"name": "b"}) == (200, {"active": "b"})


@pytest.mark.parametrize("error", ["refused", "desync"])
def test_a_switch_while_a_stop_that_timed_out_is_pending_is_409_and_the_take_ranks(
    root, tmp_path, error
):
    from redline.record.process import EngineError
    from redline.record.protocol import EngineDesync
    from test_redline_song_rec import LATE, Fixture

    add_song(root, "a")
    add_song(root, "b")
    fix = Fixture(tmp_path)

    def make_rec(logged, work):
        return rec.RecOwner(fix.opener, on_take_logged=logged, work=work, tick_s=0.01)

    make_app = functools.partial(
        SongApp,
        argv=lambda kind, name, seed, result: [sys.executable, "-c", WRITE_RANK, str(result)],
        build_studio=fake_studio,
        assets=lambda: (None, None, str),
    )
    host = AppHost(make_rec=make_rec, make_app=make_app, kill_after=0.3, switch_timeout=0.5)
    try:
        host.open("a")
        app = host.active
        app.rec_command("open", {})
        assert until(lambda: host.rec.stage() == "ready")
        assert app.rec_command("toggle", {})[0] == 200
        fix.engine.stop_errors.append(
            EngineError("the take did not finish within 10 s: audio?")
            if error == "refused"
            else EngineDesync(LATE)
        )
        with pytest.raises(server.ApiError):
            app.rec_command("toggle", {})
        assert host.rec._ready.session.pending is not None
        with pytest.raises(server.ApiError) as refused:
            host.open("b")
        assert refused.value.payload["code"] == "take_running"
        assert host.active is app
        app.rec_command("toggle" if error == "refused" else "open", {})
        assert until(lambda: host.jobs.state("a").get("rank", {}).get("state") == "done")
    finally:
        host.rec.shutdown(10)
        host.jobs.close(timeout=5)
        if host.active is not None:
            host.active.close()


def test_opening_waits_for_requests_in_flight_and_gives_up_as_busy(served, root):
    add_song(root, "a")
    add_song(root, "b")
    served.host.open("a")
    with served.host._gate.shared():
        status, data = api(served, "POST", "/api/songs/open", {"name": "b"})
    assert status == 409 and data["code"] == "busy"
    assert served.host.active.name == "a" and free("b")


def test_opening_a_missing_or_badly_named_song_is_refused(served, root):
    add_song(root, "a")
    assert api(served, "POST", "/api/songs/open", {"name": "nope"})[0] == 404
    assert api(served, "POST", "/api/songs/open", {"name": "../a"})[0] == 422
    assert served.host.active is None
    served.host.open("a")
    first = served.host.active
    assert api(served, "POST", "/api/songs/open", {"name": "a"}) == (200, {"active": "a"})
    assert served.host.active is first


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/song", None),
        ("GET", "/api/rec/state", None),
        ("GET", "/api/jobs", None),
        ("POST", "/api/jobs/start", {"kind": "rank"}),
        ("POST", "/api/rec/open", {}),
    ],
)
def test_a_song_route_with_no_active_song_is_409_no_song(served, root, method, path, body):
    add_song(root, "a")
    status, data = api(served, method, path, body)
    assert status == 409 and data["code"] == "no_song"


def test_a_take_logged_after_a_switch_ranks_the_song_that_recorded_it(served, root):
    add_song(root, "a", takes=1)
    add_song(root, "b", takes=1)
    served.host.open("a")
    served.host.open("b")
    served.host.rec.logged(root / "input" / "takes" / "b" / "take-01.wav")
    assert until(lambda: served.host.jobs.state("b").get("rank", {}).get("state") == "done")
    assert (root / "work" / "songs" / "b" / "ranking.json").is_file()
    assert "rank" not in served.host.jobs.state("a")
    assert not (root / "work" / "songs" / "a" / "ranking.json").exists()


def test_the_gate_lets_a_switch_in_only_after_the_requests_in_flight():
    gate = Gate()
    order = []
    entered = threading.Event()
    release = threading.Event()

    def reader():
        with gate.shared():
            entered.set()
            assert release.wait(5)
            order.append("request")

    def writer():
        with gate.alone(5):
            order.append("switch")

    first = threading.Thread(target=reader)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=writer)
    second.start()
    assert until(lambda: gate._waiting == 1)
    late_done = []

    def late_reader():
        with gate.shared():
            late_done.append(list(order))

    late = threading.Thread(target=late_reader)
    late.start()
    time.sleep(0.05)
    assert order == [] and late_done == []
    release.set()
    for thread in (first, second, late):
        thread.join(5)
    assert order == ["request", "switch"] and late_done == [["request", "switch"]]


# command line


@pytest.fixture
def no_serve(root, monkeypatch):
    seen = {}

    class Stub:
        server_address = ("127.0.0.1", 1)

        def serve_forever(self):
            seen["active"] = seen["host"].active

        def server_close(self):
            pass

    def fake_make_server(host, token, port=0, site=None):
        seen["host"] = host
        return Stub()

    monkeypatch.setattr(server, "make_server", fake_make_server)
    monkeypatch.setattr("signal.signal", lambda *args: None)
    yield seen
    seen["host"].jobs.close(timeout=5)


def test_app_starts_with_no_active_song(no_serve, capsys):
    assert main(["app", "--no-browser"]) == 0
    assert no_serve["active"] is None


def test_song_studio_without_a_name_is_the_app_and_with_one_opens_it(no_serve, root, capsys):
    add_song(root, "a")
    assert main(["song", "studio", "a", "--no-browser"]) == 0
    assert no_serve["active"].name == "a"
    assert main(["song", "studio", "--no-browser"]) == 0
    assert no_serve["active"] is None
    assert main(["song", "studio", "nope", "--no-browser"]) == 2
