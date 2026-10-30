import contextlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.cli import main
from redline.record import run as record_run
from redline.record import terminal
from redline.record.process import EngineError

FAKE = [sys.executable, str(Path(__file__).with_name("fake_rec.py"))]
RATE = 48000


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(record_run, "engine_command", lambda say: FAKE)
    monkeypatch.setattr(record_run.time, "sleep", lambda seconds: None)


def scripted(keys):
    @contextlib.contextmanager
    def raw_keys():
        pending = list(keys)
        yield lambda timeout: pending.pop(0) if pending else "q"

    return raw_keys


@pytest.fixture
def beat(tmp_path):
    (tmp_path / "in").mkdir()
    path = tmp_path / "in" / "beat.wav"
    sf.write(path, np.zeros((RATE // 10, 2), dtype=np.float32), RATE, subtype="FLOAT")
    return path


def record_args(tmp_path, beat, *extra):
    return [
        "record",
        "--beat",
        str(beat),
        "--song",
        "song",
        "--takes",
        str(tmp_path / "input" / "takes"),
        "--work",
        str(tmp_path / "work"),
        *extra,
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["record"],
        ["record", "--beat", "b.wav"],
        ["record", "--song", "s"],
        ["record", "--beat", "b.wav", "--song", "../s"],
        ["record", "--beat", "b.wav", "--song", ""],
        ["record", "--calibrate", "--song", "s"],
        ["record", "--beat", "b.wav", "--song", "s", "--mode", "cup"],
    ],
)
def test_the_arguments_are_checked(args):
    with pytest.raises(SystemExit) as stopped:
        main(args)
    assert stopped.value.code == 2


def test_no_binary_and_no_way_to_build_it_exits_2_saying_so(tmp_path, beat, monkeypatch, capsys):
    def missing(say):
        raise EngineError("redline-rec is not built and cargo is not installed to build it")

    monkeypatch.setattr(record_run, "engine_command", missing)
    assert main(record_args(tmp_path, beat)) == 2
    assert "cargo is not installed" in capsys.readouterr().err


def test_no_jack_exits_2_with_the_engine_message(tmp_path, beat, fake_engine, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_REC_FAIL", "nojack")
    assert main(record_args(tmp_path, beat)) == 2
    assert "cannot connect to JACK" in capsys.readouterr().err
    assert not (tmp_path / "input" / "takes" / "song").exists()


def test_a_silent_mic_exits_2_saying_what_is_missing(
    tmp_path, beat, fake_engine, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_REC_TONE", "silence")
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    monkeypatch.setattr(terminal, "raw_keys", scripted([" ", " "]))
    assert main(record_args(tmp_path, beat)) == 2
    assert "mic" in capsys.readouterr().err
    assert not (tmp_path / "input" / "takes" / "song").exists()


def test_calibrate_stores_the_round_trip_and_record_then_uses_it(
    tmp_path, beat, fake_engine, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_REC_TONE", "sweep:333")
    work = tmp_path / "work"
    assert main(["record", "--calibrate", "--work", str(work)]) == 0
    out = capsys.readouterr().out
    assert "333 frames" in out and "6.9 ms" in out
    entry = json.loads((work / "record" / "latency.json").read_text(encoding="utf-8"))
    assert entry["calibrations"][0]["frames"] == 333
    monkeypatch.setenv("FAKE_REC_TONE", "")
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    monkeypatch.setattr(terminal, "raw_keys", scripted([" ", " "]))
    assert main(record_args(tmp_path, beat)) == 0
    out = capsys.readouterr().out
    assert "round trip 333 frames (calibrated)" in out
    take = tmp_path / "input" / "takes" / "song" / "take-01.json"
    assert json.loads(take.read_text(encoding="utf-8"))["offset"] == 333


def test_a_speaker_calibration_is_stored_with_a_warning(tmp_path, fake_engine, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_REC_TONE", "sweep:900")
    assert main(["record", "--calibrate", "--mode", "speakers", "--work", str(tmp_path)]) == 0
    assert "speakers" in capsys.readouterr().out


def test_a_failed_calibration_exits_2_and_stores_nothing(
    tmp_path, fake_engine, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_REC_TONE", "silence")
    monkeypatch.setenv("FAKE_REC_TAKE_S", "3")
    assert main(["record", "--calibrate", "--work", str(tmp_path)]) == 2
    assert "silent" in capsys.readouterr().err
    assert not (tmp_path / "record" / "latency.json").exists()


def test_a_session_records_takes_and_points_at_redline_takes(
    tmp_path, beat, fake_engine, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    monkeypatch.setattr(terminal, "raw_keys", scripted([" ", " ", " ", " ", "r"]))
    assert main(record_args(tmp_path, beat)) == 0
    out = capsys.readouterr().out
    folder = tmp_path / "input" / "takes" / "song"
    assert sorted(p.name for p in folder.glob("take-*")) == ["take-01.json", "take-01.wav"]
    assert "48000 Hz" in out and "quantum 64" in out
    assert "JACK port estimate" in out  # no calibration yet
    assert "level check" in out
    assert "redline takes" in out and str(folder) in out
    assert "not met" not in out


def test_a_quantum_above_128_is_warned_about(tmp_path, beat, fake_engine, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    monkeypatch.setenv("FAKE_REC_QUANTUM", "256")
    monkeypatch.setattr(terminal, "raw_keys", scripted([]))
    assert main(record_args(tmp_path, beat)) == 0
    assert "under 10 ms is not met" in capsys.readouterr().out
