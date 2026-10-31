import dataclasses
import functools
import hashlib
import io
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.beat.recipe import make_recipe, recipe_json
from redline.engine.settings import from_dict, to_dict
from redline.song import model
from redline.song.app import AppHost
from redline.song.server import SongApp, make_server
from test_redline_song_server import NAME, TOKEN, FakeRec, api, call, fake_studio, tone, until

SEED = 7
SLEEP = "import time; time.sleep(60)"
WRITE = (
    "import pathlib, sys; r = pathlib.Path(sys.argv[1]); "
    "len(sys.argv) > 3 and (r.parent.parent / 'variants.json').write_text(sys.argv[3]); "
    "r.write_text(sys.argv[2])"
)
FAIL = "import sys; print('redline: the {} broke', file=sys.stderr); sys.exit(2)"


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    sf.write(base / "input" / "beat.wav", tone(2.0), 8000)
    sf.write(base / "input" / "ref.wav", tone(), 8000)
    sf.write(base / "input" / "outside.wav", tone(), 8000)
    takes = base / "input" / "takes" / NAME
    takes.mkdir(parents=True)
    sf.write(takes / "take-01.wav", tone(), 8000)
    return base


def current_key(name=NAME):
    return model.mix_inputs(model.load(name)).key()


@pytest.fixture
def daw(root, tmp_path):
    model.save(model.new_song(NAME, {"file": "input/beat.wav"}, None, ("input/ref.wav",)), 0)
    plan: dict[str, str] = {}
    calls: list[str] = []
    variants_key: list[str | None] = [None]

    def argv(kind, name, seed, result):
        calls.append(kind)
        mode = plan.get(kind, "ok")
        if mode == "sleep":
            return [sys.executable, "-c", SLEEP]
        if mode == "fail":
            return [sys.executable, "-c", FAIL.format(kind)]
        out = [sys.executable, "-c", WRITE, str(result), json.dumps({"kind": kind})]
        if kind == "separate":
            out[-1] = json.dumps({"kind": kind, "key": current_key(name)})
        if kind == "auto":
            variants = {
                "key": variants_key[0] or current_key(name),
                "auto": {"formant": 1.1},
                "variants": [],
                "seed": seed,
            }
            out.append(json.dumps(variants))
        return out

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "daw.html").write_bytes(b"<!doctype html><title>daw</title>")
    host = AppHost(
        make_rec=FakeRec,
        make_app=functools.partial(
            SongApp, argv=argv, build_studio=fake_studio, assets=lambda: (None, None, str)
        ),
        kill_after=0.3,
    )
    host.open(NAME)
    httpd = make_server(host, TOKEN, port=0, site=dist)
    httpd.plan, httpd.calls, httpd.variants_key = plan, calls, variants_key
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    host.jobs.close(timeout=5)
    httpd.server_close()


def choose_take(daw):
    song = model.load(NAME)
    status, _ = api(
        daw, "POST", "/api/song", {"rev": song.rev, "set": {"voice_chosen": "take-01.wav"}}
    )
    assert status == 200


def mark_separated(daw):
    marker = daw.app._result_path("separate")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"key": current_key()}), encoding="utf-8")


def set_beat(daw, beat):
    rev = model.load(NAME).rev
    assert api(daw, "POST", "/api/song", {"rev": rev, "set": {"beat": beat}})[0] == 200


def render_beat(root, seed, samples, rate):
    path = root / "work" / "beats" / str(seed) / "beat.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, samples, rate)
    return path


def score_body(kick="x...x...x...x..."):
    return {
        "version": 1,
        "bpm": 180,
        "swing": 50,
        "seed": SEED,
        "key": "A minor",
        "sounds": {"kick": {"instrument": "kick"}, "hat": {"instrument": "hat_closed"}},
        "patterns": {
            "drums": {"bars": 1, "rows": {"kick": kick, "hat": "x.x.x.x.x.x.x.x."}},
        },
        "sections": [{"name": "loop", "bars": 2, "play": ["drums"]}],
    }


def seed_folder(root, seed=SEED):
    folder = root / "private" / "beats" / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    recipe = dataclasses.replace(
        make_recipe(seed, genre="trap", bpm=180, key="A minor"), bars=(1, 8)
    )
    (folder / "recipe.json").write_text(recipe_json(recipe), encoding="utf-8")
    (folder / "score-1.json").write_text(json.dumps(score_body(), indent=1), encoding="utf-8")
    return folder


def facts(daw):
    status, data = api(daw, "GET", "/api/song")
    assert status == 200
    return data["facts"]


