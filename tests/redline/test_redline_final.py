import json

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, write

from redline.cli import main
from redline.final_run import TIMBRES, master_round, master_targets, run_track, timbre_round
from redline.loudness import integrated_lufs
from redline.master import CEILING_DB
from redline.run import FileResult


class HalfSeparator:
    id = "fake:half"

    def __call__(self, samples, rate):
        return samples * 0.5, samples * 0.5


def song(seconds, rate=44100, amp=0.3):
    rng = make_rng()
    n = int(seconds * rate)
    t = np.arange(n) / rate
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    envelope = 0.65 + 0.35 * np.cos(2 * np.pi * 3 * t)
    voice = np.sin(2 * np.pi * 400 * t) * envelope * 0.5
    mono = amp * (kick + voice) + 0.0005 * rng.standard_normal(n)
    return np.column_stack([mono, mono]).astype(np.float32)


@pytest.fixture
def inputs(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    track = write(folder, "track.wav", song(8), 44100)
    refs = folder / "refs"
    refs.mkdir()
    write(refs, "a.wav", song(6, amp=0.6), 44100, subtype="FLOAT")
    write(refs, "b.wav", song(6, amp=0.9), 44100, subtype="FLOAT")
    return track, refs


def ref(name, lufs):
    return FileResult(name, "wav", 44100, False, values={"mix.lufs": lufs})


def test_master_targets_span_the_references():
    targets = master_targets([ref("a", -9.5), ref("b", -8.9), ref("c", -4.8)])
    assert targets == {"master -11.0": -11.0, "master -8.9": -8.9, "master -4.8": -4.8}


def test_master_targets_need_reference_loudness():
    with pytest.raises(ValueError, match="loudness"):
        master_targets([ref("a", None), ref("b", -9.0)])


def run(inputs, tmp_path, **kwargs):
    track, refs = inputs
    return master_round(
        track,
        sorted(refs.iterdir()),
        tmp_path / "work",
        tmp_path / "out",
        HalfSeparator(),
        **kwargs,
    )


def test_master_round_writes_the_original_and_four_masters(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=1)
    key = json.loads((result.run_dir / "key.json").read_text())
    names = sorted(key["key"].values())
    assert names[-1] == "original" and "no master" in names
    assert len([n for n in names if n.startswith("master ")]) == 3
    levels = []
    for path in sorted(result.listen_dir.iterdir()):
        data, rate = sf.read(path, always_2d=True)
        levels.append(integrated_lufs(data, rate))
    assert max(levels) - min(levels) <= 0.1
    chain = json.loads((result.run_dir / "chain.json").read_text())
    masters = chain["masters"]
    assert sorted(m["name"] for m in masters) == sorted(n for n in names if n.startswith("master "))
    for m in masters:
        assert m["reached"] is True
        assert m["final_lufs"] == pytest.approx(m["target_lufs"], abs=0.2)


def test_louder_targets_clip_more(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=2)
    masters = json.loads((result.run_dir / "chain.json").read_text())["masters"]
    shares = [m["clipped_share"] for m in sorted(masters, key=lambda m: m["target_lufs"])]
    assert shares == sorted(shares)
    assert shares[-1] > shares[0]


def test_master_round_refuses_overlapping_folders(inputs, tmp_path):
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        master_round(
            track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "w" / "o", HalfSeparator()
        )


def test_cli_master_prints_the_listening_folder(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["master", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o"), "--seed", "3"]
    assert main(args, separator=HalfSeparator()) == 0
    assert capsys.readouterr().out.strip().endswith("listen")


def test_cli_master_without_references_exits_2(inputs, tmp_path):
    track, _ = inputs
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["master", "--track", str(track), "--ref", str(empty), "--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 2


SPEC_FILES = [
    ".gitignore",
    "chain.json",
    "compare/final.wav",
    "compare/original.wav",
    "final.wav",
    "original.wav",
    "report.md",
    "stems/beat.wav",
    "stems/vocal.wav",
]


def make_track(inputs, tmp_path):
    track, refs = inputs
    return run_track(track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator())


def test_run_writes_the_spec_folder(inputs, tmp_path):
    folder = make_track(inputs, tmp_path)
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert files == SPEC_FILES
    assert (folder / ".gitignore").read_text() == "*\n"
    assert folder.is_relative_to(tmp_path / "o")


def test_final_is_hot_and_original_matches_its_loudness(inputs, tmp_path):
    folder = make_track(inputs, tmp_path)
    chain = json.loads((folder / "chain.json").read_text())
    final, rate = sf.read(folder / "final.wav", always_2d=True)
    original, _ = sf.read(folder / "original.wav", always_2d=True)
    target = chain["master"]["target_lufs"]
    assert integrated_lufs(final, rate) == pytest.approx(target, abs=0.5)
    assert 20 * np.log10(np.abs(final).max()) <= CEILING_DB + 0.01
    assert integrated_lufs(original, rate) == pytest.approx(integrated_lufs(final, rate), abs=0.2)
    assert sf.info(folder / "original.wav").subtype == "FLOAT"


def test_compare_pair_is_at_one_safe_loudness(inputs, tmp_path):
    folder = make_track(inputs, tmp_path)
    levels = []
    for name in ("final", "original"):
        data, rate = sf.read(folder / "compare" / f"{name}.wav", always_2d=True)
        levels.append(integrated_lufs(data, rate))
        assert 20 * np.log10(np.abs(data).max()) < 0
    assert levels[0] == pytest.approx(levels[1], abs=0.1)


def test_stems_rebuild_the_mix_before_the_master(inputs, tmp_path):
    folder = make_track(inputs, tmp_path)
    vocal, _ = sf.read(folder / "stems" / "vocal.wav", always_2d=True)
    beat, _ = sf.read(folder / "stems" / "beat.wav", always_2d=True)
    chain = json.loads((folder / "chain.json").read_text())
    final, _ = sf.read(folder / "final.wav", always_2d=True)
    gain = 10 ** (chain["master"]["gain_db"] / 20)
    # before the clip, final is the stems' sum times the master gain
    unclipped = np.abs((vocal + beat) * gain) < 0.5
    np.testing.assert_allclose(final[unclipped], ((vocal + beat) * gain)[unclipped], atol=2e-3)


def test_report_says_what_was_done_in_words(inputs, tmp_path):
    text = (make_track(inputs, tmp_path) / "report.md").read_text(encoding="utf-8")
    for phrase in ("Вокал", "Мастер", "LUFS", "ближе", "референс"):
        assert phrase in text
    assert "лучше" not in text.replace("не «лучше»", "")


def test_cli_run_prints_the_folder(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["run", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 0
    printed = capsys.readouterr().out.strip()
    (folder,) = list((tmp_path / "o").iterdir())
    assert printed == str(folder)


def test_run_masters_to_the_references_median(inputs, tmp_path):
    folder = make_track(inputs, tmp_path)
    chain = json.loads((folder / "chain.json").read_text())
    refs = sorted(inputs[1].iterdir())
    levels = [integrated_lufs(sf.read(r, always_2d=True)[0], 44100) for r in refs]
    assert chain["master"]["target_lufs"] == pytest.approx(round(float(np.median(levels)), 1))


def test_master_round_output_holds_only_the_letters(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=4)
    out_files = sorted(
        str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file()
    )
    stamp = result.listen_dir.parent.name
    expected = [f"{stamp}/.gitignore"] + [f"{stamp}/listen/{c}.wav" for c in "ABCDE"]
    assert out_files == expected


@pytest.mark.parametrize(("work", "out"), [("w", "w/o"), ("o/w", "o")])
def test_run_refuses_overlapping_folders(inputs, tmp_path, work, out):
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        run_track(track, sorted(refs.iterdir()), tmp_path / work, tmp_path / out, HalfSeparator())


def test_timbre_round_writes_the_original_the_base_and_each_effect(inputs, tmp_path):
    track, refs = inputs
    result = timbre_round(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), seed=5
    )
    key = json.loads((result.run_dir / "key.json").read_text())
    assert key["axis"] == "timbre"
    assert sorted(key["key"].values()) == sorted(["original", "base", *TIMBRES])
    assert len(list(result.listen_dir.iterdir())) == 2 + len(TIMBRES)
