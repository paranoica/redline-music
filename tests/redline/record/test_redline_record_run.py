import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.engine import live
from redline.engine.settings import Settings
from redline.record import run, terminal
from redline.record.latency import LatencyKey, round_trip
from redline.record.process import EngineError
from redline.record.protocol import WrongRate
from redline.record.session import Session
from redline.record.sweep import CalibrationError

FAKE = [sys.executable, str(Path(__file__).with_name("fake_rec.py"))]
RATE = 48000
# BS.1770 on a 0.1 sine in both channels: -0.691 + 10 log10(2 * 0.1**2 / 2), less the
# K-filter's fraction of a dB at 220 Hz
SINE_LUFS = -20.9


def no_wait(seconds):
    pass


def sine(seconds, rate, amp=0.1):
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture
def beat(tmp_path):
    (tmp_path / "in").mkdir()
    path = tmp_path / "in" / "beat.wav"
    sf.write(path, np.column_stack([sine(0.2, 44100)] * 2), 44100, subtype="FLOAT")
    return path


def test_the_beat_is_resampled_to_the_graph_rate_as_float(beat, tmp_path):
    prepared = run.prepare_beat(beat, 48000, tmp_path / "work" / "record")
    info = sf.info(prepared)
    assert (info.samplerate, info.channels, info.subtype) == (48000, 2, "FLOAT")
    assert info.frames == pytest.approx(0.2 * 48000, abs=2)
    assert prepared.parent == tmp_path / "work" / "record"


def test_a_beat_at_the_graph_rate_is_written_unchanged(tmp_path):
    data = np.column_stack([sine(0.1, RATE), sine(0.1, RATE, 0.2)])
    sf.write(tmp_path / "beat.wav", data, RATE, subtype="FLOAT")
    prepared = run.prepare_beat(tmp_path / "beat.wav", RATE, tmp_path / "out")
    assert np.array_equal(sf.read(prepared, dtype="float32")[0], data)


@pytest.mark.parametrize("graph", [48000, 44100])
def test_the_engine_ends_up_with_the_beat_at_the_rate_it_was_granted(
    beat, tmp_path, monkeypatch, graph
):
    monkeypatch.setenv("FAKE_REC_RATE", str(graph))
    folder = tmp_path / "work" / "record"
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, folder))
    try:
        assert engine.startup.rate == graph
        assert sf.info(folder / f"beat-{graph}.wav").samplerate == graph
    finally:
        engine.close()


@pytest.fixture
def launches(monkeypatch):
    seen = []
    launch = run.EngineProcess.launch

    def counted(argv, env=None, **kwargs):
        seen.append((list(argv), env))
        return launch(argv, env, **kwargs)

    monkeypatch.setattr(run.EngineProcess, "launch", counted)
    return seen


def test_a_graph_at_another_rate_restarts_the_engine_on_the_resampled_beat(
    beat, tmp_path, monkeypatch, launches
):
    monkeypatch.setenv("FAKE_REC_RATE", "44100")
    folder = tmp_path / "work" / "record"
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, folder))
    engine.close()
    assert engine.startup.rate == 44100
    assert [argv[len(FAKE)] for argv, _ in launches] == [
        str(folder / "beat-48000.wav"),
        str(folder / "beat-44100.wav"),
    ]
    # the graph's quantum and rate are left to PipeWire and the engine
    assert all(env is None or "PIPEWIRE_QUANTUM" not in env for _, env in launches)


def test_a_graph_at_the_guessed_rate_starts_once(beat, tmp_path, launches):
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, tmp_path))
    engine.close()
    assert len(launches) == 1


def test_a_second_rate_mismatch_is_an_engine_error(beat, tmp_path, monkeypatch, launches):
    monkeypatch.setenv("FAKE_REC_RATE", "44100")
    # a beat that stays at 48 kHz whatever rate is asked for
    with pytest.raises(EngineError, match="44100") as refused:
        run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, RATE, tmp_path))
    assert not isinstance(refused.value, WrongRate)
    assert len(launches) == 2


def test_the_latency_key_is_the_ports_rate_and_quantum(beat, tmp_path):
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, tmp_path))
    try:
        key = run.latency_key(engine.startup)
    finally:
        engine.close()
    assert key == LatencyKey(("fake:capture_1", "fake:playback_1", "fake:playback_2"), RATE, 64)


def test_calibration_finds_the_engine_round_trip_and_stores_it_keyed(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TONE", "sweep:333")
    work = tmp_path / "work"
    key, delay = run.calibrate(FAKE, work, "cup", wait=no_wait)
    assert delay.frames == 333
    stored = round_trip(work / "record" / "latency.json", key, estimate=None)
    assert (stored.frames, stored.source) == (333, "calibrated")
    # the recording is not left behind
    assert sorted(p.name for p in (work / "record").glob("*.wav")) == ["sweep-48000.wav"]


def test_a_calibration_that_hears_nothing_is_refused_and_stores_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "3")
    monkeypatch.setenv("FAKE_REC_TONE", "")
    work = tmp_path / "work"
    with pytest.raises(CalibrationError):
        run.calibrate(FAKE, work, "loopback", wait=no_wait)
    assert not (work / "record" / "latency.json").exists()


def test_the_level_check_measures_the_mic_and_cleans_up(beat, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    folder = tmp_path / "work" / "record"
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, folder))
    try:
        loudness = run.level_check(engine, folder, wait=no_wait)
    finally:
        engine.close()
    assert loudness == pytest.approx(SINE_LUFS, abs=0.3)
    assert not (folder / "level-check.wav").exists()


