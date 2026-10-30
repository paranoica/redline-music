import json
import sys
import threading
import time
from pathlib import Path

import pytest
from redline.record import driver
from redline.record.latency import RoundTrip
from redline.record.process import EngineError, EngineProcess
from redline.record.protocol import Dropout, EngineDesync, Meters, Startup, TakeReport
from redline.record.session import Session
from redline.song import rec
from redline.song.rec import (
    Busy,
    CommandTimeout,
    NotReady,
    OpenRequest,
    OutOfSync,
    RecClosed,
    RecOwner,
    TakeRunning,
)

RATE = 48000
HASH = "ab" * 32
TICK = 0.01
LATE = "the engine did not answer stop in 15 s"


def until(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.005)
    return False


class FakeEngine:
    """No process, no audio: records which thread called it and counts every call."""

    def __init__(self):
        self.startup = Startup(RATE, 64, None, ())
        self.guard = threading.Lock()
        self.threads = set()
        self.calls = []
        self.meters_calls = 0
        self.recording = False
        self.take = None
        self.peaks = [(0.0, 0.0)]
        self.stop_entered = threading.Event()
        self.stop_gate = None
        self.stop_errors = []
        self.closed = False
        self.dropouts = []

    def enter(self, name):
        assert self.guard.acquire(blocking=False), f"{name} overlapped another call"
        self.threads.add(threading.get_ident())
        self.calls.append(name)

    def leave(self):
        self.guard.release()

    def start(self, path, offset):
        self.enter("start")
        try:
            if self.recording:
                raise EngineError("a take is already being recorded")
            path.write_bytes(b"x")
            self.take, self.recording = path, True
            return None
        finally:
            self.leave()

    def stop(self):
        self.enter("stop")
        try:
            self.stop_entered.set()
            if self.stop_gate is not None:
                assert self.stop_gate.wait(10)
            if self.stop_errors:
                raise self.stop_errors.pop(0)
            if not self.recording:
                raise EngineError("no take is being recorded")
            self.recording = False
            return TakeReport(self.take, RATE, 2 * RATE, 300, self.dropouts)
        finally:
            self.leave()

    def meters(self):
        self.enter("meters")
        try:
            index = min(self.meters_calls, len(self.peaks) - 1)
            self.meters_calls += 1
            mic, out = self.peaks[index]
            frames = RATE // 2 if self.recording else 0
            return Meters(mic, out, 3, 1, self.recording, frames, 0, 64)
        finally:
            self.leave()

    def set_rack(self, rack_json, budget):
        self.enter("set_rack")
        try:
            self.budget = budget
            return 30, ["verb"]
        finally:
            self.leave()

    def exited(self):
        return False

    def close(self):
        self.enter("close")
        try:
            self.closed = True
        finally:
            self.leave()


class Fixture:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.engines = []
        self.logged = []
        self.stages = [driver.Stage("launching")]
        self.fail = None
        self.hold = None

    def opener(self, request, on_stage, meters, on_logged):
        if self.hold is not None:
            on_stage(driver.Stage("level_check", {"seconds": 5.0}))
            assert self.hold.wait(10)
        if self.fail:
            raise EngineError(self.fail)
        engine = FakeEngine()
        if self.engines:
            engine.peaks = self.engines[-1].peaks
        self.engines.append(engine)
        trip = RoundTrip(300, "calibrated")
        session = Session(engine, request.folder, trip, 64, HASH, meters, on_logged)
        link = driver.Link(RATE, 64, trip, ())
        return driver.Ready(engine, session, link, driver.Monitor("a level check", (), 6.2))

    def request(self):
        return OpenRequest(self.tmp / "beat.wav", self.tmp / "takes", self.tmp / "work")

    @property
    def engine(self):
        return self.engines[-1]


@pytest.fixture
def fix(tmp_path):
    return Fixture(tmp_path)


@pytest.fixture
def make(fix):
    made = []

    def build(**kwargs):
        kwargs.setdefault("tick_s", TICK)
        kwargs.setdefault("timeout_s", 5.0)
        owner = RecOwner(fix.opener, on_take_logged=fix.logged.append, **kwargs)
        made.append(owner)
        return owner

    yield build
    for owner in made:
        owner.shutdown(10)


@pytest.fixture
def ready(fix, make):
    owner = make()
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "ready")
    return owner