def jobs_of(daw, kind):
    listed = api(daw, "GET", "/api/jobs")[1]["jobs"]
    return next((j for j in listed if j["kind"] == kind), None)


def wav(body):
    return sf.read(io.BytesIO(body), always_2d=True, dtype="float32"), sf.info(io.BytesIO(body))


# beat audio


def test_the_beat_audio_of_a_file_beat_is_16_bit_stereo_at_its_own_rate(daw, root):
    status, headers, body = call(daw, "GET", "/api/song/beat-audio")
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    (samples, rate), info = wav(body)
    assert info.subtype == "PCM_16" and info.channels == 2 and rate == 8000
    expected = tone(2.0)
    assert len(samples) == len(expected)
    assert np.max(np.abs(samples[:, 0] - expected)) < 2 / 32767
    assert np.array_equal(samples[:, 0], samples[:, 1])


def test_the_beat_audio_of_an_own_seed_is_409_until_it_is_rendered(daw, root):
    set_beat(daw, {"own": 4})
    status, data = api(daw, "GET", "/api/song/beat-audio")
    assert status == 409 and data["code"] == "no_render"
    stereo = np.stack([tone(0.5), -tone(0.5)], axis=1)
    render_beat(root, 4, stereo, 48000)
    status, _, body = call(daw, "GET", "/api/song/beat-audio")
    (samples, rate), info = wav(body)
    assert status == 200 and rate == 48000 and info.channels == 2
    assert np.max(np.abs(samples - stereo)) < 2 / 32767


def test_the_beat_audio_of_a_flip_render_is_its_wav(daw, root):
    rel = "work/flips/0123abcd/flip-2.wav"
    path = root / rel
    path.parent.mkdir(parents=True)
    stereo = np.stack([tone(0.25), tone(0.25) * 0.5], axis=1)
    sf.write(path, stereo, 44100)
    set_beat(daw, {"file": rel})
    status, _, body = call(daw, "GET", "/api/song/beat-audio")
    (samples, rate), _ = wav(body)
    assert status == 200 and rate == 44100
    assert np.max(np.abs(samples - stereo)) < 2 / 32767
    path.unlink()
    assert api(daw, "GET", "/api/song/beat-audio")[0] == 409


# peaks


def ramp_take(root, frames=8000, name="take-01.wav"):
    left = np.linspace(-0.8, 0.8, frames, dtype=np.float32)
    right = np.sin(np.arange(frames) / 7.0).astype(np.float32) * 0.5
    path = root / "input" / "takes" / NAME / name
    sf.write(path, np.stack([left, right], axis=1), 8000)
    return path


def test_take_peaks_are_n_min_max_pairs_of_the_mono_file(daw, root):
    path = ramp_take(root)
    status, data = api(daw, "GET", "/api/peaks?of=take&name=take-01.wav&n=200")
    assert status == 200
    assert data["rate"] == 8000 and data["duration_s"] == pytest.approx(1.0)
    read, _ = sf.read(path, always_2d=True, dtype="float32")
    bins = read.mean(axis=1).reshape(200, 40)
    expected = np.stack([bins.min(axis=1), bins.max(axis=1)], axis=1)
    assert np.array(data["peaks"]).shape == (200, 2)
    assert np.max(np.abs(np.array(data["peaks"]) - expected)) < 1e-3


def test_beat_peaks_follow_the_songs_beat(daw, root):
    status, data = api(daw, "GET", "/api/peaks?of=beat&n=400")
    assert status == 200 and data["rate"] == 8000 and len(data["peaks"]) == 400
    assert data["duration_s"] == pytest.approx(2.0)
    top = max(p[1] for p in data["peaks"])
    assert top == pytest.approx(0.3, abs=2e-3)
    set_beat(daw, {"own": 4})
    status, data = api(daw, "GET", "/api/peaks?of=beat")
    assert status == 409 and data["code"] == "no_render"


@pytest.mark.parametrize(
    ("asked", "points"),
    [
        ("", 2000),
        ("&n=5", 200),
        ("&n=0", 200),
        ("&n=200", 200),
        ("&n=8000", 8000),
        ("&n=99999", 8000),
    ],
)
def test_the_peak_count_is_clamped_to_200_8000(daw, root, asked, points):
    ramp_take(root, frames=16000)
    status, data = api(daw, "GET", f"/api/peaks?of=take&name=take-01.wav{asked}")
    assert status == 200 and len(data["peaks"]) == points


