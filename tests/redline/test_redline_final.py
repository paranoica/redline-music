import json

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, write

from redline.cli import main
from redline.final_run import master_round, master_targets
from redline.loudness import integrated_lufs
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