def test_without_takes_the_monitor_numbers_come_from_the_level_check(beat, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    folder = tmp_path / "work" / "record"
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, folder))
    takes = tmp_path / "input" / "takes" / "song"
    try:
        numbers, source = run.monitor_numbers(
            engine, takes, beat, [], tmp_path / "work", None, Settings(), wait=no_wait
        )
    finally:
        engine.close()
    assert "level check" in source
    assert numbers["pre_gain_db"] == pytest.approx(-18.0 - SINE_LUFS, abs=0.3)
    assert numbers["gate_db"] == live.NEUTRAL_NUMBERS["gate_db"]


def test_with_a_take_and_no_references_the_last_take_sets_the_level(beat, tmp_path):
    takes = tmp_path / "song"
    takes.mkdir()
    sf.write(takes / "take-01.wav", sine(4, RATE, 0.01), RATE, subtype="FLOAT")
    sf.write(takes / "take-02.wav", sine(4, RATE, 0.1), RATE, subtype="FLOAT")
    numbers, source = run.monitor_numbers(
        None, takes, beat, [], tmp_path / "work", None, Settings(), wait=no_wait
    )
    assert "take-02" in source and "neutral" in source
    assert numbers["pre_gain_db"] == pytest.approx(-18.0 - SINE_LUFS, abs=0.3)


class Keys:
    def __init__(self, keys):
        self.keys = list(keys)

    def __call__(self, timeout):
        return self.keys.pop(0) if self.keys else "q"


def test_the_terminal_loop_drives_the_session_by_keys(beat, tmp_path):
    folder = tmp_path / "work" / "record"
    engine = run.open_engine(FAKE, lambda rate: run.prepare_beat(beat, rate, folder))
    takes = tmp_path / "song"
    session = Session(
        engine, takes, round_trip(folder / "latency.json", LatencyKey((), 1, 1), 300), 64
    )
    shown = []
    try:
        terminal.loop(session, engine, Keys([" ", None, " ", " ", "r", "x", "q"]), shown.append)
    finally:
        session.close()
    out = "".join(shown)
    assert "recording take-01" in out and "take-01 kept" in out and "take-02 discarded" in out
    assert "REC take-01" in out and "quantum 64" in out
    assert sorted(p.name for p in takes.iterdir()) == ["take-01.json", "take-01.wav"]
    data = json.loads((takes / "take-01.json").read_text(encoding="utf-8"))
    assert data["offset"] == 300 and data["offset_source"] == "estimate"


def test_a_terminal_is_needed_for_the_keys(monkeypatch):
    monkeypatch.setattr(sys, "stdin", Path(__file__).open(encoding="utf-8"))  # noqa: SIM115
    with pytest.raises(EngineError, match="terminal"), terminal.raw_keys():
        pass


class BrokenMeters:
    """An engine still running whose meters reply cannot be read."""

    startup = type("Startup", (), {"rate": RATE})()

    def __init__(self, exited=False):
        self._exited = exited

    def meters(self):
        raise EngineError("the engine's meters are unreadable: no peak")

    def exited(self):
        return self._exited


def test_unreadable_meters_from_a_running_engine_are_shown_not_fatal(tmp_path):
    engine = BrokenMeters()
    session = Session(
        engine, tmp_path, round_trip(tmp_path / "none.json", LatencyKey((), 1, 1), 300), 64
    )
    shown = []
    terminal.loop(session, engine, Keys([None, None, "q"]), shown.append)
    assert "engine: the engine's meters are unreadable: no peak" in "".join(shown)


def test_unreadable_meters_from_an_engine_that_ended_end_the_loop_with_the_reason(tmp_path):
    engine = BrokenMeters(exited=True)
    session = Session(
        engine, tmp_path, round_trip(tmp_path / "none.json", LatencyKey((), 1, 1), 300), 64
    )
    with pytest.raises(EngineError, match="meters are unreadable"):
        terminal.loop(session, engine, Keys([None, "q"]), [].append)


def test_a_calibration_during_which_the_quantum_moved_is_not_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TONE", "sweep:333")
    monkeypatch.setenv("FAKE_REC_METERS_QUANTUM", "256")
    work = tmp_path / "work"
    with pytest.raises(EngineError, match="quantum moved from 64 to 256"):
        run.calibrate(FAKE, work, "cup", wait=no_wait)
    assert not (work / "record" / "latency.json").exists()
    assert not (work / "record" / "calibration.wav").exists()


def test_the_relaunch_callback_gets_the_graph_rate_before_the_second_start(
    beat, tmp_path, monkeypatch
):
    monkeypatch.setenv("FAKE_REC_RATE", "44100")
    seen = []

    def make(rate):
        seen.append(("beat", rate))
        return run.prepare_beat(beat, rate, tmp_path)

    engine = run.open_engine(FAKE, make, on_relaunch=lambda rate: seen.append(("relaunch", rate)))
    engine.close()
    assert seen == [("beat", run.GUESS_RATE), ("relaunch", 44100), ("beat", 44100)]


def test_the_relaunch_callback_is_silent_when_the_guess_was_right(beat, tmp_path):
    seen = []
    engine = run.open_engine(
        FAKE,
        lambda rate: run.prepare_beat(beat, rate, tmp_path),
        on_relaunch=seen.append,
    )
    engine.close()
    assert seen == []