def test_commands_before_an_open_answer_not_ready(make):
    owner = make()
    for call in (owner.toggle, owner.discard_last, lambda: owner.set_rack("{}")):
        with pytest.raises(NotReady):
            call()
    assert owner.snapshot().stage == "idle"


def test_while_the_engine_opens_the_stage_shows_and_takes_are_refused(fix, make):
    fix.hold = threading.Event()
    owner = make()
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "level_check")
    with pytest.raises(NotReady, match="level_check"):
        owner.toggle()
    with pytest.raises(Busy):
        owner.open(fix.request())
    fix.hold.set()
    assert until(lambda: owner.snapshot().stage == "ready")
    assert owner.toggle().code == "recording"


def test_an_engine_that_will_not_start_ends_in_failed_with_the_reason(fix, make):
    fix.fail = "cannot connect to JACK"
    owner = make()
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "failed")
    assert owner.snapshot().error == "cannot connect to JACK"
    with pytest.raises(NotReady):
        owner.toggle()
    fix.fail = None
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "ready")
    assert owner.snapshot().error is None


def test_one_meters_request_per_tick_whatever_the_snapshot_pollers(ready, fix):
    engine = fix.engine
    start_calls, start = engine.meters_calls, time.monotonic()
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            ready.snapshot()

    pollers = [threading.Thread(target=poll) for _ in range(3)]
    for thread in pollers:
        thread.start()
    time.sleep(0.5)
    stop.set()
    for thread in pollers:
        thread.join()
    made = engine.meters_calls - start_calls
    ticks = (time.monotonic() - start) / TICK
    assert 5 <= made <= ticks + 2


def test_every_engine_call_comes_from_the_one_owner_thread(ready, fix):
    ready.toggle()
    ready.toggle()
    ready.set_rack("{}")
    ready.snapshot()
    assert len(fix.engine.threads) == 1
    assert threading.get_ident() not in fix.engine.threads


def test_start_stop_from_two_handler_threads_never_cross_replies(ready, fix):
    results = {"a": [], "b": []}

    def press(name):
        for _ in range(10):
            results[name].append(ready.toggle())

    threads = [threading.Thread(target=press, args=(n,)) for n in results]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    codes = [o.code for o in results["a"] + results["b"]]
    assert codes.count("recording") == 10 and codes.count("kept") == 10
    engine_calls = [c for c in fix.engine.calls if c in ("start", "stop")]
    assert engine_calls == ["start", "stop"] * 10
    assert all(len(outcomes) == 10 for outcomes in results.values())
    kept = [o.values["take"] for o in results["a"] + results["b"] if o.code == "kept"]
    started = [o.values["take"] for o in results["a"] + results["b"] if o.code == "recording"]
    assert sorted(kept) == sorted(started)


def test_a_late_reply_marks_the_engine_out_of_sync_and_refuses_until_a_relaunch(ready, fix):
    ready.toggle()
    fix.engine.stop_errors.append(EngineDesync(LATE))
    with pytest.raises(EngineError, match="did not answer"):
        ready.toggle()
    assert ready.snapshot().out_of_sync
    calls = len(fix.engine.calls)
    for call in (ready.toggle, ready.discard_last, lambda: ready.set_rack("{}")):
        with pytest.raises(OutOfSync):
            call()
    time.sleep(0.1)
    assert fix.engine.calls[calls:].count("meters") == 0
    ready.open(fix.request())
    assert until(lambda: ready.snapshot().stage == "ready" and not ready.snapshot().out_of_sync)
    assert ready.toggle().code == "recording"


def test_a_plain_refusal_from_the_engine_does_not_put_it_out_of_sync(ready, fix):
    ready.toggle()
    fix.engine.stop_errors.append(EngineError("the take did not finish within 10 s: audio?"))
    with pytest.raises(EngineError, match="did not finish"):
        ready.toggle()
    assert not ready.snapshot().out_of_sync


