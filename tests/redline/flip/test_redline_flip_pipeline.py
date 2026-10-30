import json
import stat

import numpy as np
import pytest
import soundfile as sf
from redline.flip import model, pipeline
from redline.flip.stems import DRUM_STEMS, FOUR_STEMS
from redline.song import model as song_model

pytest.importorskip("redline_dsp")

RATE = 22050
OUT_RATE = 48000
BPM = 140.0
STEP_S = 60.0 / BPM / 4
BAR_S = STEP_S * 16
BARS = 4
DOWNBEAT = 0.1
NOTES = ((0, 6, 36), (6, 6, 38), (32, 8, 36))


def kick() -> np.ndarray:
    t = np.arange(int(0.2 * RATE)) / RATE
    phase = 2 * np.pi * (50 * t + 90 * 0.05 * (1 - np.exp(-t / 0.05)))
    return (np.sin(phase) * np.exp(-t / 0.07)).astype(np.float32)


def eight_oh_eight(midi: int, steps: int) -> np.ndarray:
    n = int(steps * STEP_S * RATE)
    t = np.arange(n) / RATE
    env = np.exp(-t / 0.6) * np.minimum(1.0, t / 0.004)
    env[-int(0.005 * RATE) :] *= np.linspace(1, 0, int(0.005 * RATE))
    hz = 440.0 * 2 ** ((midi - 69) / 12)
    return (0.8 * np.sin(2 * np.pi * hz * t) * env).astype(np.float32)


def make_stems() -> dict[str, np.ndarray]:
    frames = int((DOWNBEAT + BARS * BAR_S) * RATE) + RATE // 4
    drums = np.zeros(frames, np.float32)
    bass = np.zeros(frames, np.float32)
    body = kick()
    for bar in range(BARS):
        for pos in (0, 6, 10, 15):
            at = int((DOWNBEAT + (bar * 16 + pos) * STEP_S) * RATE)
            drums[at : at + body.size] += body[: frames - at] * (0.4 if pos == 15 else 0.9)
    for step, length, midi in NOTES:
        at = int((DOWNBEAT + step * STEP_S) * RATE)
        note = eight_oh_eight(midi, length)
        bass[at : at + note.size] += note
    t = np.arange(frames) / RATE
    other = (0.1 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
    mono = {"drums": drums, "bass": bass, "other": other, "vocals": np.zeros(frames, np.float32)}
    return {k: np.column_stack([v, v]) for k, v in mono.items()}


class FakeFour:
    id = "fake:four"
    names = FOUR_STEMS

    def __init__(self) -> None:
        self.calls = 0
        self.stems = make_stems()

    def __call__(self, samples, rate):
        self.calls += 1
        assert samples.shape == self.stems["drums"].shape
        return self.stems


class FakeDrumSep:
    id = "fake:drumsep"
    names = DRUM_STEMS

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, samples, rate):
        self.calls += 1
        out = {name: np.zeros_like(samples) for name in self.names}
        out["hh"] = samples * np.float32(0.25)
        out["kick"] = samples * np.float32(0.75)
        return out