def test_peaks_are_cached_by_the_files_sha256(daw, root):
    path = ramp_take(root)
    first = api(daw, "GET", "/api/peaks?of=take&name=take-01.wav&n=200")[1]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    folder = root / "work" / "songs" / NAME / "peaks"
    cached = [p for p in folder.iterdir() if digest in p.name]
    assert len(cached) == 1 and cached[0].stat().st_mode & 0o777 == 0o600
    assert json.loads(cached[0].read_text())["peaks"] == first["peaks"]
    planted = {**first, "peaks": [[0.0, 0.123]] * 200}
    cached[0].write_text(json.dumps(planted), encoding="utf-8")
    again = api(daw, "GET", "/api/peaks?of=take&name=take-01.wav&n=200")[1]
    assert again["peaks"] == planted["peaks"]
    ramp_take(root, frames=4000)
    fresh = api(daw, "GET", "/api/peaks?of=take&name=take-01.wav&n=200")[1]
    assert fresh["peaks"] != planted["peaks"] and fresh["duration_s"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("query", "status"),
    [
        ("", 400),
        ("of=vocal", 400),
        ("of=take", 400),
        ("of=take&name=../beat.wav", 400),
        ("of=take&name=..%2Fbeat.wav", 400),
        ("of=take&name=notes.wav", 400),
        ("of=take&name=take-01.wav&n=abc", 400),
        ("of=take&name=take-01.wav&n=-3", 400),
        ("of=beat&name=take-01.wav", 400),
    ],
)
def test_bad_peak_requests_are_refused(daw, root, query, status):
    assert api(daw, "GET", f"/api/peaks?{query}")[0] == status


def test_a_missing_take_is_404_no_take(daw):
    status, data = api(daw, "GET", "/api/peaks?of=take&name=take-09.wav")
    assert status == 404 and data["code"] == "no_take"


def test_a_take_that_resolves_out_of_its_folder_is_409(daw, root):
    (root / "input" / "takes" / NAME / "take-02.wav").symlink_to(root / "input" / "outside.wav")
    assert api(daw, "GET", "/api/peaks?of=take&name=take-02.wav")[0] == 409


# own-beat draft


def own_seed(daw, root):
    folder = seed_folder(root)
    set_beat(daw, {"own": SEED})
    return folder


def test_the_draft_is_409_not_own_for_a_file_beat(daw):
    status, data = api(daw, "GET", "/api/beat/draft")
    assert status == 409 and data["code"] == "not_own"
    status, data = api(daw, "POST", "/api/beat/draft", {"score": score_body(), "base": "x"})
    assert status == 409 and data["code"] == "not_own"


def test_without_a_draft_the_newest_score_is_given(daw, root):
    own_seed(daw, root)
    status, data = api(daw, "GET", "/api/beat/draft")
    assert status == 200 and data == {"score": score_body(), "base": "score-1.json"}


def test_a_draft_is_kept_private_and_given_back(daw, root):
    folder = own_seed(daw, root)
    edited = score_body("x.x.x...x...x...")
    status, data = api(daw, "POST", "/api/beat/draft", {"score": edited, "base": "score-1.json"})
    assert status == 200 and data == {"ok": True}
    draft = folder / "draft.json"
    assert draft.stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in folder.iterdir()) == ["draft.json", "recipe.json", "score-1.json"]
    assert api(daw, "GET", "/api/beat/draft")[1] == {"score": edited, "base": "score-1.json"}
    again = score_body("xxxx............")
    api(daw, "POST", "/api/beat/draft", {"score": again, "base": "score-1.json"})
    assert api(daw, "GET", "/api/beat/draft")[1]["score"] == again


def test_a_draft_over_an_old_score_is_409_stale_base(daw, root):
    folder = own_seed(daw, root)
    api(
        daw,
        "POST",
        "/api/beat/draft",
        {"score": score_body("x..............."), "base": "score-1.json"},
    )
    newer = score_body("..x.............")
    (folder / "score-2.json").write_text(json.dumps(newer), encoding="utf-8")
    body = {"score": score_body(), "base": "score-1.json"}
    status, data = api(daw, "POST", "/api/beat/draft", body)
    assert status == 409 and data["code"] == "stale_base"
    assert api(daw, "GET", "/api/beat/draft")[1] == {"score": newer, "base": "score-2.json"}