def test_the_wording_the_real_process_uses_for_a_missing_reply_is_what_marks_it(tmp_path):
    script = tmp_path / "silent.py"
    line = json.dumps(
        {
            "event": "started",
            "rate": RATE,
            "quantum": 64,
            "capture_latency": [64, 64],
            "playback_latency": [64, 64],
            "round_trip": 128,
            "round_trip_source": "port_latency_estimate",
        }
    )
    script.write_text(f"import sys, time\nprint({line!r}, flush=True)\ntime.sleep(5)\n")
    engine = EngineProcess.launch([sys.executable, str(script)])
    try:
        with pytest.raises(EngineError) as raised:
            engine.request({"cmd": "meters"}, "meters", timeout=0.2)
    finally:
        engine._process.kill()
        engine.close()
    assert rec.is_desync(raised.value)
    assert not rec.is_desync(EngineError("a take is already being recorded"))
    assert not rec.is_desync(EngineError("the engine answered nothing useful"))


def test_a_command_that_timed_out_while_queued_never_runs_later(ready, fix):
    fix.engine.stop_gate = threading.Event()
    ready.toggle()
    first = threading.Thread(target=ready.toggle)
    first.start()
    assert fix.engine.stop_entered.wait(5)
    with pytest.raises(CommandTimeout):
        ready.toggle(timeout=0.1)
    fix.engine.stop_gate.set()
    first.join(5)
    time.sleep(0.1)
    assert fix.engine.calls.count("start") == 1
    assert fix.engine.calls.count("stop") == 1


def test_while_a_stop_is_pending_the_snapshot_says_stopping_and_ages(ready, fix):
    fix.engine.stop_gate = threading.Event()
    ready.toggle()
    stopper = threading.Thread(target=ready.toggle)
    stopper.start()
    assert fix.engine.stop_entered.wait(5)
    assert until(lambda: ready.snapshot().stopping)
    first = ready.snapshot().age_s
    time.sleep(0.2)
    assert ready.snapshot().age_s > first + 0.1
    fix.engine.stop_gate.set()
    stopper.join(5)
    assert until(lambda: not ready.snapshot().stopping)
    assert ready.snapshot().age_s < 0.5


def test_peaks_between_snapshot_reads_are_not_lost(fix, make):
    owner = make()
    fix.engines.append(FakeEngine())
    fix.engines[0].peaks = [(0.0, 0.0), (0.2, 0.1), (0.9, 0.4), (0.1, 0.05)]
    owner.open(fix.request())
    assert until(lambda: fix.engine.meters_calls >= 6)
    first = owner.snapshot()
    assert first.mic_peak == 0.9 and first.out_peak == 0.4
    assert until(lambda: fix.engine.meters_calls >= 8)
    assert owner.snapshot().mic_peak == 0.1


def test_the_snapshot_carries_counters_and_the_take_length(ready, fix):
    ready.toggle()
    assert until(lambda: ready.snapshot().recording)
    snap = ready.snapshot()
    assert (snap.xruns, snap.overflows, snap.take) == (3, 1, "take-01")
    assert snap.take_seconds == pytest.approx(0.5)
    assert (snap.rate, snap.quantum, snap.monitor_ms) == (RATE, 64, 6.2)


def test_dropouts_and_kept_takes_are_counted_from_the_session(ready, fix):
    fix.engine.dropouts = [Dropout(10, 20, ("xrun",))]
    ready.toggle()
    ready.toggle()
    ready.toggle()
    ready.toggle()
    assert ready.snapshot().kept == 2 and ready.snapshot().dropouts == 2
    ready.discard_last()
    assert ready.snapshot().kept == 1


def test_set_rack_gives_the_budget_and_what_was_switched_off(ready, fix):
    assert ready.set_rack("{}") == (30, ("verb",))
    assert fix.engine.budget == 480 - 300
    assert ready.snapshot().dropped == ("verb",)


def sidecar(fix, name="take-01.json"):
    return json.loads((fix.tmp / "takes" / name).read_text(encoding="utf-8"))


def test_shutdown_during_a_running_take_keeps_it_with_a_complete_log(ready, fix):
    ready.toggle()
    ready.shutdown(10)
    data = sidecar(fix)
    assert data["complete"] is True and data["beat_sha256"] == HASH
    assert (fix.tmp / "takes" / "take-01.wav").exists()
    assert fix.logged == [fix.engine.take]
    assert fix.engine.closed
    assert not ready._thread.is_alive()
    assert ready.snapshot().stage == "closed"


