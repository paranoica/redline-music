import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.record.latency import RoundTrip
from redline.record.process import EngineError, EngineProcess, engine_argv, parse_startup
from redline.record.protocol import WrongRate
from redline.record.session import (
    Session,
    budget_frames,
    level_meter,
    monitor_ms,
    quantum_warning,
)
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


def started(**changes):
    """A startup line as redline-rec writes it (rec/src/protocol.rs, Startup)."""
    line = {
        "event": "started",
        "client": "redline-rec",
        "rate": 48000,
        "quantum": 64,
        "requested_quantum": 64,
        "latency_request": "64/48000",
        "capture_latency": [64, 96],
        "playback_latency": [128, 192],
        "round_trip": 288,
        "round_trip_ms": 6.0,
        "round_trip_source": "port_latency_estimate",
        "under_10ms": True,
        "monitor_budget": 192,
        "mic": "a:capture_1",
        "left": "b:playback_1",
        "right": "b:playback_2",
        "mic_aliases": [],
        "left_aliases": [],
        "right_aliases": [],
        "beat_frames": 96000,
    }
    line.update(changes)
    return json.dumps(line)


def test_the_startup_line_gives_rate_quantum_ports_and_the_port_estimate():
    startup = parse_startup(started())
    assert (startup.rate, startup.quantum) == (48000, 64)
    assert startup.devices == ("a:capture_1", "b:playback_1", "b:playback_2")
    # the worst of each range, in and out
    assert startup.estimate == 96 + 192


def test_a_round_trip_passed_to_the_engine_is_not_taken_for_the_port_estimate():
    startup = parse_startup(started(round_trip=500, round_trip_source="calibrated"))
    assert startup.estimate == 96 + 192


def test_port_aliases_name_the_hardware_in_the_devices():
    line = started(
        mic="system:capture_1",
        mic_aliases=["alsa_pcm:CardA/capture_1"],
        left_aliases=["alsa_pcm:CardA/playback_1", "alias:two"],
    )
    assert parse_startup(line).devices == (
        "system:capture_1 (alsa_pcm:CardA/capture_1)",
        "b:playback_1 (alsa_pcm:CardA/playback_1, alias:two)",
        "b:playback_2",
    )
    other = started(mic="system:capture_1", mic_aliases=["alsa_pcm:CardB/capture_1"])
    assert parse_startup(other).devices[0] != parse_startup(line).devices[0]


def test_a_startup_line_without_aliases_keeps_the_port_names():
    line = json.loads(started())
    for name in ("mic_aliases", "left_aliases", "right_aliases"):
        del line[name]
    assert parse_startup(json.dumps(line)).devices == (
        "a:capture_1",
        "b:playback_1",
        "b:playback_2",
    )


def test_aliases_that_are_not_a_list_of_names_are_refused():
    with pytest.raises(EngineError, match="aliases"):
        parse_startup(started(mic_aliases="alsa_pcm:capture_1"))


def test_ports_left_unconnected_are_not_part_of_the_devices():
    assert parse_startup(started(mic=None, left=None, right=None)).devices == ()
    assert parse_startup(started(right=None)).devices == ("a:capture_1", "b:playback_1")


def test_ports_that_report_no_latency_give_no_estimate():
    line = started(capture_latency=[0, 0], playback_latency=[0, 0], round_trip=0)
    assert parse_startup(line).estimate is None


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        "[]",
        '{"rate": 48000, "quantum": 64, "latency_estimate": 512}',
        started(event="ready"),
        started(rate=0),
        started(quantum=None),
        started(round_trip_source="guessed"),
        started(capture_latency=[64]),
        started(playback_latency=None),
    ],
)
def test_a_startup_line_not_in_the_engine_format_is_refused(line):
    with pytest.raises(EngineError):
        parse_startup(line)