def test_a_score_rust_refuses_is_422_and_the_draft_stays(daw, root):
    folder = own_seed(daw, root)
    kept = score_body("x...............")
    api(daw, "POST", "/api/beat/draft", {"score": kept, "base": "score-1.json"})
    broken = {**score_body(), "sections": [{"name": "loop", "bars": 2, "play": ["nothing"]}]}
    status, data = api(daw, "POST", "/api/beat/draft", {"score": broken, "base": "score-1.json"})
    assert status == 422 and data["errors"]
    assert json.loads((folder / "draft.json").read_text())["score"] == kept


@pytest.mark.parametrize(
    "body",
    [
        {"score": [], "base": "score-1.json"},
        {"score": score_body(), "base": "draft.json"},
        {"score": score_body(), "base": 1},
        {"score": score_body()},
        {"score": score_body(), "base": "score-1.json", "more": 1},
    ],
)
def test_a_bad_draft_body_is_400(daw, root, body):
    folder = own_seed(daw, root)
    assert api(daw, "POST", "/api/beat/draft", body)[0] == 400
    assert not (folder / "draft.json").exists()


def test_a_save_removes_the_draft_and_a_refused_save_keeps_it(daw, root):
    folder = own_seed(daw, root)
    edited = score_body("x.x.x...x...x...")
    api(daw, "POST", "/api/beat/draft", {"score": edited, "base": "score-1.json"})
    off_key = {**edited, "key": "C# major"}
    status, _ = api(daw, "POST", "/api/beat/save", {"score": off_key, "base": "score-1.json"})
    assert status == 422 and (folder / "draft.json").exists()
    status, data = api(daw, "POST", "/api/beat/save", {"score": edited, "base": "score-1.json"})
    assert status == 200 and data["file"] == "score-2.json"
    assert not (folder / "draft.json").exists()


# layout


def test_the_layout_is_empty_until_saved_then_kept_private(daw, root):
    assert api(daw, "GET", "/api/song/layout") == (200, {"start_bar": None, "bars_per_line": None})
    status, data = api(daw, "POST", "/api/song/layout", {"start_bar": 4, "bars_per_line": 2})
    assert status == 200 and data == {"start_bar": 4, "bars_per_line": 2}
    path = root / "private" / "songs" / NAME / "layout.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert api(daw, "GET", "/api/song/layout")[1] == {"start_bar": 4, "bars_per_line": 2}
    body = {"start_bar": None, "bars_per_line": None}
    assert api(daw, "POST", "/api/song/layout", body) == (200, body)
    assert api(daw, "GET", "/api/song/layout")[1] == body


@pytest.mark.parametrize(
    "body",
    [
        {"start_bar": 0, "bars_per_line": 1},
        {"start_bar": 511, "bars_per_line": 2},
        {"start_bar": None, "bars_per_line": 1},
        {"start_bar": 3, "bars_per_line": None},
    ],
)
def test_layout_edges_that_are_taken(daw, body):
    assert api(daw, "POST", "/api/song/layout", body) == (200, body)


@pytest.mark.parametrize(
    "body",
    [
        {"start_bar": 512, "bars_per_line": 1},
        {"start_bar": -1, "bars_per_line": 1},
        {"start_bar": True, "bars_per_line": 1},
        {"start_bar": 1.5, "bars_per_line": 1},
        {"start_bar": "3", "bars_per_line": 1},
        {"start_bar": 3, "bars_per_line": 0},
        {"start_bar": 3, "bars_per_line": 3},
        {"start_bar": 3, "bars_per_line": True},
        {"start_bar": 3},
        {"bars_per_line": 1},
        {"start_bar": 3, "bars_per_line": 1, "lines": 4},
    ],
)
def test_layout_values_out_of_range_are_400_and_change_nothing(daw, body):
    api(daw, "POST", "/api/song/layout", {"start_bar": 8, "bars_per_line": 1})
    assert api(daw, "POST", "/api/song/layout", body)[0] == 400
    assert api(daw, "GET", "/api/song/layout")[1] == {"start_bar": 8, "bars_per_line": 1}


# the chained mix job


def finished(daw, kind="mix"):
    return until(lambda: (jobs_of(daw, kind) or {}).get("state") in ("done", "failed", "cancelled"))


def test_mix_separates_stale_stems_runs_auto_and_keeps_variant_0(daw, root):
    choose_take(daw)
    status, data = api(daw, "POST", "/api/jobs/start", {"kind": "mix", "seed": 5})
    assert status == 202 and data["kind"] == "mix"
    assert finished(daw)
    job = jobs_of(daw, "mix")
    assert job["state"] == "done" and job["step"] == "keep", job
    assert daw.calls == ["separate", "auto"]
    song = model.load(NAME)
    assert song.mix == {"settings": f"private/songs/{NAME}/mix.json", "variant": 0}
    mix = json.loads((root / "private" / "songs" / NAME / "mix.json").read_text())
    assert mix["formant"] == 1.1
    assert api(daw, "GET", "/api/song")[1]["stations"]["mix"]["ready"] is True


