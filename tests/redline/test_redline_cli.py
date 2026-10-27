import hashlib
import json

import numpy as np
import pytest
from redline_signals import tone, write

from redline.cli import main
from redline.compare import CONFIRMED, REFS_HIGHER
from redline.run import measure_run


class HalfSeparator:
    id = "fake:half"

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, samples, rate):
        self.calls += 1
        return samples * 0.5, samples * 0.5


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    # 1 kHz stereo sines: L dBFS reads L LUFS (EBU Tech 3341); the track is the long, quiet one
    folder = tmp_path / "in"
    folder.mkdir()
    track = write(folder, "track.wav", tone(1000, 30, rate=48000, amp=10 ** (-20 / 20)), 48000)
    refs = folder / "refs"
    refs.mkdir()
    write(refs, "a.wav", tone(1000, 10, amp=10 ** (-10 / 20)), 44100, subtype="FLOAT")
    write(refs, "b.wav", tone(1000, 11, amp=10 ** (-11 / 20)), 44100, subtype="FLOAT")
    return track, refs


def test_measure_run_writes_report_and_measurements(tmp_path, inputs):
    track, refs = inputs
    before = {p: digest(p) for p in (tmp_path / "in").rglob("*.wav")}
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())

    data = json.loads((run_dir / "measurements.json").read_text(encoding="utf-8"))
    assert data["track"]["values"]["mix.lufs"] == pytest.approx(-20.0, abs=0.2)
    assert [r["values"]["mix.lufs"] for r in data["refs"]] == pytest.approx([-10, -11], abs=0.2)

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "-20.0" in report
    lufs_line = next(line for line in report.splitlines() if "громкость микса" in line)
    assert REFS_HIGHER in lufs_line
    length_line = next(line for line in report.splitlines() if line.startswith("| Длина трека"))
    assert CONFIRMED in length_line

    assert {p: digest(p) for p in (tmp_path / "in").rglob("*.wav")} == before
    written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"in", "work"}


def test_second_run_reuses_stems_and_measurements(tmp_path, inputs):
    track, refs = inputs
    separator = HalfSeparator()
    measure_run(track, sorted(refs.iterdir()), tmp_path / "work", separator)
    calls = separator.calls
    second = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", separator)
    assert separator.calls == calls == 3
    data = json.loads((second / "measurements.json").read_text(encoding="utf-8"))
    assert all(f["cached"] for f in [data["track"], *data["refs"]])


def test_cli_refuses_a_missing_track(tmp_path, inputs, capsys):
    _, refs = inputs
    code = main(["measure", "--track", str(tmp_path / "nope.wav"), "--ref", str(refs)])
    assert code == 2
    assert "nope.wav" in capsys.readouterr().err


def test_cli_refuses_a_reference_folder_without_audio(tmp_path, inputs, capsys):
    track, _ = inputs
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("not audio", encoding="utf-8")
    code = main(["measure", "--track", str(track), "--ref", str(empty)])
    assert code == 2
    assert "no reference" in capsys.readouterr().err


def test_cli_help_lists_measure(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    assert "measure" in capsys.readouterr().out


def test_silent_reference_is_reported_not_crashed(tmp_path, inputs):
    track, refs = inputs
    write(refs, "c.wav", np.zeros((44100 * 5, 2), dtype=np.float32), 44100, subtype="FLOAT")
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    data = json.loads((run_dir / "measurements.json").read_text(encoding="utf-8"))
    assert data["refs"][2]["values"]["mix.lufs"] is None


def test_report_prints_counts_as_integers_and_small_shares_with_a_decimal(tmp_path, inputs):
    track, refs = inputs
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    lines = (run_dir / "report.md").read_text(encoding="utf-8").splitlines()
    tails = next(line for line in lines if line.startswith("| концов фраз найдено"))
    assert ".0" not in tails
    hot = next(line for line in lines if line.startswith("| доля сэмплов выше -1 dBFS"))
    assert "| 0.5% |" in hot