def test_shutdown_while_a_stop_is_pending_waits_for_it_and_logs_the_take_once(ready, fix):
    fix.engine.stop_gate = threading.Event()
    ready.toggle()
    stopper = threading.Thread(target=ready.toggle)
    stopper.start()
    assert fix.engine.stop_entered.wait(5)
    closer = threading.Thread(target=ready.shutdown, args=(10,))
    closer.start()
    time.sleep(0.1)
    assert closer.is_alive()
    with pytest.raises(RecClosed):
        ready.toggle()
    fix.engine.stop_gate.set()
    stopper.join(5)
    closer.join(5)
    assert not closer.is_alive()
    assert sidecar(fix)["complete"] is True
    assert len(fix.logged) == 1 and fix.engine.closed


def test_shutdown_after_a_stop_timed_out_collects_the_take(ready, fix):
    ready.toggle()
    fix.engine.stop_errors.append(EngineError("the take did not finish within 10 s: audio?"))
    with pytest.raises(EngineError):
        ready.toggle()
    assert sidecar(fix)["complete"] is False
    ready.shutdown(10)
    data = sidecar(fix)
    assert data["complete"] is True and data["beat_sha256"] == HASH
    assert len(fix.logged) == 1


def test_commands_after_a_shutdown_are_refused_and_a_second_shutdown_is_harmless(ready):
    ready.shutdown(10)
    ready.shutdown(10)
    for call in (ready.toggle, ready.close):
        with pytest.raises(RecClosed):
            call()


def test_a_beat_change_while_a_take_runs_is_refused_and_the_take_goes_on(ready, fix):
    ready.toggle()
    with pytest.raises(TakeRunning):
        ready.open(fix.request())
    assert len(fix.engines) == 1 and ready.snapshot().stage == "ready"
    assert ready.toggle().code == "kept"
    ready.open(fix.request())
    assert until(lambda: len(fix.engines) == 2 and ready.snapshot().stage == "ready")
    assert fix.engines[0].closed


def test_only_a_take_kept_is_announced_as_logged_not_a_discarded_one(ready, fix):
    ready.toggle()
    ready.discard_last()
    assert fix.logged == []
    ready.toggle()
    ready.toggle()
    assert [p.name for p in fix.logged] == ["take-01.wav"]


def test_a_failing_callback_does_not_lose_the_take(fix, make):
    owner = RecOwner(fix.opener, on_take_logged=lambda path: 1 / 0, tick_s=TICK)
    try:
        owner.open(fix.request())
        assert until(lambda: owner.snapshot().stage == "ready")
        owner.toggle()
        assert owner.toggle().code == "kept"
        assert sidecar(fix)["complete"] is True
    finally:
        owner.shutdown(10)


class Calibration:
    def __init__(self, fix):
        self.fix = fix
        self.seen = []
        self.error = None

    def __call__(self, mode, on_stage):
        self.seen.append((mode, self.fix.engines[-1].closed, self.owner.snapshot().stage))
        if self.error:
            raise self.error
        return "done"


def test_calibration_closes_the_session_runs_and_relaunches(fix, make):
    calibration = Calibration(fix)
    owner = make(calibrator=calibration)
    calibration.owner = owner
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "ready")
    assert owner.calibrate("cup") == "done"
    assert calibration.seen == [("cup", True, "calibrating")]
    assert until(lambda: len(fix.engines) == 2 and owner.snapshot().stage == "ready")


def test_a_failed_calibration_reaches_the_caller_and_the_engine_still_comes_back(fix, make):
    calibration = Calibration(fix)
    calibration.error = EngineError("silent")
    owner = make(calibrator=calibration)
    calibration.owner = owner
    owner.open(fix.request())
    assert until(lambda: owner.snapshot().stage == "ready")
    with pytest.raises(EngineError, match="silent"):
        owner.calibrate("cup")
    assert until(lambda: len(fix.engines) == 2 and owner.snapshot().stage == "ready")


def test_calibration_is_refused_while_a_take_runs(ready, fix):
    ready.toggle()
    with pytest.raises(TakeRunning):
        ready.calibrate("cup")
    assert not fix.engine.closed


def test_close_leaves_the_owner_idle_and_open_works_again(ready, fix):
    ready.toggle()
    ready.close()
    assert ready.snapshot().stage == "closed" and sidecar(fix)["complete"] is True
    with pytest.raises(NotReady):
        ready.toggle()
    ready.open(fix.request())
    assert until(lambda: ready.snapshot().stage == "ready")
    assert Path(fix.tmp / "takes" / "take-01.wav").exists()
