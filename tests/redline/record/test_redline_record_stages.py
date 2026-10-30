import json
import sys
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.record import driver, terminal
from redline.record.latency import RoundTrip
from redline.record.process import EngineError
from redline.record.protocol import Dropout, Meters, TakeReport
from redline.record.session import Outcome, Session
from redline.record.takes import file_sha256

FAKE = [sys.executable, str(Path(__file__).with_name("fake_rec.py"))]
RATE = 48000


@pytest.fixture
def beat(tmp_path):
    (tmp_path / "in").mkdir()
    path = tmp_path / "in" / "beat.wav"
    sf.write(path, np.zeros((RATE // 10, 2), dtype=np.float32), RATE, subtype="FLOAT")
    return path


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(driver.record_run.time, "sleep", lambda seconds: None)


def recording(tmp_path, beat):
    return driver.Driver(FAKE, beat, tmp_path / "takes", tmp_path / "work")


def names(stages):
    return [stage.name for stage in stages]


def test_a_startup_walks_launching_level_check_ready_and_returns_the_facts(
    tmp_path, beat, no_sleep, monkeypatch
):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    stages, links = [], []
    rec = recording(tmp_path, beat)
    engine = rec.open(stages.append)
    try:
        ready = rec.start(engine, stages.append, links.append)
    finally:
        engine.close()
    assert names(stages) == ["launching", "level_check", "ready"]
    assert stages[1].values == {"seconds": 5.0}
    assert links == [ready.link]
    assert (ready.link.rate, ready.link.quantum) == (RATE, 64)
    assert ready.link.trip.source == "estimate" and ready.link.notices == ()
    assert "level check" in ready.monitor.source
    assert ready.monitor.dropped == ()
    assert ready.monitor.latency_ms == pytest.approx(1000 * (320 + 60) / RATE)


def test_a_graph_at_another_rate_adds_the_relaunching_stage_with_its_rate(
    tmp_path, beat, no_sleep, monkeypatch
):
    monkeypatch.setenv("FAKE_REC_RATE", "44100")
    sf.write(beat, np.zeros((4410, 2), dtype=np.float32), 44100, subtype="FLOAT")
    stages = []
    engine = recording(tmp_path, beat).open(stages.append)
    engine.close()
    assert stages == [driver.Stage("launching"), driver.Stage("relaunching", {"rate": 44100})]


def test_a_quantum_above_128_is_a_notice_with_values_not_a_sentence(
    tmp_path, beat, no_sleep, monkeypatch
):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    monkeypatch.setenv("FAKE_REC_QUANTUM", "256")
    rec = recording(tmp_path, beat)
    engine = rec.open()
    try:
        link = rec.link(engine)
    finally:
        engine.close()
    assert link.notices == (driver.Notice("quantum_high", {"quantum": 256, "limit": 128}),)
    assert "under 10 ms is not met" in terminal.render_notice(link.notices[0])


def test_the_analysis_of_a_take_is_the_stage_analysing_with_the_take(tmp_path, beat, monkeypatch):
    seen = []

    def numbers(engine, takes, beat_, refs, work, separator, settings, say, on_stage=None):
        on_stage("analysing", {"take": "take-01.wav"})
        return {}, "the analysis of take-01.wav"

    monkeypatch.setattr(driver.record_run, "monitor_numbers", numbers)
    monkeypatch.setattr(driver.live, "monitor_rack", lambda settings, numbers_: '{"effects": []}')
    rec = recording(tmp_path, beat)
    engine = rec.open()
    try:
        rec.start(engine, seen.append)
    finally:
        engine.close()
    assert names(seen) == ["analysing", "ready"]
    assert seen[0].values == {"take": "take-01.wav"}


def test_effects_switched_off_come_back_as_names(tmp_path, beat, no_sleep, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TAKE_S", "4")
    rec = recording(tmp_path, beat)
    monkeypatch.setattr(
        driver.live,
        "monitor_rack",
        lambda settings, numbers: json.dumps(
            {"effects": [{"effect": "eq"}, {"effect": "comp"}, {"effect": "verb"}]}
        ),
    )
    monkeypatch.setattr(driver, "budget_frames", lambda trip, rate: 5)
    engine = rec.open()
    try:
        ready = rec.start(engine)
    finally:
        engine.close()
    assert ready.monitor.dropped == ("verb", "comp", "eq")


def test_the_terminal_words_the_link_and_the_monitor_as_it_always_did():
    link = driver.Link(
        48000,
        256,
        RoundTrip(512, "estimate", "no calibration yet"),
        (driver.Notice("quantum_high", {"quantum": 256, "limit": 128}),),
    )
    lines = terminal.render_link(link)
    assert lines[0] == "JACK: 48000 Hz, quantum 256"
    assert lines[1].startswith("quantum 256 is above 128")
    assert lines[2:] == ["round trip 512 frames (JACK port estimate)", "no calibration yet"]
    assert terminal.render_monitor(driver.Monitor("a level check", ("a", "b"), 7.94)) == [
        "monitor numbers from a level check",
        "switched off to stay under 10 ms: a, b",
        "monitor latency 7.9 ms",
    ]
    assert terminal.render_monitor(driver.Monitor("x", (), None))[1:] == ["monitor latency unknown"]


def test_the_stages_the_terminal_never_showed_say_nothing():
    for name in ("launching", "relaunching", "ready", "failed"):
        assert terminal.render_stage(driver.Stage(name)) == []
    assert terminal.render_stage(driver.Stage("building"))[0].startswith("redline-rec is not built")


def test_a_missing_binary_is_the_stage_building_before_the_engine_is_looked_for(monkeypatch):
    stages = []

    def command(say):
        say("redline-rec is not built; building it")
        return ["x"]

    monkeypatch.setattr(driver.record_run, "engine_command", command)
    assert driver.resolve_command(stages.append) == ["x"]
    assert stages == [driver.Stage("building")]


def test_calibrating_through_speakers_carries_the_notice(tmp_path, no_sleep, monkeypatch):
    monkeypatch.setenv("FAKE_REC_TONE", "sweep:900")
    done = driver.calibrate(FAKE, tmp_path, "speakers")
    assert done.frames == 900 and done.mode == "speakers"
    assert done.notices == (driver.Notice("speakers"),)
    assert done.stored_in == tmp_path / "record" / "latency.json"
    assert terminal.render_calibrated(done)[-1] == terminal.SPEAKER_WARNING
    cup = driver.calibrate(FAKE, tmp_path, "cup")
    assert cup.notices == ()


class Quiet:
    """An engine that records nothing and counts its meters reads."""

    def __init__(self, quantum=64):
        self.quantum = quantum
        self.reads = 0
        self.recording = False
        self.dropouts = []

    def start(self, path, offset):
        path.write_bytes(b"")
        self.recording = True

    def stop(self):
        self.recording = False
        return TakeReport(Path(self.path), RATE, RATE, 0, self.dropouts)

    path = ""

    def close(self):
        pass

    def exited(self):
        return False

    def meters(self):
        self.reads += 1
        return Meters(0.0, 0.0, 0, 0, self.recording, 0, 0, self.quantum)


def test_a_session_reads_meters_from_the_provider_and_never_from_the_engine(tmp_path):
    engine = Quiet()
    provided = []

    def provider():
        provided.append(1)
        return Meters(0.0, 0.0, 0, 0, engine.recording, 0, 0, 64)

    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64, meters=provider)
    session.toggle()
    engine.path = str(tmp_path / "take-01.wav")
    session.toggle()
    assert engine.reads == 0 and len(provided) >= 2


def test_a_take_log_holds_the_beat_hash_the_session_was_given(tmp_path, beat):
    engine = Quiet()
    session = Session(engine, tmp_path / "t", RoundTrip(300, "calibrated"), 64, file_sha256(beat))
    session.toggle()
    engine.path = str(tmp_path / "t" / "take-01.wav")
    session.toggle()
    data = json.loads((tmp_path / "t" / "take-01.json").read_text(encoding="utf-8"))
    assert data["beat_sha256"] == file_sha256(beat) and len(data["beat_sha256"]) == 64


def test_a_failed_stop_log_holds_the_beat_hash_too(tmp_path):
    class Failing(Quiet):
        def stop(self):
            raise EngineError("did not finish")

    session = Session(Failing(), tmp_path, RoundTrip(300, "calibrated"), 64, "ef" * 32)
    session.toggle()
    with pytest.raises(EngineError):
        session.toggle()
    data = json.loads((tmp_path / "take-01.json").read_text(encoding="utf-8"))
    assert data["complete"] is False and data["beat_sha256"] == "ef" * 32


def test_the_logged_callback_comes_after_the_complete_log_is_on_disk(tmp_path):
    engine = Quiet()
    seen = []

    def logged(path):
        seen.append((path.name, path.with_suffix(".json").exists()))

    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64, on_logged=logged)
    session.toggle()
    assert seen == []
    engine.path = str(tmp_path / "take-01.wav")
    session.toggle()
    assert seen == [("take-01.wav", True)]


def test_a_discarded_take_is_never_announced_as_logged(tmp_path):
    seen = []
    session = Session(Quiet(), tmp_path, RoundTrip(300, "calibrated"), 64, on_logged=seen.append)
    session.toggle()
    session.discard_last()
    assert seen == []


@pytest.mark.parametrize(
    ("outcome", "sentence"),
    [
        (Outcome("recording", {"take": "take-03"}), "recording take-03"),
        (
            Outcome("kept", {"take": "take-01", "seconds": 4.04, "dropouts": 0}),
            "take-01 kept: 4.0 s",
        ),
        (
            Outcome("kept", {"take": "take-01", "seconds": 12.0, "dropouts": 2}),
            "take-01 kept: 12.0 s, 2 dropouts",
        ),
        (
            Outcome("kept_late", {"take": "take-02", "seconds": 1.0, "dropouts": 1}),
            "take-02 finished late, kept: 1.0 s, 1 dropouts",
        ),
        (
            Outcome("finished_late_discarded", {"take": "take-02"}),
            "take-02 finished late; it was discarded",
        ),
        (Outcome("discarded", {"take": "take-02"}), "take-02 discarded"),
        (
            Outcome("still_finishing", {"take": "take-02"}),
            "take-02 is still finishing: press space to collect it first",
        ),
        (Outcome("nothing_to_discard"), "nothing recorded in this session to discard"),
    ],
)
def test_the_terminal_words_each_outcome(outcome, sentence):
    assert terminal.render(outcome) == sentence


def test_an_outcome_nobody_worded_is_an_error_not_an_empty_line():
    with pytest.raises(ValueError, match="mystery"):
        terminal.render(Outcome("mystery"))


def test_a_refused_take_carries_both_quantums_as_values(tmp_path):
    session = Session(Quiet(256), tmp_path, RoundTrip(300, "calibrated"), 64)
    assert session.toggle() == Outcome("quantum_moved", {"was": 64, "now": 256})


def test_a_dropout_that_lost_nothing_does_not_count_in_the_kept_outcome(tmp_path):
    engine = Quiet()
    engine.dropouts = [Dropout(10, 0, ("xrun",)), Dropout(500, 40, ("xrun",))]
    session = Session(engine, tmp_path, RoundTrip(300, "calibrated"), 64)
    session.toggle()
    engine.path = str(tmp_path / "take-01.wav")
    assert session.toggle().values["dropouts"] == 1
