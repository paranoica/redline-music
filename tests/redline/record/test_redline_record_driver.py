import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.record.latency import RoundTrip
from redline.record.process import EngineError, EngineProcess, engine_argv, parse_startup
from redline.record.session import Session, monitor_ms, quantum_warning
from redline.takes.rank import read_dropouts

FAKE = [sys.executable, str(Path(__file__).with_name("fake_rec.py"))]
RATE = 48000


@pytest.fixture
def beat(tmp_path):
    (tmp_path / "beat").mkdir()
    path = tmp_path / "beat" / "beat.wav"
    sf.write(path, np.zeros((RATE // 10, 2), dtype=np.float32), RATE, subtype="FLOAT")
    return path


@pytest.fixture
def engine(beat):
    process = EngineProcess.launch(engine_argv(FAKE, beat, 64))
    yield process
    process.close()


def test_the_startup_line_gives_rate_quantum_ports_and_the_port_estimate():
    startup = parse_startup(
        '{"ready": true, "rate": 48000, "quantum": 64, "devices": ["a:in", "b:out"],'
        ' "latency": {"capture": [64, 96], "playback": [128, 192]}}'
    )
    assert (startup.rate, startup.quantum) == (48000, 64)
    assert startup.devices == ("a:in", "b:out")
    # the worst of each range, in and out
    assert startup.estimate == 96 + 192


def test_a_startup_line_with_a_plain_round_trip_estimate():
    startup = parse_startup('{"rate": 44100, "quantum": 128, "latency_estimate": 512}')
    assert (startup.rate, startup.quantum, startup.estimate) == (44100, 128, 512)
    assert startup.devices == ()


def test_a_startup_line_without_latency_has_no_estimate():
    assert parse_startup('{"rate": 48000, "quantum": 64}').estimate is None


@pytest.mark.parametrize(
    "line",
    ["not json", "[]", '{"quantum": 64}', '{"rate": 0, "quantum": 64}', '{"rate": 48000}'],
)
def test_a_startup_line_without_rate_or_quantum_is_refused(line):
    with pytest.raises(EngineError):
        parse_startup(line)


def test_an_error_reply_at_startup_is_the_engine_message():
    with pytest.raises(EngineError, match="no mic port"):
        parse_startup('{"reply": "error", "error": "no mic port"}')


def test_the_engine_starts_and_reports_what_it_was_granted(engine):
    assert engine.startup.rate == RATE
    assert engine.startup.quantum == 64
    assert engine.startup.estimate == 64 + 256
    assert engine.startup.devices == ("fake:capture_1", "fake:playback_1")


def test_no_jack_is_a_clear_engine_error(beat, monkeypatch):
    monkeypatch.setenv("FAKE_REC_FAIL", "nojack")
    with pytest.raises(EngineError, match="cannot connect to JACK"):
        EngineProcess.launch(engine_argv(FAKE, beat, 64))


def test_a_missing_binary_is_an_engine_error(tmp_path, beat):
    with pytest.raises(EngineError, match="cannot run"):
        EngineProcess.launch(engine_argv([str(tmp_path / "nothing")], beat, 64))


def test_a_rack_over_budget_comes_back_with_what_was_switched_off(engine):
    rack = json.dumps({"effects": [{"effect": "gain"}, {"effect": "room"}, {"effect": "tune"}]})
    assert engine.set_rack(rack, None) == (30, [])
    assert engine.set_rack(rack, 15) == (10, ["tune", "room"])


def test_an_engine_error_reply_raises(engine, tmp_path):
    taken = tmp_path / "take-01.wav"
    taken.write_bytes(b"")
    with pytest.raises(EngineError, match="already exists"):
        engine.start(taken, 0)


def names(folder):
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def test_space_records_takes_one_after_another_into_the_song_folder(engine, tmp_path):
    folder = tmp_path / "input" / "takes" / "song"
    folder.mkdir(parents=True)
    (folder / "take-01.wav").write_bytes(b"earlier")
    session = Session(engine, folder, RoundTrip(300, "calibrated"), 64)
    assert "take-02" in session.toggle()
    assert engine.meters().recording
    assert "take-02" in session.toggle()
    assert not engine.meters().recording
    data = json.loads((folder / "take-02.json").read_text(encoding="utf-8"))
    assert data["offset"] == 300 and data["offset_source"] == "calibrated"
    assert read_dropouts(folder / "take-02.wav", RATE) == (0, 0.0, None)
    assert sf.info(folder / "take-02.wav").subtype == "FLOAT"
    session.toggle()
    session.toggle()
    assert names(folder) == [
        "take-01.wav",
        "take-02.json",
        "take-02.wav",
        "take-03.json",
        "take-03.wav",
    ]


def test_r_discards_this_session_takes_from_the_last_and_never_older_ones(engine, tmp_path):
    folder = tmp_path / "song"
    folder.mkdir()
    (folder / "take-01.wav").write_bytes(b"earlier")
    session = Session(engine, folder, RoundTrip(300, "estimate"), 64)
    for _ in range(4):
        session.toggle()
    assert "take-03" in session.discard_last()
    assert "take-02" in session.discard_last()
    assert "nothing" in session.discard_last()
    assert names(folder) == ["take-01.wav"]
    # the numbers are taken again
    session.toggle()
    session.toggle()
    assert "take-02.wav" in names(folder)


def test_r_while_recording_stops_and_throws_the_take_away(engine, tmp_path):
    session = Session(engine, tmp_path, RoundTrip(None, "unknown"), 64)
    session.toggle()
    assert "take-01" in session.discard_last()
    assert names(tmp_path) == []
    assert not engine.meters().recording


def test_the_take_offset_is_zero_when_the_round_trip_is_unknown(engine, tmp_path):
    session = Session(engine, tmp_path, RoundTrip(None, "unknown"), 64)
    session.toggle()
    session.toggle()
    assert json.loads((tmp_path / "take-01.json").read_text(encoding="utf-8"))["offset"] == 0


def test_the_engine_dropouts_reach_the_take_log(beat, tmp_path, monkeypatch):
    dropouts = [{"start": 100, "frames": 50, "causes": ["xrun"]}]
    monkeypatch.setenv("FAKE_REC_DROPOUTS", json.dumps(dropouts))
    process = EngineProcess.launch(engine_argv(FAKE, beat, 64))
    try:
        session = Session(process, tmp_path, RoundTrip(10, "calibrated"), 64)
        session.toggle()
        session.toggle()
    finally:
        process.close()
    assert read_dropouts(tmp_path / "take-01.wav", RATE)[0] == 1


def test_quit_keeps_a_running_take_with_its_log_and_ends_the_engine(engine, tmp_path):
    session = Session(engine, tmp_path, RoundTrip(10, "calibrated"), 64)
    session.toggle()
    session.close()
    assert names(tmp_path) == ["take-01.json", "take-01.wav"]
    assert engine.exited()


def test_the_status_line_shows_take_level_rate_quantum_latency_and_xruns(engine, tmp_path):
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    engine.set_rack(json.dumps({"effects": [{"effect": "gain"}]}), None)
    session.toggle()
    line = session.status(engine.meters())
    assert "REC take-01" in line
    assert "-6.0 dB" in line  # the fake's mic peak, 0.5
    assert "48000 Hz" in line and "quantum 64" in line
    assert "xruns 1" in line
    assert "monitor 6.2 ms" in line  # (300 frames + the rack's 0) at 48 kHz


def test_the_quantum_warning_starts_above_128():
    assert quantum_warning(64) is None
    assert quantum_warning(128) is None
    assert "under 10 ms is not met" in quantum_warning(256)


def test_monitor_latency_adds_the_rack_to_the_round_trip():
    assert monitor_ms(RoundTrip(240, "calibrated"), 240, 48000) == pytest.approx(10.0)
    assert monitor_ms(RoundTrip(None, "unknown"), 240, 48000) is None
