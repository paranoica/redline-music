import hashlib
import json
import logging
import subprocess
from pathlib import Path

import numpy as np
import pytest
from redline_signals import tone, write

from redline.cli import main
from redline.compare import CONFIRMED, REFS_HIGHER
from redline.run import MIN_TAILS, fingerprint, measure_run, metrics_key, reliable_tails


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


def test_work_folder_anywhere_in_a_repository_stays_out_of_git(tmp_path, inputs):
    # report files carry reference file names; a work folder outside the repo root
    # (not covered by the root .gitignore) must still never be staged
    track, refs = inputs
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    measure_run(track, sorted(refs.iterdir()), repo / "sub" / "work", HalfSeparator())
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""


def test_metrics_cache_key_follows_code_and_codec():
    base = metrics_key("stems", 16000.0, 16000.0, "wav", 48000, "code-a")
    assert metrics_key("stems", 16000.0, 16000.0, "wav", 48000, "code-b") != base
    assert metrics_key("stems", 16000.0, 16000.0, "mp3", 48000, "code-a") != base
    assert metrics_key("stems", 16000.0, 16000.0, "wav", 44100, "code-a") != base
    assert metrics_key("stems", 16000.0, 16000.0, "wav", 48000, "code-a") == base


def test_tails_count_below_minimum_is_not_a_measurement():
    assert reliable_tails(30.0, MIN_TAILS - 1) is None
    assert reliable_tails(30.0, MIN_TAILS) == 30.0


def test_too_short_reference_names_the_file(tmp_path, inputs, capsys):
    track, refs = inputs
    write(refs, "short.wav", tone(1000, 2, amp=0.3), 44100, subtype="FLOAT")
    code = main(["measure", "--track", str(track), "--ref", str(refs)], separator=HalfSeparator())
    assert code == 2
    assert "short.wav" in capsys.readouterr().err


def test_cli_measure_succeeds_and_prints_the_report_path(tmp_path, inputs, capsys):
    track, refs = inputs
    work = tmp_path / "work"
    args = ["measure", "--track", str(track), "--ref", str(refs), "--work", str(work)]
    assert main(args, separator=HalfSeparator()) == 0
    printed = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed.name == "report.md" and printed.is_file()


def test_cache_hits_are_logged(tmp_path, inputs, caplog):
    track, refs = inputs
    measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    caplog.clear()
    with caplog.at_level(logging.INFO):
        measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert messages.count("stems from cache") == 3
    assert messages.count("measurements from cache") == 3


def test_cpu_fallback_shows_in_the_report(tmp_path, inputs):
    track, refs = inputs

    class FellBack(HalfSeparator):
        fallback_used = True

    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", FellBack())
    assert "на процессоре" in (run_dir / "report.md").read_text(encoding="utf-8")


def test_report_has_reference_range_and_stem_loudness(tmp_path, inputs):
    track, refs = inputs
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    lines = (run_dir / "report.md").read_text(encoding="utf-8").splitlines()
    header = next(line for line in lines if line.startswith("| Замер |"))
    assert "диапазон референсов" in header
    lufs = next(line for line in lines if line.startswith("| громкость микса"))
    assert "-11.0 … -10.0" in lufs
    assert any(line.startswith("| громкость вокала") for line in lines)
    assert any(line.startswith("| громкость бита") for line in lines)


def test_tonal_curves_are_saved(tmp_path, inputs):
    track, refs = inputs
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    data = json.loads((run_dir / "measurements.json").read_text(encoding="utf-8"))
    for stem in ("mix", "vocal", "beat"):
        curve = data["track"]["info"][f"{stem}.tonal_curve"]
        assert len(curve["centers_hz"]) == len(curve["db"]) > 10


def test_mp3_reference_has_no_hot_share(tmp_path, inputs):
    track, refs = inputs
    write(refs, "c.mp3", tone(1000, 12, amp=0.3), 44100, format="MP3")
    run_dir = measure_run(track, sorted(refs.iterdir()), tmp_path / "work", HalfSeparator())
    data = json.loads((run_dir / "measurements.json").read_text(encoding="utf-8"))
    by_name = {r["name"]: r["values"]["mix.hot_share"] for r in data["refs"]}
    assert by_name["c.mp3"] is None
    assert by_name["a.wav"] is not None


def test_existing_gitignore_in_the_work_folder_does_not_let_reports_in(tmp_path, inputs):
    # --work pointing at a folder that already has its own .gitignore, e.g. the repo root
    track, refs = inputs
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"],
        check=True,
    )
    measure_run(track, sorted(refs.iterdir()), repo, HalfSeparator())
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""


def test_unusable_work_folder_is_a_clear_error(tmp_path, inputs, capsys):
    track, refs = inputs
    blocker = tmp_path / "occupied"
    blocker.write_text("a file where the work folder should be", encoding="utf-8")
    args = ["measure", "--track", str(track), "--ref", str(refs), "--work", str(blocker)]
    assert main(args, separator=HalfSeparator()) == 2
    assert "occupied" in capsys.readouterr().err


def test_fingerprint_follows_library_versions():
    assert fingerprint([b"src"], {"librosa": "1.0.0"}) != fingerprint(
        [b"src"], {"librosa": "1.0.1"}
    )
    assert fingerprint([b"src"], {"librosa": "1.0.0"}) != fingerprint(
        [b"src2"], {"librosa": "1.0.0"}
    )