@pytest.fixture(scope="module")
def analysed(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    for top in song_model.ALLOWED:
        (root / top).mkdir()
    stems = make_stems()
    source = root / "input" / "beats"
    source.mkdir()
    path = source / "demo.wav"
    sf.write(path, sum(stems.values()), RATE)
    fake = FakeFour()
    result = pipeline.analyse(path, root / "work", separator=fake, root=root)
    return root, path, result, fake


def folder_of(analysed):
    root, _, result, _ = analysed
    return root / "work" / "flips" / result["sha8"]


def flip_data(folder, **changes):
    data = json.loads((folder / "draft.json").read_text(encoding="utf-8"))
    data.update(changes)
    return data


def test_analyse_writes_the_analysis_and_a_default_draft(analysed):
    _, _, result, fake = analysed
    folder = folder_of(analysed)
    assert fake.calls == 1
    data = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    assert len(result["sha8"]) == 8 and folder.name == result["sha8"]
    assert data["bpm"] == pytest.approx(BPM, abs=0.1)
    assert data["downbeat_s"] == pytest.approx(DOWNBEAT, abs=0.01)
    assert len(data["bar_starts_s"]) == BARS
    assert data["bar_starts_s"][-1] == pytest.approx(DOWNBEAT + (BARS - 1) * BAR_S, abs=0.01)
    steps = {(n["step"], n["midi"]) for n in data["notes"]}
    assert {(0, 36), (6, 38), (32, 36)} <= steps
    assert data["counts"]["notes"] == len(data["notes"])
    assert data["rate"] == OUT_RATE
    for name in ("drums", "bass", "other"):
        info = sf.info(folder / "stems48" / f"{name}.wav")
        assert info.samplerate == OUT_RATE and info.frames == data["frames"]

    draft = model.load(folder / "draft.json")
    assert draft.source == "input/beats/demo.wav"
    assert draft.bpm == pytest.approx(data["bpm"])
    assert draft.bass_mode == "original" and draft.hats_mode == "original"
    assert draft.gains == {"drums": 0, "other": 0, "bass": 0}
    assert [(b.from_bar, b.to_bar) for b in draft.arrangement] == [(0, BARS)]
    assert stat.S_IMODE((folder / "draft.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((folder / "analysis.json").stat().st_mode) == 0o600


def test_a_second_analysis_keeps_the_draft_the_page_edited(analysed):
    root, path, _, fake = analysed
    folder = folder_of(analysed)
    edited = flip_data(folder)
    edited["arrangement"] = [{"label": "mine", "from_bar": 1, "to_bar": 3}]
    model.save_draft(folder, model.parse(edited))
    pipeline.analyse(path, root / "work", separator=fake, root=root)
    assert fake.calls == 1
    assert [b.label for b in model.load(folder / "draft.json").arrangement] == ["mine"]


def write_version(folder, **changes):
    data = flip_data(folder)
    data["arrangement"] = [
        {"label": "a", "from_bar": 2, "to_bar": 4},
        {"label": "b", "from_bar": 0, "to_bar": 1},
    ]
    data.update(changes)
    return model.save_new(folder, model.parse(data))


def test_render_writes_a_wav_as_long_as_the_bars_in_play_order(analysed):
    folder = folder_of(analysed)
    n = write_version(folder)
    path = pipeline.render(folder, n)
    assert path == folder / f"flip-{n}.wav"
    info = sf.info(path)
    assert info.samplerate == OUT_RATE and info.channels == 2 and info.subtype == "FLOAT"
    assert info.frames == pytest.approx(3 * BAR_S * OUT_RATE, abs=3)
    assert (folder / f"flip-{n}.report.md").is_file()


def energy(x):
    return float(np.sqrt(np.mean(np.square(x))))


def test_our_808_plays_the_source_notes_of_each_play_bar(analysed):
    folder = folder_of(analysed)
    quiet = {"drums": -60, "other": -60, "bass": 0}
    data = flip_data(folder, gains=quiet, bass808={"mode": "ours", "params": {}})
    # source bar 2 holds a note at step 0, source bar 1 holds none
    data["arrangement"] = [
        {"label": "a", "from_bar": 2, "to_bar": 3},
        {"label": "b", "from_bar": 1, "to_bar": 2},
    ]
    n = model.save_new(folder, model.parse(data))
    out, rate = sf.read(pipeline.render(folder, n), dtype="float32")
    bar = int(BAR_S * rate)
    assert energy(out[: bar // 4]) > 0.05
    assert energy(out[bar + bar // 4 : 2 * bar]) < 0.002


def test_the_flips_grid_overrides_the_analysis_grid(analysed):
    folder = folder_of(analysed)
    quiet = {"drums": -60, "other": -60, "bass": 0}
    data = flip_data(folder, gains=quiet, bass808={"mode": "ours", "params": {}})
    data["grid"] = {"bpm": BPM, "downbeat_s": DOWNBEAT + 0.5 * BAR_S, "swing": 50}
    data["arrangement"] = [{"label": "a", "from_bar": 0, "to_bar": 2}]
    n = model.save_new(folder, model.parse(data))
    out, rate = sf.read(pipeline.render(folder, n), dtype="float32")
    # shifted half a bar, the notes of the first bars fall before the downbeat or move later
    assert energy(out[: int(0.3 * rate)]) < 0.002


def test_hats_removed_run_drumsep_once_on_the_drums_and_originals_never(analysed):
    folder = folder_of(analysed)
    sep = FakeDrumSep()
    n = write_version(folder)
    pipeline.render(folder, n, drumsep=sep)
    assert sep.calls == 0
    removed = write_version(folder, hats={"mode": "removed", "remove": ["hh"], "patterns": []})
    out, _ = sf.read(pipeline.render(folder, removed, drumsep=sep), dtype="float32")
    assert sep.calls == 1
    again, _ = sf.read(pipeline.render(folder, removed, drumsep=sep), dtype="float32")
    assert sep.calls == 1
    base, _ = sf.read(folder / f"flip-{n}.wav", dtype="float32")
    assert energy(out) < energy(base)
    np.testing.assert_array_equal(out, again)


def test_hats_ours_follow_the_pattern_of_their_block(analysed):
    folder = folder_of(analysed)
    data = flip_data(
        folder,
        hats={"mode": "ours", "remove": ["hh"], "patterns": ["16ths"]},
        gains={"drums": -60, "other": -60, "bass": -60},
    )
    data["arrangement"] = [{"label": "a", "from_bar": 0, "to_bar": 1}]
    n = model.save_new(folder, model.parse(data))
    out, _ = sf.read(pipeline.render(folder, n, drumsep=FakeDrumSep()), dtype="float32")
    assert energy(out) > 1e-4


def test_render_of_a_missing_flip_or_missing_stems_says_so(analysed, tmp_path):
    folder = folder_of(analysed)
    with pytest.raises(pipeline.PipelineError, match="flip-99"):
        pipeline.render(folder, 99)
    n = write_version(folder)
    bare = tmp_path / "work" / "flips" / "deadbeef"
    bare.mkdir(parents=True)
    (bare / f"flip-{n}.json").write_text((folder / f"flip-{n}.json").read_text(encoding="utf-8"))
    with pytest.raises(pipeline.PipelineError, match="flip_stems"):
        pipeline.render(bare, n)


def test_an_arrangement_past_the_last_bar_is_refused_before_any_render(analysed):
    folder = folder_of(analysed)
    data = flip_data(folder)
    data["arrangement"] = [{"label": "a", "from_bar": 0, "to_bar": BARS + 3}]
    n = model.save_new(folder, model.parse(data))
    with pytest.raises(ValueError, match="outside"):
        pipeline.render(folder, n)
    assert not (folder / f"flip-{n}.wav").exists()


def test_a_file_that_is_no_audio_is_refused(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "x.wav").write_bytes(b"not audio")
    with pytest.raises(pipeline.PipelineError):
        pipeline.analyse(tmp_path / "input" / "x.wav", tmp_path / "work", FakeFour(), tmp_path)


def test_lineage_of_a_plain_file_is_the_file(analysed):
    root, _, result, _ = analysed
    line = pipeline.lineage("input/beats/demo.wav", root)
    assert (line.source, line.sha8, line.used) == ("input/beats/demo.wav", result["sha8"], None)


def test_lineage_of_a_flip_render_opens_its_json_and_source(analysed):
    root, _, result, _ = analysed
    folder = folder_of(analysed)
    n = write_version(folder)
    line = pipeline.lineage(f"work/flips/{result['sha8']}/flip-{n}.wav", root)
    assert (line.source, line.sha8, line.used) == ("input/beats/demo.wav", result["sha8"], n)


@pytest.mark.parametrize(
    "beat",
    [
        "work/flips/{sha}/flip-77.wav",
        "work/flips/zzzzzzzz/flip-1.wav",
        "work/flips/{sha}/../{sha}/flip-1.wav",
        "input/missing.wav",
    ],
)
def test_lineage_refuses_a_flip_without_json_and_a_missing_file(analysed, beat):
    root, _, result, _ = analysed
    write_version(folder_of(analysed))
    with pytest.raises((pipeline.PipelineError, model.FlipError, ValueError)):
        pipeline.lineage(beat.format(sha=result["sha8"]), root)


def test_lineage_refuses_a_flip_json_that_points_outside(analysed):
    root, _, result, _ = analysed
    folder = folder_of(analysed)
    n = write_version(folder)
    data = json.loads((folder / f"flip-{n}.json").read_text(encoding="utf-8"))
    data["source"] = "input/../../etc/passwd"
    bad = folder / f"flip-{n + 50}.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises((pipeline.PipelineError, model.FlipError)):
        pipeline.lineage(f"work/flips/{result['sha8']}/flip-{n + 50}.wav", root)


def test_a_symlinked_flip_json_that_leaves_work_is_refused(analysed, tmp_path):
    root, _, result, _ = analysed
    folder = folder_of(analysed)
    n = write_version(folder)
    outside = root / "input" / "elsewhere.json"
    outside.write_text((folder / f"flip-{n}.json").read_text(encoding="utf-8"))
    link = folder / f"flip-{n + 60}.json"
    link.symlink_to(outside)
    with pytest.raises((pipeline.PipelineError, song_model.SongError)):
        pipeline.lineage(f"work/flips/{result['sha8']}/flip-{n + 60}.wav", root)
    with pytest.raises((pipeline.PipelineError, song_model.SongError)):
        pipeline.render(folder, n + 60)


def test_propose_makes_blocks_that_parse_as_an_arrangement(analysed):
    _, path, _, _ = analysed
    folder = folder_of(analysed)
    text = "[Intro]\none\n[Verse]\ntwo\nthree\n[Hook]\nfour\n"
    blocks = pipeline.propose_blocks(path, folder, text)
    assert [b["label"] for b in blocks] == ["intro", "verse", "hook"]
    assert [b["to_bar"] - b["from_bar"] for b in blocks] == [min(4, BARS), 2, 1]
    data = flip_data(folder, arrangement=blocks)
    assert model.parse(data).play_bars == sum(b["to_bar"] - b["from_bar"] for b in blocks)


def test_a_text_without_lines_proposes_nothing(analysed):
    _, path, _, _ = analysed
    with pytest.raises(pipeline.PipelineError, match="no lines"):
        pipeline.propose_blocks(path, folder_of(analysed), "[Verse]\n")


def test_clean_keeps_the_newest_and_the_ones_a_song_uses(analysed):
    folder = folder_of(analysed)
    made = [write_version(folder) for _ in range(3)]
    for n in made:
        pipeline.render(folder, n)
    gone = pipeline.clean(folder, {made[0]})
    assert made[1] in gone and made[0] not in gone and made[2] not in gone
    assert (folder / f"flip-{made[0]}.wav").is_file()
    assert not (folder / f"flip-{made[1]}.wav").exists()
    assert (folder / f"flip-{made[1]}.json").is_file()


@pytest.fixture
def cli_repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for top in song_model.ALLOWED:
        (root / top).mkdir(parents=True)
    monkeypatch.setattr(song_model, "ROOT", root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(pipeline, "FourStems", FakeFour)
    sf.write(root / "input" / "demo.wav", sum(make_stems().values()), RATE)
    return root


def test_the_flip_commands_analyse_render_propose_and_clean(cli_repo, capsys):
    from redline.cli import main

    assert main(["flip", "analyse", "input/demo.wav"]) == 0
    assert "BPM" in capsys.readouterr().out
    (sha8,) = [p.name for p in (cli_repo / "work" / "flips").iterdir()]
    folder = cli_repo / "work" / "flips" / sha8
    for _ in range(3):
        write_version(folder)
    assert main(["flip", "render", "input/demo.wav", "--flip", "1"]) == 0
    assert main(["flip", "render", "input/demo.wav", "--flip", "3"]) == 0
    assert (folder / "flip-1.wav").is_file() and (folder / "flip-3.wav").is_file()
    assert main(["flip", "render", "input/demo.wav", "--flip", "9"]) == 2

    seed = cli_repo / "private" / "lyrics" / "generated" / "4"
    seed.mkdir(parents=True)
    (seed / "text.txt").write_text("[Verse]\none\ntwo\n", encoding="utf-8")
    capsys.readouterr()
    assert main(["flip", "propose", "input/demo.wav", "--text-seed", "4"]) == 0
    blocks = json.loads(capsys.readouterr().out)
    assert [(b["label"], b["to_bar"] - b["from_bar"]) for b in blocks] == [("verse", 2)]
    assert main(["flip", "propose", "input/demo.wav", "--text-seed", "5"]) == 2

    used = song_model.new_song("s", {"file": f"work/flips/{sha8}/flip-1.wav"})
    song_model.save(used, 0)
    assert main(["flip", "render", f"work/flips/{sha8}/flip-1.wav", "--flip", "2"]) == 0
    assert main(["flip", "clean", "input/demo.wav"]) == 0
    assert (folder / "flip-1.wav").is_file()
    assert not (folder / "flip-2.wav").exists()
    assert (folder / "flip-3.wav").is_file()


def test_job_flip_stems_and_flip_render_write_their_results(cli_repo, monkeypatch):
    from redline.cli import main

    song_model.save(song_model.new_song("s", {"file": "input/demo.wav"}), 0)
    out = cli_repo / "work" / "r.json"
    assert main(["song", "job", "--result", str(out), "flip_stems", "s"]) == 0
    sha8 = json.loads(out.read_text(encoding="utf-8"))["sha8"]
    folder = cli_repo / "work" / "flips" / sha8
    n = write_version(folder)
    assert main(["song", "job", "--result", str(out), "--flip", str(n), "flip_render", "s"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["n"] == n
    assert (folder / f"flip-{n}.wav").is_file()
    assert main(["song", "job", "--result", str(out), "flip_render", "s"]) == 2
    assert main(["song", "job", "--result", str(out), "--flip", "99", "flip_render", "s"]) == 2


@pytest.mark.parametrize("bad", ["-1", "0", "1;ls", "../x", "1.5", ""])
def test_a_hostile_flip_number_is_refused_by_the_job_parser(bad):
    from redline.cli.parser import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["song", "job", "--result", "/r/x.json", "--flip", bad, "flip_render", "s"]
        )
