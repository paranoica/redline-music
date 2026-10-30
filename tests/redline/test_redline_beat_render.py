import json
import os
import re

import numpy as np
import pytest
import soundfile as sf

redline_dsp = pytest.importorskip("redline_dsp")

from redline.beat import handoff, marks, render  # noqa: E402
from redline.beat.library import git_sha1  # noqa: E402
from redline.beat.recipe import make_recipe, recipe_json  # noqa: E402
from redline.cli import build_parser, main  # noqa: E402
from redline.cli.beat import _beat  # noqa: E402
from redline.measure import beat as beat_measure  # noqa: E402

RATE = 48000
LIB = "fake-keys"
TAIL_CAP = 4 * RATE

INTRO_KICK = "x.......x......."
HOOK_KICK = "x...x...x...x..."
HAT = "x.x.x.x.x.x.x.x."
FILL_HAT = "x3x4x6x.x2x.xxxx"


def body(**top):
    """Four bars at 180 BPM: an intro with the hat muted, a hook with the 808 and a hat fill."""
    score = {
        "version": 1,
        "bpm": 180,
        "swing": 50,
        "seed": 3,
        "key": "A minor",
        "sounds": {
            "kick": {"instrument": "kick"},
            "hat": {"instrument": "hat_closed"},
            "bass": {"instrument": "bass808"},
        },
        "patterns": {
            "drums": {
                "bars": 2,
                "rows": {"kick": f"{INTRO_KICK}|{HOOK_KICK}", "hat": HAT * 2},
            },
            "low": {
                "bars": 1,
                "rows": {
                    "bass": [
                        {"step": 0, "len": 8, "note": "A1", "vel": 0.9},
                        {"step": 8, "len": 4, "note": "C2", "vel": 0.8},
                    ]
                },
            },
            "fill": {"bars": 1, "rows": {"hat": FILL_HAT}},
        },
        "sections": [
            {"name": "intro", "bars": 2, "play": ["drums"], "mute": ["hat"]},
            {"name": "hook", "bars": 2, "play": ["drums", "low"], "fill": "fill"},
        ],
    }
    score.update(top)
    return json.loads(json.dumps(score))


def with_keys(score):
    score["sounds"]["keys"] = {"instrument": "sampler", "library": LIB}
    score["patterns"]["low"]["rows"]["keys"] = [{"step": 0, "len": 8, "note": "A3", "vel": 0.8}]
    return score


def place(root, seed, score, number=1, genre="trap"):
    folder = root / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    recipe = make_recipe(seed, genre=genre, bpm=180, key="A minor")
    (folder / "recipe.json").write_text(recipe_json(recipe), encoding="utf-8")
    path = folder / f"score-{number}.json"
    path.write_text(json.dumps(score, indent=1), encoding="utf-8")
    return path


def song_frames(score):
    return redline_dsp.BeatPlayer(json.dumps(score), RATE).length


def fake_library(tmp_path, work, commercial=True, fetched=True):
    """A one-zone sampler library: a 440 Hz int16 tone at 44.1 kHz, rooted at A4."""
    sounds = work / "sounds" / LIB
    t = np.arange(int(0.6 * 44100)) / 44100
    tone = (0.5 * np.sin(2 * np.pi * 440 * t) * np.exp(-t * 3) * 32767).astype(np.int16)
    sounds.mkdir(parents=True, exist_ok=True)
    wav = sounds / "a4.wav"
    sf.write(wav, tone, 44100, subtype="PCM_16")
    data = wav.read_bytes()
    if not fetched:
        wav.unlink()
    entry = {
        "name": LIB,
        "source": "https://github.com/someone/samples",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "licence": "CC0-1.0" if commercial else "CC-BY-NC-4.0",
        "licence_quote": "a test licence",
        "kind": "fetch",
        "commercial": commercial,
        "files": [
            {
                "path": "a4.wav",
                "git_sha1": git_sha1(data),
                "size": len(data),
                "note": 69,
                "vel": [0, 127],
            }
        ],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"sources": [entry]}), encoding="utf-8")
    return manifest


def read(path):
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    assert rate == RATE
    return data


# render


