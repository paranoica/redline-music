import dataclasses
import http.client
import io
import json
import threading
import time

import numpy as np
import pytest
import soundfile as sf

redline_dsp = pytest.importorskip("redline_dsp")

from redline.beat import server as beat_server  # noqa: E402
from redline.beat.library import git_sha1  # noqa: E402
from redline.beat.recipe import make_recipe, recipe_json  # noqa: E402
from redline.cli import build_parser, main  # noqa: E402

TOKEN = "beat-t0k3n-for-tests"
SEED = 7
LIB = "fake-keys"
UNFETCHED = "fake-strings"
OWN = "own-kit"
RATE = 48000
ZONE_RATE = 44100
PAGE = b"<!doctype html><title>beat</title>"


def score_body(kick="x...x...x...x..."):
    """Two bars at 180 BPM in A minor: kick, hat, 808 and a sampler note on A3."""
    return {
        "version": 1,
        "bpm": 180,
        "swing": 50,
        "seed": SEED,
        "key": "A minor",
        "sounds": {
            "kick": {"instrument": "kick"},
            "hat": {"instrument": "hat_closed"},
            "keys": {"instrument": "sampler", "library": LIB},
        },
        "patterns": {
            "drums": {"bars": 1, "rows": {"kick": kick, "hat": "x.x.x.x.x.x.x.x."}},
            "melody": {
                "bars": 1,
                "rows": {"keys": [{"step": 0, "len": 8, "note": "A3", "vel": 0.8}]},
            },
        },
        "sections": [{"name": "loop", "bars": 2, "play": ["drums", "melody"]}],
    }


def tone(seconds, freq):
    t = np.arange(int(seconds * ZONE_RATE)) / ZONE_RATE
    return (0.5 * np.sin(2 * np.pi * freq * t) * np.exp(-t * 3) * 32767).astype(np.int16)


def entry(name, files, kind="fetch", commercial=True):
    return {
        "name": name,
        "source": "https://github.com/someone/samples",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "licence": "CC0-1.0" if commercial else "CC-BY-NC-4.0",
        "licence_quote": "a test licence",
        "kind": kind,
        "commercial": commercial,
        "files": files,
    }


def wav_entry(folder, path, note, samples):
    """Write a WAV under folder and return its manifest entry."""
    target = folder / path
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, samples, ZONE_RATE, subtype="PCM_16")
    data = target.read_bytes()
    return {
        "path": path,
        "git_sha1": git_sha1(data),
        "size": len(data),
        "note": note,
        "vel": [0, 127],
    }


@pytest.fixture
def place(tmp_path):
    """A seed folder with a recipe taking 1-8 bars, score-1.json and a sound manifest."""
    root, work = tmp_path / "beats", tmp_path / "work"
    folder = root / str(SEED)
    folder.mkdir(parents=True)
    recipe = dataclasses.replace(
        make_recipe(SEED, genre="trap", bpm=180, key="A minor"), bars=(1, 8)
    )
    (folder / "recipe.json").write_text(recipe_json(recipe), encoding="utf-8")
    (folder / "score-1.json").write_text(json.dumps(score_body(), indent=1), encoding="utf-8")
    sounds = work / "sounds"
    keys = [
        wav_entry(sounds / LIB, "a4.wav", 69, tone(0.5, 440)),
        wav_entry(sounds / LIB, "a5.wav", 81, tone(0.4, 880)),
    ]
    strings = wav_entry(tmp_path / "elsewhere", "c4.wav", 60, tone(0.3, 262))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": [
                    entry(LIB, keys),
                    entry(UNFETCHED, [strings], commercial=False),
                    {**entry(OWN, [], kind="local_only", commercial=False), "commit": ""},
                ]
            }
        ),
        encoding="utf-8",
    )
    return root, work, manifest