def test_the_wrong_rate_line_carries_the_graph_rate():
    with pytest.raises(WrongRate) as refused:
        parse_startup('{"event": "wrong_rate", "rate": 44100, "beat_rate": 48000}')
    assert (refused.value.rate, refused.value.beat_rate) == (44100, 48000)
    assert isinstance(refused.value, EngineError)


def test_the_beat_is_the_engine_positional_argument(beat):
    assert engine_argv(["redline-rec"], beat, 64) == ["redline-rec", str(beat), "--quantum", "64"]


def test_a_beat_off_the_graph_rate_stops_the_launch_with_that_rate(beat, monkeypatch):
    monkeypatch.setenv("FAKE_REC_RATE", "44100")
    with pytest.raises(WrongRate) as refused:
        EngineProcess.launch(engine_argv(FAKE, beat, 64))
    assert (refused.value.rate, refused.value.beat_rate) == (44100, RATE)


def test_an_error_reply_at_startup_is_the_engine_message():
    with pytest.raises(EngineError, match="no mic port"):
        parse_startup('{"reply": "error", "error": "no mic port"}')


def test_the_engine_starts_and_reports_what_it_was_granted(engine):
    assert engine.startup.rate == RATE
    assert engine.startup.quantum == 64
    assert engine.startup.estimate == 64 + 256
    assert engine.startup.devices == ("fake:capture_1", "fake:playback_1", "fake:playback_2")


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


def test_only_a_calibrated_round_trip_sets_the_monitor_budget():
    # 10 ms at 48 kHz is 480 frames
    assert budget_frames(RoundTrip(300, "calibrated"), RATE) == 180
    assert budget_frames(RoundTrip(600, "calibrated"), RATE) == 0
    # the engine applies its own port-estimate budget when none is sent
    assert budget_frames(RoundTrip(300, "estimate"), RATE) is None
    assert budget_frames(RoundTrip(None, "unknown"), RATE) is None


def test_a_null_peak_in_the_meters_reads_as_infinite_not_a_crash():
    from redline.record.protocol import meters

    reply = {
        "mic_peak": None,
        "out_peak": 0.5,
        "xruns": 0,
        "overflows": 0,
        "recording": False,
        "take_frames": 0,
        "rack_latency": 0,
    }
    m = meters(reply)
    assert m.mic_peak == float("inf") and m.out_peak == 0.5
    assert "CLIP" in level_meter(m.mic_peak)


def test_the_meters_carry_the_quantum_and_an_older_engine_none():
    from redline.record.protocol import meters

    reply = {
        "mic_peak": 0.1,
        "out_peak": 0.1,
        "xruns": 0,
        "overflows": 0,
        "recording": False,
        "take_frames": 0,
        "rack_latency": 0,
    }
    assert meters(reply).quantum is None
    assert meters({**reply, "quantum": None}).quantum is None
    assert meters({**reply, "quantum": 256}).quantum == 256


class QuantumEngine:
    """An engine whose meters report a quantum; it records nothing."""

    def __init__(self, quantum):
        self.quantum = quantum
        self.started = []

    def start(self, path, offset):
        self.started.append(path)

    def stop(self):
        raise AssertionError("not reached")

    def close(self):
        pass

    def meters(self):
        from redline.record.protocol import Meters

        return Meters(0.0, 0.0, 0, 0, False, 0, 0, self.quantum)


def test_a_take_is_refused_when_the_quantum_moved_since_the_start(tmp_path):
    engine = QuantumEngine(256)
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    message = session.toggle()
    assert "64" in message and "256" in message and "restart `redline record`" in message
    assert engine.started == [] and session.current is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("quantum", [64, None])
def test_a_take_starts_when_the_quantum_held_or_is_not_reported(tmp_path, quantum):
    engine = QuantumEngine(quantum)
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    assert session.toggle() == "recording take-01"
    assert [p.name for p in engine.started] == ["take-01.wav"]


class FailingStop(QuantumEngine):
    """An engine whose stop fails after the take has started."""

    def stop(self):
        raise EngineError("the take did not finish within 10 s: is the audio running?")