def test_render_writes_the_mix_and_a_stem_per_sound_that_sum_to_it(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    score = body()
    place(root, 4, score)
    done = render.render_seed(4, root=root, work=work)
    folder = work / "beats" / "4"
    assert done.folder == folder
    mix = read(folder / "beat.wav")
    length = song_frames(score)
    assert length <= len(mix) <= length + TAIL_CAP
    assert np.abs(mix[-RATE // 10 :]).max() < 1e-2
    stems = {p.stem: read(p) for p in (folder / "stems").glob("*.wav")}
    assert sorted(stems) == ["bass", "hat", "kick"]
    for stem in stems.values():
        assert stem.shape == mix.shape
        assert np.abs(stem).max() > 1e-3
    np.testing.assert_allclose(sum(stems.values()), mix, atol=1e-5)
    # the streamed render is the binding's whole render, frame for frame
    whole = redline_dsp.beat_render(json.dumps(score), RATE)
    np.testing.assert_array_equal(mix, whole)


def test_render_drops_each_racks_latency(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    plain = body()
    racked = body(mix={"sounds": {"hat": {"rack": [{"effect": "limiter"}]}}})
    mastered = body(mix={"master": [{"effect": "limiter"}]})
    assert redline_dsp.BeatPlayer(json.dumps(racked), RATE).latency > 0
    master = redline_dsp.BeatPlayer(json.dumps(mastered), RATE)
    assert master.latency > 0 and master.stem_latency == 0
    for seed, score in ((1, plain), (2, racked), (3, mastered)):
        place(root, seed, score)
        render.render_seed(seed, root=root, work=work, block=1000)
    whole = redline_dsp.beat_render(json.dumps(racked), RATE)
    np.testing.assert_array_equal(read(work / "beats/2/beat.wav"), whole)
    whole = redline_dsp.beat_render(json.dumps(mastered), RATE)
    np.testing.assert_array_equal(read(work / "beats/3/beat.wav"), whole)
    # stems come before the master rack: the same as without it, on the same frames
    for name in ("kick", "hat", "bass"):
        a = read(work / f"beats/1/stems/{name}.wav")
        b = read(work / f"beats/3/stems/{name}.wav")
        n = min(len(a), len(b))
        np.testing.assert_array_equal(a[:n], b[:n])


def test_a_long_release_rings_past_the_song_as_the_core_render_keeps_it(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    score = {
        "version": 1,
        "bpm": 120,
        "swing": 50,
        "seed": 3,
        "key": "A minor",
        "sounds": {"pad": {"instrument": "saw", "params": {"release": 8.0}}},
        "patterns": {
            "p": {"bars": 1, "rows": {"pad": [{"step": 0, "len": 16, "note": "A3", "vel": 0.9}]}}
        },
        "sections": [{"name": "a", "bars": 1, "play": ["p"]}],
    }
    place(root, 6, score)
    render.render_seed(6, root=root, work=work, block=1000)
    mix = read(work / "beats/6/beat.wav")
    # the note is released at the song's end and rings for up to 8 s more
    assert len(mix) > song_frames(score) + TAIL_CAP
    np.testing.assert_array_equal(mix, redline_dsp.beat_render(json.dumps(score), RATE))
    stem = read(work / "beats/6/stems/pad.wav")
    assert len(stem) == len(mix)


def test_render_files_are_the_owners_only(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    place(root, 4, body())
    render.render_seed(4, root=root, work=work)
    folder = work / "beats" / "4"
    for path in (folder / "beat.wav", folder / "render.json", folder / "stems/kick.wav"):
        assert path.stat().st_mode & 0o777 == 0o600, path
    assert (folder / "stems").stat().st_mode & 0o777 == 0o700
    # a second render replaces them with the same modes
    render.render_seed(4, root=root, work=work)
    assert (folder / "render.json").stat().st_mode & 0o777 == 0o600
    assert (folder / "beat.wav").stat().st_mode & 0o777 == 0o600


def test_an_existing_open_render_folder_is_closed_to_the_owner(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    place(root, 4, body())
    folder = work / "beats" / "4"
    folder.mkdir(parents=True)
    folder.chmod(0o755)
    old = os.umask(0o022)
    try:
        render.render_seed(4, root=root, work=work)
    finally:
        os.umask(old)
    assert folder.stat().st_mode & 0o777 == 0o700


def test_render_report_names_the_score_and_no_library(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    path = place(root, 4, body())
    done = render.render_seed(4, root=root, work=work)
    report = json.loads((work / "beats/4/render.json").read_text(encoding="utf-8"))
    assert report["score"] == "score-1.json"
    assert report["score_sha256"] == render.sha256(path)
    assert report["rate"] == RATE
    assert report["frames"] == len(read(work / "beats/4/beat.wav")) == done.frames
    assert report["sounds"] == ["bass", "hat", "kick"]
    assert report["libraries"] == []
    assert report["non_commercial"] == []


def test_render_takes_the_newest_score_or_the_one_given(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    place(root, 4, body(), number=2)
    older = body()
    del older["sounds"]["hat"], older["patterns"]["drums"]["rows"]["hat"]
    del older["patterns"]["fill"], older["sections"][1]["fill"], older["sections"][0]["mute"]
    place(root, 4, older, number=1)
    place(root, 4, older, number=10)
    render.render_seed(4, root=root, work=work)
    assert json.loads((work / "beats/4/render.json").read_text())["score"] == "score-10.json"
    assert sorted(p.stem for p in (work / "beats/4/stems").glob("*.wav")) == ["bass", "kick"]
    # a stem of an earlier render is not left behind
    render.render_seed(4, root=root, work=work, score=root / "4" / "score-2.json")
    assert sorted(p.stem for p in (work / "beats/4/stems").glob("*.wav")) == [
        "bass",
        "hat",
        "kick",
    ]
    render.render_seed(4, root=root, work=work)
    assert sorted(p.stem for p in (work / "beats/4/stems").glob("*.wav")) == ["bass", "kick"]


def test_a_seed_without_a_score_is_refused(tmp_path):
    (tmp_path / "beats" / "4").mkdir(parents=True)
    with pytest.raises(render.RenderError, match="no saved score"):
        render.render_seed(4, root=tmp_path / "beats", work=tmp_path / "work")


def test_a_sampler_of_a_library_not_fetched_names_the_fetch_command(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    manifest = fake_library(tmp_path, work, fetched=False)
    place(root, 4, with_keys(body()))
    with pytest.raises(render.RenderError, match=f"redline beat sounds fetch {LIB}"):
        render.render_seed(4, root=root, work=work, manifest=manifest)
    assert not (work / "beats/4/beat.wav").exists()


def test_a_sampler_of_a_fetched_library_plays(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    manifest = fake_library(tmp_path, work)
    place(root, 4, with_keys(body()))
    render.render_seed(4, root=root, work=work, manifest=manifest)
    keys = read(work / "beats/4/stems/keys.wav")
    # it plays in the hook only, bars 3 and 4
    bar = song_frames(body()) // 4
    assert np.abs(keys[: 2 * bar - 100]).max() == 0
    assert np.abs(keys[2 * bar : 3 * bar]).max() > 0.05
    report = json.loads((work / "beats/4/render.json").read_text(encoding="utf-8"))
    assert report["libraries"] == [
        {"name": LIB, "licence": "CC0-1.0", "commercial": True, "sounds": ["keys"], "zones": 1}
    ]
    assert report["non_commercial"] == []


def test_a_non_commercial_library_is_named_in_the_report(tmp_path):
    root, work = tmp_path / "beats", tmp_path / "work"
    manifest = fake_library(tmp_path, work, commercial=False)
    place(root, 4, with_keys(body()))
    done = render.render_seed(4, root=root, work=work, manifest=manifest)
    report = json.loads((work / "beats/4/render.json").read_text(encoding="utf-8"))
    assert report["non_commercial"] == ["keys"]
    assert done.non_commercial == ("keys",)
    assert report["libraries"][0]["licence"] == "CC-BY-NC-4.0"


def test_the_cli_renders_and_reports_a_non_commercial_sound(tmp_path, capsys):
    root, work = tmp_path / "beats", tmp_path / "work"
    place(root, 4, body())
    argv = ["beat", "render", "--seed", "4", "--root", str(root), "--work", str(work)]
    assert main(argv) == 0
    assert str(work / "beats" / "4" / "beat.wav") in capsys.readouterr().out
    assert main([*argv[:3], "5", *argv[4:]]) == 2
    assert "no saved score" in capsys.readouterr().err


# measure


def noise(seconds, colour, seed, amp=0.2):
    """Stereo noise with a power slope: 0 white, 1 pink (1/f)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * 44100)
    spec = np.fft.rfft(rng.standard_normal((n, 2)), axis=0)
    f = np.fft.rfftfreq(n, 1 / 44100)
    f[0] = f[1]
    spec *= (f ** (-colour / 2))[:, None]
    out = np.fft.irfft(spec, n=n, axis=0)
    return (amp * out / np.abs(out).max()).astype(np.float32)


class BeatOnly:
    """A fake separator: the whole signal is beat, the vocal silent."""

    id = "fake:beat-only"

    def __init__(self):
        self.calls = 0

    def __call__(self, samples, rate):
        self.calls += 1
        return np.zeros_like(samples), samples.copy()


@pytest.fixture
def pink_refs(tmp_path):
    folder = tmp_path / "refs"
    folder.mkdir()
    paths = []
    for i in range(3):
        path = folder / f"ref{i}.wav"
        sf.write(path, noise(3, 1.0, 10 + i, amp=0.2 + 0.05 * i), 44100, subtype="FLOAT")
        paths.append(path)
    return paths


def put_render(work, seed, samples):
    folder = work / "beats" / str(seed)
    folder.mkdir(parents=True, exist_ok=True)
    sf.write(folder / "beat.wav", samples, 44100, subtype="FLOAT")


def tilt_row(measured):
    return next(r for r in measured["metrics"] if r["key"] == "beat.tilt_db_per_oct")


def test_beat_values_are_beat_only():
    values = beat_measure.beat_values(noise(3, 1.0, 1), 44100)["values"]
    assert {
        "beat.tilt_db_per_oct",
        "beat.crest_db",
        "beat.low_share_db",
        "beat.low_crest_db",
        "beat.low_kurtosis",
    } <= set(values)
    assert all(key.startswith("beat.") for key in values)
    white = beat_measure.beat_values(noise(3, 0.0, 1), 44100)["values"]
    assert white["beat.tilt_db_per_oct"] > values["beat.tilt_db_per_oct"] + 2


def test_a_white_render_against_pink_refs_is_outside_then_a_pink_one_closer(tmp_path, pink_refs):
    work = tmp_path / "work"
    separator = BeatOnly()
    put_render(work, 7, noise(3, 0.0, 99))
    first = render.measure_seed(7, pink_refs, work, separator)
    assert separator.calls == 3
    row = tilt_row(json.loads(first.json.read_text(encoding="utf-8")))
    assert row["status"] == "refs_lower"
    assert row["render"] > row["refs_max"]
    assert row["trend"] is None
    text = first.markdown.read_text(encoding="utf-8")
    assert "отделён" in text and "чист" in text
    assert "звучит лучше" not in text
    put_render(work, 7, noise(3, 1.0, 98, amp=0.22))
    second = render.measure_seed(7, pink_refs, work, separator)
    # the references come from the stem cache the second time
    assert separator.calls == 3
    row = tilt_row(json.loads(second.json.read_text(encoding="utf-8")))
    assert row["status"] == "inside"
    assert row["trend"] == "closer"
    assert "ближе" in second.markdown.read_text(encoding="utf-8")
    put_render(work, 7, noise(3, 0.0, 97))
    third = render.measure_seed(7, pink_refs, work, separator)
    row = tilt_row(json.loads(third.json.read_text(encoding="utf-8")))
    assert row["trend"] == "further"


def test_the_report_shows_the_tolerance_that_decides_inside(tmp_path):
    values = {key: None for key in beat_measure.BEAT_METRICS}
    measured = beat_measure.BeatMeasured(
        values={**values, "beat.crest_db": 8.0},
        info={},
        refs=(
            ("a.wav", {**values, "beat.crest_db": 10.0}),
            ("b.wav", {**values, "beat.crest_db": 13.0}),
        ),
        upper=20000.0,
        separator="fake",
    )
    markdown, data = beat_measure.write_beat_report(tmp_path, measured, "score-1.json")
    # half the refs' spread of 3 dB plus the crest's 1 dB threshold
    row = next(r for r in json.loads(data.read_text())["metrics"] if r["key"] == "beat.crest_db")
    assert row["tolerance"] == pytest.approx(2.5)
    assert row["status"] == "inside"
    text = markdown.read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("| крест-фактор бита"))
    assert "±2.50" in line and "внутри" in line
    assert "Допуск" in text and "половина разброса референсов" in text
    # no tolerance where the refs give no spread to take half of
    tilt = next(r for r in json.loads(data.read_text())["metrics"] if r["key"] == "beat.lufs")
    assert tilt["tolerance"] is None


def test_measure_without_a_render_says_to_render_first(tmp_path, pink_refs):
    with pytest.raises(render.RenderError, match="redline beat render --seed 7"):
        render.measure_seed(7, pink_refs, tmp_path / "work", BeatOnly())


def test_the_cli_measure_writes_both_reports(tmp_path, pink_refs, capsys):
    work = tmp_path / "work"
    put_render(work, 7, noise(3, 0.0, 99))
    args = build_parser().parse_args(
        ["beat", "measure", "--seed", "7", "--ref", str(pink_refs[0].parent), "--work", str(work)]
    )
    assert _beat(args, BeatOnly()) == 0
    assert str(work / "beats" / "7" / "measure.md") in capsys.readouterr().out
    assert (work / "beats/7/measure.json").exists()
    for name in ("measure.md", "measure.json"):
        assert (work / "beats/7" / name).stat().st_mode & 0o777 == 0o600, name


# marks


def test_an_excerpt_is_the_bar_as_written():
    score = body()
    one = marks.excerpt(score, 1)
    assert INTRO_KICK in one and HOOK_KICK not in one
    assert "hat" not in one.split("\n", 1)[1] and "bass" not in one
    two = marks.excerpt(score, 2)
    assert HOOK_KICK in two and INTRO_KICK not in two
    three = marks.excerpt(score, 3)
    assert HAT in three and FILL_HAT not in three
    assert "A1" in three and "C2" in three
    four = marks.excerpt(score, 4)
    assert FILL_HAT in four and HAT not in four and HOOK_KICK in four
    assert "fill" in four


def test_an_excerpt_keeps_the_step_lanes_and_note_steps_of_its_bar():
    score = body()
    lane = [0.1] * 16 + [0.9] * 16
    score["patterns"]["drums"]["rows"]["kick"] = {
        "steps": f"{INTRO_KICK}|{HOOK_KICK}",
        "velocity": lane,
    }
    score["patterns"]["low"]["bars"] = 2
    score["patterns"]["low"]["rows"]["bass"].append(
        {"step": 20, "len": 4, "note": "E2", "vel": 0.7}
    )
    assert redline_dsp.beat_check(json.dumps(score)) == []
    one, two = marks.excerpt(score, 1), marks.excerpt(score, 2)
    assert "0.1" in one and "0.9" not in one
    assert "0.9" in two and "0.1" not in two
    three, four = marks.excerpt(score, 3), marks.excerpt(score, 4)
    assert "E2" not in three and "E2" in four and "A1" not in four
    assert re.search(r"E2\b.*step 4\b", four)


def test_bars_outside_the_song_are_refused(tmp_path):
    root = tmp_path / "beats"
    place(root, 4, body())
    for bar in (0, 5, -1):
        with pytest.raises(marks.MarkError, match="bar"):
            marks.add_mark(root, 4, bar, "good")
    assert not (root / "marks.jsonl").exists()
    marks.add_mark(root, 4, 4, "good")
    marks.add_mark(root, 4, 1, "bad")
    assert len(marks.seed_marks(root, 4)) == 2


def test_a_mark_needs_a_saved_score_and_a_verdict(tmp_path):
    root = tmp_path / "beats"
    with pytest.raises(marks.MarkError, match="no saved score"):
        marks.add_mark(root, 4, 1, "good")
    place(root, 4, body())
    with pytest.raises(marks.MarkError, match="good or bad"):
        marks.add_mark(root, 4, 1, "meh")


def test_a_mark_holds_the_score_hash_and_round_trips_into_the_next_request(tmp_path):
    root = tmp_path / "beats"
    path = place(root, 4, body(), number=2)
    saved = marks.add_mark(root, 4, 4, "bad", note="too busy at the end")
    line = json.loads((root / "marks.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line == saved
    assert {"seed", "bar", "verdict", "note", "excerpt", "genre"} <= set(line)
    assert line["seed"] == 4 and line["bar"] == 4 and line["verdict"] == "bad"
    assert line["genre"] == "trap"
    assert line["score"] == "score-2.json"
    assert line["score_sha256"] == render.sha256(path)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*", line["time"])
    assert FILL_HAT in line["excerpt"]
    # the next seed of the genre quotes it; another genre does not
    request = handoff.write_beat(root, 9, genre="trap", bpm=140, key="A minor")
    text = request.request.read_text(encoding="utf-8")
    assert "too busy at the end" in text and FILL_HAT in text
    other = handoff.write_beat(root, 10, genre="lofi")
    assert FILL_HAT not in other.request.read_text(encoding="utf-8")


def test_the_cli_marks_and_lists_a_seed(tmp_path, capsys):
    root = tmp_path / "beats"
    place(root, 4, body())
    place(root, 5, body())
    base = ["beat", "marks", "--root", str(root)]
    assert main([*base, "--seed", "4", "--bar", "3", "good", "--note", "the 808 sits"]) == 0
    assert main([*base, "--seed", "5", "--bar", "2", "bad"]) == 0
    capsys.readouterr()
    assert main([*base, "--seed", "4", "--list"]) == 0
    out = capsys.readouterr().out
    assert "bar 3" in out and "good" in out and "the 808 sits" in out
    assert "bar 2" not in out
    assert main([*base, "--seed", "4", "--bar", "0", "bad"]) == 2
    assert "bar" in capsys.readouterr().err
    assert main([*base, "--seed", "4", "--bar", "9", "bad"]) == 2
    # a mark made on an older score says so when listed
    place(root, 4, body(bpm=170), number=2)
    assert main([*base, "--seed", "4", "--list"]) == 0
    assert "older score" in capsys.readouterr().out