def test_mix_skips_the_separation_when_the_stems_are_current(daw, root):
    choose_take(daw)
    mark_separated(daw)
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw) and jobs_of(daw, "mix")["state"] == "done"
    assert daw.calls == ["auto"]


@pytest.mark.parametrize("step", ["separate", "auto"])
def test_cancelling_mix_stops_its_running_step_and_keeps_nothing(daw, root, step):
    choose_take(daw)
    daw.plan[step] = "sleep"
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert until(lambda: (jobs_of(daw, "mix") or {}).get("step") == step)
    job = jobs_of(daw, "mix")
    assert job["state"] == "running" and job["pid"] is not None
    pid = job["pid"]
    status, data = api(daw, "POST", "/api/jobs/cancel", {"kind": "mix"})
    assert status == 200 and data["kind"] == "mix"
    assert finished(daw) and jobs_of(daw, "mix")["state"] == "cancelled"
    assert not Path(f"/proc/{pid}").exists() or "zombie" in Path(f"/proc/{pid}/status").read_text()
    assert daw.calls == ["separate", "auto"][: ["separate", "auto"].index(step) + 1]
    assert model.load(NAME).mix["variant"] is None


@pytest.mark.parametrize("step", ["separate", "auto"])
def test_a_failing_step_fails_mix_with_its_reason(daw, root, step):
    choose_take(daw)
    daw.plan[step] = "fail"
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw)
    job = jobs_of(daw, "mix")
    assert job["state"] == "failed" and job["step"] == step
    assert f"the {step} broke" in job["reason"]
    assert model.load(NAME).mix["variant"] is None


def test_variants_of_another_mix_fail_the_keep_step(daw, root):
    choose_take(daw)
    daw.variants_key[0] = "0" * 64
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw)
    job = jobs_of(daw, "mix")
    assert job["state"] == "failed" and job["step"] == "keep" and "another mix" in job["reason"]
    assert model.load(NAME).mix["variant"] is None


def test_mix_needs_a_voice_and_runs_once(daw, root):
    status, data = api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert status == 409 and data["code"] == "no_voice"
    choose_take(daw)
    daw.plan["separate"] = "sleep"
    assert api(daw, "POST", "/api/jobs/start", {"kind": "mix"})[0] == 202
    for kind in ("mix", "separate", "auto"):
        status, data = api(daw, "POST", "/api/jobs/start", {"kind": kind})
        assert status == 409 and data["code"] == "busy", kind
    api(daw, "POST", "/api/jobs/cancel", {"kind": "mix"})
    assert finished(daw)
    assert api(daw, "POST", "/api/jobs/cancel", {"kind": "mix"})[0] == 200


def test_mix_is_refused_while_a_separation_runs(daw, root):
    choose_take(daw)
    daw.plan["separate"] = "sleep"
    assert api(daw, "POST", "/api/jobs/start", {"kind": "separate"})[0] == 202
    status, data = api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert status == 409 and data["code"] == "busy"
    api(daw, "POST", "/api/jobs/cancel", {"kind": "separate"})


def on_keep(app, action):
    """Run action in a thread of its own when the chain first reads the song to keep variant 0."""
    original = app.song
    fired = []

    def song():
        chain = app._mix
        in_chain = "_run_mix" in threading.current_thread().name
        if not fired and in_chain and chain is not None and chain.step == "keep":
            fired.append(True)
            side = threading.Thread(target=action, daemon=True)
            side.start()
            side.join(0.5)
        return original()

    app.song = song


def test_a_cancel_landing_in_the_keep_step_either_keeps_nothing_or_answers_done(daw, root):
    choose_take(daw)
    mark_separated(daw)
    app = daw.app
    answers = []
    on_keep(app, lambda: answers.append(json.loads(app._cancel_mix()[1])["state"]))
    assert api(daw, "POST", "/api/jobs/start", {"kind": "mix"})[0] == 202
    assert finished(daw) and until(lambda: answers)
    final = jobs_of(daw, "mix")["state"]
    if answers[0] == "running":
        assert final == "cancelled"
        assert model.load(NAME).mix["variant"] is None
        assert not (root / "private" / "songs" / NAME / "mix.json").exists()
    else:
        assert answers[0] == final == "done"