def test_a_failed_stop_leaves_a_sidecar_marked_incomplete_with_the_error(tmp_path):
    session = Session(FailingStop(64), tmp_path, RoundTrip(300, "calibrated"), 64)
    session.toggle()
    with pytest.raises(EngineError, match="did not finish"):
        session.toggle()
    data = json.loads((tmp_path / "take-01.json").read_text(encoding="utf-8"))
    assert data["complete"] is False
    assert "did not finish" in data["error"]
    assert read_dropouts(tmp_path / "take-01.wav", RATE)[:2] == (None, None)
    assert session.current is None


TIMED_OUT = "the take did not finish within 10 s: is the audio running?"


class LateEngine(QuantumEngine):
    """Its first stop times out while the take goes on being finished; later stops answer
    with what the second stop gives: a report, or an error."""

    def __init__(self, second):
        super().__init__(64)
        self.second = second
        self.stops = 0
        self.holding = False

    def start(self, path, offset):
        # the engine creates the file at the start
        path.write_bytes(b"")
        self.started.append(path)
        self.holding = True
        return None

    def stop(self):
        self.stops += 1
        if self.stops == 1:
            raise EngineError(TIMED_OUT)
        self.holding = False
        if isinstance(self.second, str):
            raise EngineError(self.second)
        return self.second

    def meters(self):
        from redline.record.protocol import Meters

        return Meters(0.0, 0.0, 0, 0, self.holding, 0, 0, self.quantum)


def late_report(path):
    from redline.record.protocol import Dropout, TakeReport

    return TakeReport(path, RATE, 2 * RATE, 300, [Dropout(4800, 480, ("xrun",))])


def test_after_a_stop_timed_out_the_next_key_collects_the_take_and_logs_it_complete(tmp_path):
    take = (tmp_path / "take-01.wav").resolve()
    engine = LateEngine(late_report(take))
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    session.toggle()
    with pytest.raises(EngineError, match="did not finish"):
        session.toggle()
    message = session.toggle()
    assert engine.stops == 2 and len(engine.started) == 1
    assert "take-01" in message and "late" in message
    data = json.loads((tmp_path / "take-01.json").read_text(encoding="utf-8"))
    assert data["complete"] is True and "error" not in data
    assert data["dropouts"] == [[4800, 5280]]
    assert read_dropouts(take, RATE)[0] == 1
    assert session.toggle() == "recording take-02"


def test_a_take_the_writer_failed_on_late_stays_incomplete_with_the_writer_error(tmp_path):
    engine = LateEngine("writing take-01.wav: no space left on device")
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    session.toggle()
    with pytest.raises(EngineError):
        session.toggle()
    with pytest.raises(EngineError, match="no space"):
        session.toggle()
    data = json.loads((tmp_path / "take-01.json").read_text(encoding="utf-8"))
    assert data["complete"] is False and "no space" in data["error"]
    # nothing more will come: the next key records again
    assert session.toggle() == "recording take-02"


def test_a_timed_out_take_discarded_meanwhile_leaves_no_log_when_it_comes(tmp_path):
    take = (tmp_path / "take-01.wav").resolve()
    engine = LateEngine(late_report(take))
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    session.toggle()
    with pytest.raises(EngineError):
        session.toggle()
    session.discard_last()
    session.toggle()
    assert engine.stops == 2
    assert not (tmp_path / "take-01.json").exists()


def test_the_start_reply_carries_a_late_take_or_the_writer_error():
    from redline.record.protocol import late_take

    assert late_take({"reply": "start", "path": "t.wav"}) is None
    report = {"path": "a.wav", "rate": RATE, "frames": 10, "offset": 0, "dropouts": []}
    taken = late_take({"reply": "start", "path": "t.wav", "late": {"take": report}})
    assert taken is not None and not isinstance(taken, str) and taken.frames == 10
    assert late_take({"reply": "start", "late": {"error": "no space"}}) == "no space"