def serve(beat, site):
    httpd = beat_server.make_server(beat, TOKEN, port=0, site=site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def dist(tmp_path):
    site = tmp_path / "dist"
    (site / "assets").mkdir(parents=True)
    (site / "beat.html").write_bytes(PAGE)
    (site / "index.html").write_bytes(b"<!doctype html><title>studio</title>")
    (site / "assets" / "beat.js").write_text("console.log(1)", encoding="utf-8")
    (site / "secret.py").write_text("x = 1", encoding="utf-8")
    return site


@pytest.fixture
def server(place, dist):
    root, work, manifest = place
    beat = beat_server.BeatStudio(SEED, root, work, manifest=manifest)
    httpd = serve(beat, dist)
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


def error_of(reply):
    status, headers, body = reply
    assert headers["Content-Type"] == "application/json"
    return status, json.loads(body)["error"]


def zone_path(library=LIB, file="0"):
    return f"/api/beat/zone?library={library}&file={file}"


# guards


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x"])
@pytest.mark.parametrize("path", ["/api/beat/session", zone_path(), "/api/beat/render/status"])
def test_the_api_refuses_a_missing_or_wrong_token(server, token, path):
    status, message = error_of(call(server, "GET", path, token=token))
    assert status == 403 and message == "no token"


def test_a_post_without_the_token_writes_nothing(server, place):
    root, _, _ = place
    reply = call(
        server,
        "POST",
        "/api/beat/save",
        {"score": score_body(), "base": "score-1.json"},
        token=None,
    )
    assert reply[0] == 403
    assert not (root / str(SEED) / "score-2.json").exists()


@pytest.mark.parametrize("host", ["evil.com", "127.0.0.1:1", "localhost"])
def test_a_foreign_host_is_refused(server, host):
    assert call(server, "GET", "/api/beat/session", host=host)[0] == 403


def test_the_server_binds_this_machine_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_posts_take_json_only_and_a_bounded_body(server):
    port = server.server_address[1]
    status, message = error_of(
        call(server, "POST", "/api/beat/check", b"{}", headers={"Content-Type": "text/plain"})
    )
    assert status == 415 and message == "send JSON"
    # the length alone is refused, before a byte of the body is read
    over = {"Content-Length": str(4 * 1024 * 1024 + 1)}
    assert error_of(call(server, "POST", "/api/beat/check", b"{}", headers=over))[0] == 413
    assert error_of(call(server, "POST", "/api/beat/check", b"[1]"))[0] == 400
    assert port


def test_an_unknown_call_is_a_json_error(server):
    assert error_of(call(server, "GET", "/api/beat/nothing")) == (404, "no such call")
    assert error_of(call(server, "POST", "/api/beat/nothing", {}))[0] == 404


# session


def test_the_session_carries_the_score_recipe_tables_and_libraries(server, place):
    root, _, _ = place
    folder = root / str(SEED)
    text = (folder / "score-1.json").read_text(encoding="utf-8")
    status, headers, body = call(server, "GET", "/api/beat/session")
    assert status == 200 and headers["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["seed"] == SEED
    assert data["score_file"] == "score-1.json"
    assert data["score"] == score_body()
    assert data["recipe"] == json.loads((folder / "recipe.json").read_text(encoding="utf-8"))
    assert data["recipe"]["bars"] == [1, 8]
    assert data["instruments"] == json.loads(redline_dsp.beat_instruments())
    every = json.loads(redline_dsp.rack_effects())
    assert data["effects"] == [e for e in every if not e["offline_only"]]
    assert any(e["offline_only"] for e in every) and len(data["effects"]) < len(every)
    assert data["rate"] == RATE
    assert data["events"] == json.loads(redline_dsp.beat_events(text, float(RATE)))
    assert data["marks"] == []
    [lib] = data["libraries"]
    assert lib["name"] == LIB and lib["licence"] == "CC0-1.0" and lib["commercial"] is True
    assert lib["fetched"] is True and lib["sounds"] == ["keys"]
    # the score plays A3 only: the A4 root is the nearest, the A5 zone is not needed
    frames = len(tone(0.5, 440))
    assert lib["zones"] == [
        {
            "library": LIB,
            "file": "0",
            "root": 69,
            "vel": [0, 127],
            "rate": ZONE_RATE,
            "channels": 1,
            "frames": frames,
        }
    ]


def test_the_session_names_a_library_not_fetched(server, place):
    root, _, _ = place
    score = score_body()
    score["sounds"]["keys"]["library"] = UNFETCHED
    (root / str(SEED) / "score-2.json").write_text(json.dumps(score), encoding="utf-8")
    data = json.loads(call(server, "GET", "/api/beat/session")[2])
    assert data["score_file"] == "score-2.json"
    [lib] = data["libraries"]
    assert lib["name"] == UNFETCHED and lib["commercial"] is False
    assert lib["fetched"] is False and lib["zones"] == [] and "missing" in lib["error"]


def test_the_session_names_a_library_not_in_the_manifest(server, place):
    root, _, _ = place
    score = score_body()
    score["sounds"]["keys"]["library"] = "nowhere"
    (root / str(SEED) / "score-2.json").write_text(json.dumps(score), encoding="utf-8")
    [lib] = json.loads(call(server, "GET", "/api/beat/session")[2])["libraries"]
    assert lib["name"] == "nowhere" and lib["fetched"] is False and lib["commercial"] is False
    assert lib["zones"] == [] and "not in the sound library" in lib["error"]


def test_an_unexpected_failure_is_still_a_json_error(server, monkeypatch):
    def broken(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(beat_server.BeatStudio, "session", broken)
    status, message = error_of(call(server, "GET", "/api/beat/session"))
    assert status == 500 and "disk on fire" in message
    # the server keeps answering
    assert call(server, "GET", "/api/beat/render/status")[0] == 200


def test_the_session_without_a_saved_score_is_a_json_error(place, dist):
    root, work, manifest = place
    (root / str(SEED) / "score-1.json").unlink()
    httpd = serve(beat_server.BeatStudio(SEED, root, work, manifest=manifest), dist)
    try:
        status, message = error_of(call(httpd, "GET", "/api/beat/session"))
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 404 and "no saved score" in message


# zones


def test_a_zone_is_served_as_int16_pcm(server):
    status, headers, body = call(server, "GET", zone_path(file="1"))
    assert status == 200 and headers["Content-Type"] == "application/octet-stream"
    assert headers["X-Rate"] == str(ZONE_RATE) and headers["X-Channels"] == "1"
    expected = tone(0.4, 880)
    assert headers["X-Frames"] == str(len(expected))
    assert np.array_equal(np.frombuffer(body, "<i2"), expected)


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("library=nobody&file=0", 404),
        (f"library={LIB}&file=2", 404),
        (f"library={LIB}&file=-1", 400),
        (f"library={LIB}&file=a4.wav", 400),
        (f"library={LIB}&file=../a4.wav", 400),
        (f"library={LIB}&file=0&file=1", 400),
        (f"library={LIB}", 400),
        (f"library={UNFETCHED}&file=0", 404),
        (f"library={OWN}&file=0", 403),
    ],
)
def test_a_zone_outside_the_manifest_or_not_fetched_is_refused(server, query, status):
    got, message = error_of(call(server, "GET", f"/api/beat/zone?{query}"))
    assert got == status, message


def test_a_zone_symlinked_out_of_its_folder_is_refused(server, place, tmp_path):
    _, work, _ = place
    zone = work / "sounds" / LIB / "a4.wav"
    # the same bytes, so only the path guard can refuse it
    outside = tmp_path / "outside.wav"
    outside.write_bytes(zone.read_bytes())
    zone.unlink()
    zone.symlink_to(outside)
    status, message = error_of(call(server, "GET", zone_path()))
    assert status == 403 and "outside" in message


def test_a_tampered_zone_is_refused(server, place):
    _, work, _ = place
    zone = work / "sounds" / LIB / "a4.wav"
    data = bytearray(zone.read_bytes())
    data[-1] ^= 0xFF
    zone.write_bytes(bytes(data))
    status, message = error_of(call(server, "GET", zone_path()))
    assert status == 409 and "hash" in message
    assert call(server, "GET", zone_path(file="1"))[0] == 200


# check and save


def test_check_lists_errors_with_paths_and_saves_nothing(server, place):
    root, _, _ = place
    reply = call(server, "POST", "/api/beat/check", {"score": score_body(kick="x...x...")})
    assert reply[0] == 200
    data = json.loads(reply[2])
    assert any(e.startswith("patterns.drums.rows.kick:") for e in data["errors"]), data
    assert sorted(p.name for p in (root / str(SEED)).glob("score-*.json")) == ["score-1.json"]


def test_check_of_a_good_score_has_no_errors(server):
    data = json.loads(call(server, "POST", "/api/beat/check", {"score": score_body()})[2])
    assert data["errors"] == []
    assert all(set(w) == {"kind", "where", "message", "source", "text"} for w in data["warnings"])


def test_check_refuses_a_score_that_is_not_an_object(server):
    assert error_of(call(server, "POST", "/api/beat/check", {"score": "{}"}))[0] == 400
    assert error_of(call(server, "POST", "/api/beat/check", {"score": {}, "x": 1}))[0] == 400


def test_save_writes_the_next_score(server, place):
    root, _, _ = place
    edited = score_body(kick="x.......x.x.....")
    status, _, body = call(
        server, "POST", "/api/beat/save", {"score": edited, "base": "score-1.json"}
    )
    assert status == 200
    data = json.loads(body)
    assert data["file"] == "score-2.json" and isinstance(data["warnings"], list)
    saved = root / str(SEED) / "score-2.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == edited
    assert saved.stat().st_mode & 0o777 == 0o600
    assert json.loads((root / str(SEED) / "score-1.json").read_text()) == score_body()
    session = json.loads(call(server, "GET", "/api/beat/session")[2])
    assert session["score_file"] == "score-2.json" and session["score"] == edited


def test_save_refuses_an_invalid_score_and_writes_nothing(server, place):
    root, _, _ = place
    bad = score_body(kick="x...x...")
    bad["key"] = "C major"
    status, headers, body = call(
        server, "POST", "/api/beat/save", {"score": bad, "base": "score-1.json"}
    )
    assert status == 422 and headers["Content-Type"] == "application/json"
    data = json.loads(body)
    assert data["error"]
    assert any(e.startswith("patterns.drums.rows.kick:") for e in data["errors"])
    assert any(e.startswith("key:") for e in data["errors"])
    names = sorted(p.name for p in (root / str(SEED)).iterdir())
    assert names == ["recipe.json", "score-1.json"]


def test_save_on_a_stale_base_is_a_conflict(server, place):
    root, _, _ = place
    first = call(server, "POST", "/api/beat/save", {"score": score_body(), "base": "score-1.json"})
    assert first[0] == 200
    status, message = error_of(
        call(server, "POST", "/api/beat/save", {"score": score_body(), "base": "score-1.json"})
    )
    assert status == 409 and "score-2.json" in message
    assert not (root / str(SEED) / "score-3.json").exists()


@pytest.mark.parametrize("base", ["../score-1.json", "score-1", "recipe.json", 1, None])
def test_save_refuses_a_base_that_is_not_a_score_name(server, place, base):
    root, _, _ = place
    status, _ = error_of(
        call(server, "POST", "/api/beat/save", {"score": score_body(), "base": base})
    )
    assert status == 400
    assert not (root / str(SEED) / "score-2.json").exists()


def test_save_never_overwrites_a_score_written_meanwhile(place, monkeypatch):
    root, work, manifest = place
    beat = beat_server.BeatStudio(SEED, root, work, manifest=manifest)
    folder = root / str(SEED)
    real = beat_server.latest_score

    # another writer takes score-2.json between the stale check and the write
    def racing(where):
        found = real(where)
        if not (folder / "score-2.json").exists():
            (folder / "score-2.json").write_text("{}", encoding="utf-8")
        return found

    monkeypatch.setattr(beat_server, "latest_score", racing)
    name, _ = beat.save(score_body(), "score-1.json")
    assert name == "score-3.json"
    assert (folder / "score-2.json").read_text(encoding="utf-8") == "{}"
    assert [p.name for p in folder.iterdir() if p.name.endswith(".tmp")] == []


# marks


def test_a_mark_is_appended_and_shows_in_the_session(server, place):
    root, _, _ = place
    status, _, body = call(
        server,
        "POST",
        "/api/beat/mark",
        {"bar": 2, "verdict": "bad", "note": "too busy", "base": "score-1.json"},
    )
    assert status == 200
    mark = json.loads(body)
    assert (mark["seed"], mark["bar"], mark["verdict"], mark["note"]) == (
        SEED,
        2,
        "bad",
        "too busy",
    )
    assert mark["score"] == "score-1.json" and mark["genre"] == "trap"
    assert mark["excerpt"].startswith("bar 2 of 2")
    lines = (root / "marks.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [mark]
    call(server, "POST", "/api/beat/mark", {"bar": 1, "verdict": "good", "base": "score-1.json"})
    session = json.loads(call(server, "GET", "/api/beat/session")[2])
    assert [m["bar"] for m in session["marks"]] == [2, 1]


@pytest.mark.parametrize(
    "body",
    [
        {"bar": 3, "verdict": "bad"},
        {"bar": 0, "verdict": "bad"},
        {"bar": True, "verdict": "bad"},
        {"bar": 1, "verdict": "meh"},
        {"bar": 1, "verdict": "bad", "note": 5},
        {"bar": 1, "verdict": "bad", "note": "x" * 2001},
        {"bar": 1, "verdict": "bad", "extra": 1},
        {"bar": 1, "verdict": "bad", "base": None},
        {"bar": 1, "verdict": "bad", "base": "../score-1.json"},
        {"bar": 1, "verdict": "bad", "base": "recipe.json"},
        {"bar": 1, "verdict": "bad", "base": 1},
    ],
)
def test_a_bad_mark_is_refused_and_not_written(server, place, body):
    root, _, _ = place
    body = {"base": "score-1.json", **body}
    if body["base"] is None:
        del body["base"]
    assert error_of(call(server, "POST", "/api/beat/mark", body))[0] == 400
    assert not (root / "marks.jsonl").exists()


def test_a_mark_on_a_score_that_is_not_the_newest_is_a_conflict(server, place):
    root, _, _ = place
    edited = score_body(kick="x.......x.x.....")
    saved = call(server, "POST", "/api/beat/save", {"score": edited, "base": "score-1.json"})
    assert saved[0] == 200
    stale = {"bar": 1, "verdict": "bad", "base": "score-1.json"}
    status, message = error_of(call(server, "POST", "/api/beat/mark", stale))
    assert status == 409 and "score-2.json" in message
    assert not (root / "marks.jsonl").exists()
    # a base that was never saved is no newer score either
    missing = {"bar": 1, "verdict": "bad", "base": "score-9.json"}
    assert error_of(call(server, "POST", "/api/beat/mark", missing))[0] == 409
    status, _, body = call(
        server, "POST", "/api/beat/mark", {"bar": 1, "verdict": "good", "base": "score-2.json"}
    )
    assert status == 200
    mark = json.loads(body)
    assert mark["score"] == "score-2.json"
    assert mark["excerpt"].splitlines()[1].startswith("kick: x.......x.x.....")


# render


def wait_render(server, timeout=120.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = json.loads(call(server, "GET", "/api/beat/render/status")[2])
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError("the render did not finish")


def test_render_writes_the_beat_and_serves_it(server, place):
    _, work, _ = place
    status, _, body = call(server, "POST", "/api/beat/render", {"score_file": "score-1.json"})
    assert status == 202 and json.loads(body)["state"] == "running"
    done = wait_render(server)
    assert done["state"] == "done", done
    assert done["score_file"] == "score-1.json" and done["non_commercial"] == []
    assert (work / "beats" / str(SEED) / "beat.wav").is_file()
    status, headers, wav = call(server, "GET", "/api/beat/render?file=beat.wav")
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    samples, rate = sf.read(io.BytesIO(wav), always_2d=True)
    two_bars = 2 * 4 * 60 / 180
    assert rate == RATE and len(samples) >= int(two_bars * RATE) and np.abs(samples).max() > 0.01
    stem = call(server, "GET", "/api/beat/render?file=stems/kick.wav")
    assert stem[0] == 200
    report = call(server, "GET", "/api/beat/render?file=render.json")
    assert report[0] == 200 and json.loads(report[2])["score"] == "score-1.json"


def test_render_files_outside_the_fixed_set_are_refused(server, place, tmp_path):
    _, work, _ = place
    folder = work / "beats" / str(SEED)
    (folder / "stems").mkdir(parents=True)
    (folder / "notes.txt").write_text("private", encoding="utf-8")
    (folder / "stems" / "kick.wav").write_bytes(b"RIFF")
    (tmp_path / "outside.wav").write_bytes(b"RIFF")
    for name in (
        "notes.txt",
        "../../../outside.wav",
        "/etc/passwd",
        "stems/../notes.txt",
        "stems/kick.wav",
        "open.html",
    ):
        status, _ = error_of(call(server, "GET", f"/api/beat/render?file={name}"))
        assert status in (400, 404), name
    # no render has listed a stem yet
    assert error_of(call(server, "GET", "/api/beat/render?file=stems/kick.wav"))[0] == 400


def test_a_served_render_file_symlinked_out_is_refused(server, place, tmp_path):
    _, work, _ = place
    folder = work / "beats" / str(SEED)
    folder.mkdir(parents=True)
    (tmp_path / "outside.wav").write_bytes(b"RIFF")
    (folder / "beat.wav").symlink_to(tmp_path / "outside.wav")
    status, _ = error_of(call(server, "GET", "/api/beat/render?file=beat.wav"))
    assert status == 403


@pytest.mark.parametrize("name", ["../score-1.json", "score-9.json", "recipe.json", 3])
def test_render_refuses_a_score_file_not_saved_in_the_seed(server, name):
    status, _ = error_of(call(server, "POST", "/api/beat/render", {"score_file": name}))
    assert status in (400, 404)


def test_a_second_render_while_one_runs_is_a_conflict(place, dist):
    root, work, manifest = place
    started, release = threading.Event(), threading.Event()
    seen = []

    def slow(seed, root_, work_, rate, score, manifest):
        seen.append((seed, root_, work_, rate, score.name, manifest))
        started.set()
        release.wait(30)
        raise beat_server.RenderError("stopped by the test")

    beat = beat_server.BeatStudio(SEED, root, work, manifest=manifest, render=slow)
    httpd = serve(beat, dist)
    try:
        assert call(httpd, "POST", "/api/beat/render", {"score_file": "score-1.json"})[0] == 202
        assert started.wait(10)
        second = call(httpd, "POST", "/api/beat/render", {"score_file": "score-1.json"})
        assert error_of(second)[0] == 409
        assert error_of(call(httpd, "GET", "/api/beat/render?file=beat.wav"))[0] == 409
        release.set()
        done = wait_render(httpd)
        assert done["state"] == "failed" and "stopped by the test" in done["error"]
        assert seen == [(SEED, root, work, RATE, "score-1.json", manifest)]
        # the lock is free again
        assert call(httpd, "POST", "/api/beat/render", {"score_file": "score-1.json"})[0] == 202
        wait_render(httpd)
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


# page


def test_the_page_is_served_with_its_token_and_assets(server):
    status, headers, body = call(server, "GET", f"/?t={TOKEN}", token=None)
    assert status == 200 and body == PAGE
    assert "wasm-unsafe-eval" in headers["Content-Security-Policy"]
    assert call(server, "GET", "/", token=None)[0] == 403
    assert call(server, "GET", "/assets/beat.js", token=None)[0] == 200
    # the html pages are reached at / only, behind the token
    assert call(server, "GET", "/beat.html", token=None)[0] == 404
    assert call(server, "GET", "/index.html", token=None)[0] == 403
    assert call(server, "GET", "/secret.py", token=None)[0] == 404


def test_the_page_not_built_says_how_to_build_it(place, tmp_path):
    root, work, manifest = place
    httpd = serve(beat_server.BeatStudio(SEED, root, work, manifest=manifest), tmp_path / "none")
    try:
        status, message = error_of(call(httpd, "GET", f"/?t={TOKEN}", token=None))
        assert status == 404 and "npm run build" in message and "apps/studio" in message
        assert call(httpd, "GET", "/", token=None)[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


# command line


def test_the_studio_command_parses():
    args = build_parser().parse_args(["beat", "studio", "--seed", "3", "--no-open"])
    assert (args.beat_command, args.seed, args.no_open, args.port) == ("studio", 3, True, 0)
    assert str(args.root) == "private/beats" and str(args.work) == "work"


def test_the_studio_command_prints_the_address_with_the_token(place, monkeypatch, capsys):
    root, work, _ = place

    def stop(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(beat_server.BeatServer, "serve_forever", stop)
    code = main(
        [
            "beat",
            "studio",
            "--seed",
            str(SEED),
            "--root",
            str(root),
            "--work",
            str(work),
            "--no-open",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "http://127.0.0.1:" in out and "/?t=" in out


def test_the_studio_command_refuses_a_seed_without_a_score(tmp_path, capsys):
    code = main(["beat", "studio", "--seed", "99", "--root", str(tmp_path), "--no-open"])
    assert code == 2 and "no saved score" in capsys.readouterr().err


def test_a_long_score_fits_the_body_limit(server):
    # the per-step arrays of 64 patterns of 8 bars run past the 64 KB the studio takes
    big = score_body()
    big["patterns"]["drums"]["rows"]["kick"] = {
        "steps": "x...x...x...x...",
        "velocity": [0.123456789] * 16,
    }
    padding = {"note": "x" * 300_000}
    status, _, body = call(server, "POST", "/api/beat/check", {"score": big, **padding})
    assert status != 413, body