def test_close_during_the_keep_step_returns_only_after_the_chain_wrote_its_last(daw, root):
    choose_take(daw)
    mark_separated(daw)
    app = daw.app
    seen = {}
    settings = root / "work" / "studio" / NAME / "settings.json"

    def close():
        app.close()
        seen["rev"] = model.load(NAME).rev
        seen["settings"] = settings.exists()

    on_keep(app, close)
    assert api(daw, "POST", "/api/jobs/start", {"kind": "mix"})[0] == 202
    assert until(lambda: "rev" in seen)
    assert until(lambda: app._mix.state != "running")
    time.sleep(0.2)
    assert model.load(NAME).rev == seen["rev"]
    assert settings.exists() == seen["settings"]


def test_close_while_a_mix_step_runs_ends_the_chain_cancelled_before_it_returns(daw, root):
    choose_take(daw)
    daw.plan["separate"] = "sleep"
    assert api(daw, "POST", "/api/jobs/start", {"kind": "mix"})[0] == 202
    assert until(lambda: (jobs_of(daw, "mix") or {}).get("pid") is not None)
    app = daw.app
    app.close()
    assert app._mix.state == "cancelled"
    assert model.load(NAME).mix["variant"] is None
    assert daw.calls == ["separate"]


def test_a_separation_asked_together_with_mix_never_runs_beside_it(daw, root):
    choose_take(daw)
    daw.plan["separate"] = "sleep"
    app = daw.app
    original = app._start
    inside, go = threading.Event(), threading.Event()

    def start(kind, seed, *args, **kwargs):
        if kind == "separate":
            inside.set()
            go.wait(5)
        return original(kind, seed, *args, **kwargs)

    app._start = start
    answers = {}

    def ask(kind):
        answers[kind] = api(daw, "POST", "/api/jobs/start", {"kind": kind})

    separate = threading.Thread(target=ask, args=("separate",))
    separate.start()
    assert inside.wait(5)
    mix = threading.Thread(target=ask, args=("mix",))
    mix.start()
    mix.join(0.5)
    go.set()
    separate.join(10)
    mix.join(10)
    try:
        assert answers["separate"][0] == 202
        assert answers["mix"][0] == 409 and answers["mix"][1]["code"] == "busy"
    finally:
        api(daw, "POST", "/api/jobs/cancel", {"kind": "separate"})
        if answers["mix"][0] == 202:
            api(daw, "POST", "/api/jobs/cancel", {"kind": "mix"})
        assert finished(daw, "separate")


def test_layout_saves_from_many_threads_all_succeed_and_one_of_them_is_kept(daw, root):
    app = daw.app
    bodies = [{"start_bar": n, "bars_per_line": 1 + n % 2} for n in range(8)]
    errors = []
    start = threading.Barrier(len(bodies))

    def save(body):
        start.wait()
        for _ in range(100):
            try:
                assert app.post_layout(body)[0] == 200
            except Exception as error:  # noqa: BLE001
                errors.append(error)

    threads = [threading.Thread(target=save, args=(body,)) for body in bodies]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert api(daw, "GET", "/api/song/layout")[1] in bodies
    folder = root / "private" / "songs" / NAME
    assert not [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


# facts


def test_facts_beat_audio(daw, root):
    assert facts(daw)["beat_audio"] is True
    set_beat(daw, {"own": 4})
    assert facts(daw)["beat_audio"] is False
    render_beat(root, 4, tone(0.5), 8000)
    assert facts(daw)["beat_audio"] is True


def test_facts_beat_analysed(daw, root):
    from redline.flip import pipeline

    assert facts(daw)["beat_analysed"] is False
    sha8 = pipeline.source_sha8(root / "input" / "beat.wav")
    folder = root / "work" / "flips" / sha8
    folder.mkdir(parents=True)
    (folder / pipeline.ANALYSIS).write_text(json.dumps({"format": pipeline.FORMAT}))
    assert facts(daw)["beat_analysed"] is True
    set_beat(daw, {"own": 4})
    assert facts(daw)["beat_analysed"] is True


def test_facts_studio_ready(daw, root):
    assert facts(daw)["studio_ready"] is False
    choose_take(daw)
    assert facts(daw)["studio_ready"] is False
    mark_separated(daw)
    assert facts(daw)["studio_ready"] is True


def test_facts_mix_stale(daw, root):
    choose_take(daw)
    assert facts(daw)["mix_stale"] is True
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw) and jobs_of(daw, "mix")["state"] == "done"
    assert facts(daw)["mix_stale"] is False
    sf.write(root / "input" / "takes" / NAME / "take-02.wav", tone(0.5), 8000)
    rev = model.load(NAME).rev
    api(daw, "POST", "/api/song", {"rev": rev, "set": {"voice_chosen": "take-02.wav"}})
    assert facts(daw)["mix_stale"] is True


def test_facts_mix_stale_after_the_beat_file_changes(daw, root):
    choose_take(daw)
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw) and facts(daw)["mix_stale"] is False
    sf.write(root / "input" / "beat.wav", tone(3.0), 8000)
    assert facts(daw)["mix_stale"] is True


def open_song(daw, root, name, suno=None, refs=("input/ref.wav",)):
    model.save(model.new_song(name, {"file": "input/beat.wav"}, suno, refs), 0)
    assert api(daw, "POST", "/api/songs/open", {"name": name})[0] == 200


def test_facts_refs_and_voice_kind(daw, root):
    first = facts(daw)
    assert first["refs"] == 1 and first["voice_kind"] == "own"
    open_song(daw, root, "bare", refs=())
    assert facts(daw)["refs"] == 0
    open_song(daw, root, "sun", suno="input/outside.wav", refs=("input/ref.wav", "input/beat.wav"))
    second = facts(daw)
    assert second["refs"] == 2 and second["voice_kind"] == "suno"


def test_facts_exporting(daw, root):
    choose_take(daw)
    assert facts(daw)["exporting"] is False
    daw.plan["result"] = "sleep"
    assert api(daw, "POST", "/api/jobs/start", {"kind": "result"})[0] == 202
    assert facts(daw)["exporting"] is True
    api(daw, "POST", "/api/jobs/cancel", {"kind": "result"})
    assert until(lambda: facts(daw)["exporting"] is False)


def rendered_seed(root, score="score-1.json"):
    out = root / "work" / "beats" / str(SEED)
    out.mkdir(parents=True, exist_ok=True)
    sf.write(out / "beat.wav", tone(0.5), 8000)
    (out / "render.json").write_text(json.dumps({"score": score}), encoding="utf-8")


def test_facts_beat_dirty(daw, root):
    assert facts(daw)["beat_dirty"] is False
    folder = own_seed(daw, root)
    assert facts(daw)["beat_dirty"] is True
    rendered_seed(root)
    assert facts(daw)["beat_dirty"] is False
    api(
        daw,
        "POST",
        "/api/beat/draft",
        {"score": score_body("x..............."), "base": "score-1.json"},
    )
    assert facts(daw)["beat_dirty"] is True
    (folder / "draft.json").unlink()
    assert facts(daw)["beat_dirty"] is False
    (folder / "score-2.json").write_text(json.dumps(score_body()), encoding="utf-8")
    assert facts(daw)["beat_dirty"] is True
    rendered_seed(root, "score-2.json")
    assert facts(daw)["beat_dirty"] is False


def flip_json(label="all"):
    return {
        "format": 1,
        "source": "input/beat.wav",
        "grid": {"bpm": 120, "downbeat_s": 0.0, "swing": 50},
        "bass808": {"mode": "original", "params": {}},
        "hats": {"mode": "original", "remove": [], "patterns": []},
        "gains": {"drums": 0, "other": 0, "bass": 0},
        "arrangement": [{"label": label, "from_bar": 0, "to_bar": 2}],
        "effects": [],
    }


def test_facts_beat_dirty_for_a_flip_is_a_draft_that_differs_from_the_version_in_use(daw, root):
    folder = root / "work" / "flips" / "0123abcd"
    folder.mkdir(parents=True)
    sf.write(folder / "flip-2.wav", tone(0.25), 8000)
    set_beat(daw, {"file": "work/flips/0123abcd/flip-2.wav"})
    write = lambda name, label: (folder / name).write_text(  # noqa: E731
        json.dumps(flip_json(label)), encoding="utf-8"
    )
    assert facts(daw)["beat_dirty"] is False
    write("flip-2.json", "all")
    assert facts(daw)["beat_dirty"] is False
    write("draft.json", "all")
    assert facts(daw)["beat_dirty"] is False
    write("draft.json", "verse")
    assert facts(daw)["beat_dirty"] is True
    write("flip-2.json", "verse")
    assert facts(daw)["beat_dirty"] is False
    (folder / "draft.json").write_text("{", encoding="utf-8")
    assert facts(daw)["beat_dirty"] is False


def test_facts_beat_dirty_for_an_analysed_source_is_a_draft_that_differs_from_the_default(
    daw, root
):
    from redline.flip import model as flip_model
    from redline.flip import pipeline

    path = root / "input" / "beat.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, tone(0.25), 8000)
    set_beat(daw, {"file": "input/beat.wav"})
    folder = root / "work" / "flips" / pipeline.source_sha8(path)
    folder.mkdir(parents=True)
    assert facts(daw)["beat_dirty"] is False
    analysis = {
        "format": pipeline.FORMAT,
        "bpm": 120.0,
        "downbeat_s": 0.0,
        "detune_cents": 0.0,
        "bar_starts_s": [0.0, 2.0],
        "source": "input/beat.wav",
    }
    (folder / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    default = pipeline._default_draft("input/beat.wav", analysis).to_json()
    draft = folder / "draft.json"
    draft.write_text(json.dumps(default), encoding="utf-8")
    assert facts(daw)["beat_dirty"] is False
    edited = json.loads(json.dumps(default))
    edited["arrangement"][0]["drums"] = {"kick": "x" * 16 * 2}
    flip_model.parse(edited)
    draft.write_text(json.dumps(edited), encoding="utf-8")
    assert facts(daw)["beat_dirty"] is True
    draft.write_text(json.dumps(default), encoding="utf-8")
    assert facts(daw)["beat_dirty"] is False
    draft.write_text("{", encoding="utf-8")
    assert facts(daw)["beat_dirty"] is False


# knob saves reach the export


def result_settings(monkeypatch, root):
    from redline.cli import song as cli_song

    given = []

    def write_result(measured, engine, out, label, settings):
        given.append(settings)
        return root / "output" / NAME / "run"

    monkeypatch.setattr(cli_song, "_measured", lambda inputs, work: object())
    monkeypatch.setattr("redline.engine.track.track_engine", lambda measured: object())
    monkeypatch.setattr("redline.master.run.write_result", write_result)
    monkeypatch.setattr("redline.output.check_folders", lambda work, out: None)
    cli_song._job_result(model.load(NAME), root / "work", 0)
    return given[0]


def knob_save(daw, values):
    settings = from_dict(values)
    daw.app.studio().exact.add(settings)
    status, _ = api(daw, "POST", "/api/mix/save", {"settings": to_dict(settings)})
    return status, settings


def test_a_knob_save_is_what_the_result_job_renders_with(daw, root, monkeypatch):
    choose_take(daw)
    mark_separated(daw)
    status, settings = knob_save(daw, {"formant": 1.3})
    assert status == 200
    assert model.load(NAME).mix["settings"] == f"private/songs/{NAME}/mix.json"
    assert result_settings(monkeypatch, root) == settings
    assert facts(daw)["mix_stale"] is False


def test_a_knob_save_after_a_kept_variant_reaches_the_export_while_the_song_is_busy(
    daw, root, monkeypatch
):
    choose_take(daw)
    api(daw, "POST", "/api/jobs/start", {"kind": "mix"})
    assert finished(daw) and jobs_of(daw, "mix")["state"] == "done"

    def busy(*args, **kwargs):
        raise model.StaleRev("the song moved on")

    with monkeypatch.context() as patched:
        patched.setattr(model, "save", busy)
        status, settings = knob_save(daw, {"formant": 0.9})
    assert status == 200
    assert result_settings(monkeypatch, root) == settings


def test_a_knob_save_that_cannot_point_the_song_at_it_is_refused(daw, root, monkeypatch):
    choose_take(daw)
    mark_separated(daw)
    daw.app.studio()

    def busy(*args, **kwargs):
        raise model.StaleRev("the song moved on")

    with monkeypatch.context() as patched:
        patched.setattr(model, "save", busy)
        status, _ = knob_save(daw, {"formant": 0.9})
    assert status == 409
    assert model.load(NAME).mix["settings"] is None


# guards


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/song/beat-audio"),
        ("GET", "/api/peaks?of=beat"),
        ("GET", "/api/beat/draft"),
        ("GET", "/api/song/layout"),
        ("POST", "/api/beat/draft"),
        ("POST", "/api/song/layout"),
    ],
)
def test_the_new_routes_need_the_token_and_this_host(daw, method, path):
    body = {} if method == "POST" else None
    status, headers, data = call(daw, method, path, body)
    known = headers["Content-Type"] != "application/json" or json.loads(data).get("error") != (
        "no such call"
    )
    assert status != 403 and known
    assert call(daw, method, path, body, token=None)[0] == 403
    assert call(daw, method, path, body, host="evil.com")[0] == 403
