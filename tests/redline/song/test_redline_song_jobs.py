import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from redline.song.jobs import Busy, JobRunner

WRITE = (
    "import json, os, sys; p = sys.argv[1]; "
    "open(p + '.tmp', 'w').write(json.dumps({'ok': 1})); os.replace(p + '.tmp', p)"
)
SLEEP = "import sys, time; print('up', flush=True); time.sleep(60)"


def job(code, result, *extra):
    return [sys.executable, "-c", code, str(result), *extra]


def wait_for(runner, song, kind, states, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if runner.state(song).get(kind, {}).get("state") in states:
            return runner.state(song)[kind]
        time.sleep(0.02)
    raise AssertionError(f"{kind} did not reach {states}: {runner.state(song)}")


@pytest.fixture
def runner():
    made = JobRunner(tick=0.02, kill_after=0.3)
    yield made
    made.close(timeout=5)


def test_a_job_that_writes_its_result_is_done(runner, tmp_path):
    result = tmp_path / "sep.json"
    runner.start("s", "sep", job(WRITE, result), result)
    info = wait_for(runner, "s", "sep", ("done",))
    assert json.loads(result.read_text()) == {"ok": 1}
    assert info["result"] == str(result) and info["reason"] is None
    assert not (tmp_path / "sep.json.tmp").exists()


def test_a_job_runs_in_its_own_group_at_niceness_ten_with_a_private_log(runner, tmp_path):
    result = tmp_path / "r.json"
    code = "import os; print(os.getpgrp() == os.getpid(), os.nice(0))"
    runner.start("s", "sep", job(code, result), result)
    info = wait_for(runner, "s", "sep", ("failed",))
    log = (tmp_path / "r.json.log").read_text()
    # niceness adds to the parent's, which a run of the tests may already have raised
    assert log.split() == ["True", str(min(os.nice(0) + 10, 19))]
    assert (tmp_path / "r.json.log").stat().st_mode & 0o777 == 0o600
    assert info["log"] == str(tmp_path / "r.json.log")


def test_a_failed_job_says_why_with_the_last_lines_of_its_output(runner, tmp_path):
    result = tmp_path / "r.json"
    code = "import sys; [print('line', i, file=sys.stderr) for i in range(30)]; sys.exit(3)"
    runner.start("s", "sep", job(code, result), result)
    info = wait_for(runner, "s", "sep", ("failed",))
    assert "exit code 3" in info["reason"]
    assert "line 29" in info["reason"] and "line 5" not in info["reason"]
    assert info["result"] is None


def test_exit_zero_without_a_result_is_a_failure(runner, tmp_path):
    result = tmp_path / "r.json"
    runner.start("s", "sep", job("pass", result), result)
    info = wait_for(runner, "s", "sep", ("failed",))
    assert "no result was written" in info["reason"]


def test_a_crashed_child_leaves_no_result_file(runner, tmp_path):
    result = tmp_path / "r.json"
    code = (
        "import os, signal, sys; open(sys.argv[1] + '.tmp', 'w').write('{'); "
        "os.kill(os.getpid(), signal.SIGKILL)"
    )
    runner.start("s", "sep", job(code, result), result)
    info = wait_for(runner, "s", "sep", ("failed",))
    assert info["result"] is None
    assert list(tmp_path.glob("r.json*")) == [tmp_path / "r.json.log"]


def test_an_old_result_is_not_taken_for_a_new_runs(runner, tmp_path):
    result = tmp_path / "r.json"
    result.write_text("{}")
    runner.start("s", "sep", job("raise SystemExit(1)", result), result)
    wait_for(runner, "s", "sep", ("failed",))
    assert not result.exists()


def test_cancel_kills_the_group_and_the_slot_is_held_until_it_exits(runner, tmp_path):
    result = tmp_path / "r.json"
    stubborn = (
        "import signal, subprocess, sys, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(c.pid, flush=True); time.sleep(60)"
    )
    runner.start("s", "sep", job(stubborn, result), result)
    wait_for(runner, "s", "sep", ("running",))
    log = tmp_path / "r.json.log"
    while not log.read_text().strip():
        time.sleep(0.02)
    grandchild = int(log.read_text().split()[0])
    pid = runner.state("s")["sep"]["pid"]
    assert runner.cancel("s", "sep")["state"] == "cancelling"
    with pytest.raises(Busy):
        runner.start("s", "sep", job(WRITE, result), result)
    assert runner.state("s")["sep"]["state"] in ("cancelling", "cancelled")
    wait_for(runner, "s", "sep", ("cancelled",))
    for dead in (pid, grandchild):
        with pytest.raises(ProcessLookupError):
            for _ in range(100):
                os.kill(dead, 0)
                time.sleep(0.02)
    runner.start("s", "sep", job(WRITE, result), result)
    wait_for(runner, "s", "sep", ("done",))


def test_cancel_with_nothing_to_cancel_and_a_cancelled_queued_job(tmp_path):
    held = JobRunner(hold=lambda: True, tick=0.02)
    result = tmp_path / "r.json"
    assert held.cancel("s", "sep") is None
    held.start("s", "sep", job(WRITE, result), result, heavy=True)
    assert held.cancel("s", "sep")["state"] == "cancelled"
    held.start("s", "sep", job(WRITE, result), result, heavy=True)
    held.close(timeout=2)


def test_a_second_job_of_a_kind_for_a_song_is_busy_but_other_songs_and_kinds_run(runner, tmp_path):
    first = tmp_path / "a.json"
    runner.start("s", "sep", job(SLEEP, first), first)
    with pytest.raises(Busy):
        runner.start("s", "sep", job(WRITE, first), first)
    other_song, other_kind = tmp_path / "b.json", tmp_path / "c.json"
    runner.start("t", "sep", job(WRITE, other_song), other_song)
    runner.start("s", "auto", job(WRITE, other_kind), other_kind)
    wait_for(runner, "t", "sep", ("done",))
    wait_for(runner, "s", "auto", ("done",))
    assert runner.state("s")["sep"]["state"] == "running"


def test_a_finished_slot_can_be_started_again(runner, tmp_path):
    result = tmp_path / "r.json"
    runner.start("s", "sep", job("raise SystemExit(1)", result), result)
    wait_for(runner, "s", "sep", ("failed",))
    runner.start("s", "sep", job(WRITE, result), result)
    wait_for(runner, "s", "sep", ("done",))


def test_three_follow_up_requests_collapse_into_one_extra_run(runner, tmp_path):
    result = tmp_path / "rank.json"
    counter = tmp_path / "runs"
    code = (
        "import json, os, sys, time; "
        "open(sys.argv[2], 'a').write(sys.argv[3] + '\\n'); time.sleep(0.4); "
        "p = sys.argv[1]; open(p + '.tmp', 'w').write('{}'); os.replace(p + '.tmp', p)"
    )

    def argv(tag):
        return [sys.executable, "-c", code, str(result), str(counter), tag]

    runner.start("s", "rank", argv("first"), result, follow_up=True)
    wait_for(runner, "s", "rank", ("running",))
    for tag in ("b", "c", "d"):
        info = runner.start("s", "rank", argv(tag), result, follow_up=True)
        assert info["state"] == "running" and info["follow_up"]
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        info = runner.state("s")["rank"]
        if info["state"] == "done" and not info["follow_up"]:
            break
        time.sleep(0.05)
    time.sleep(0.3)
    assert runner.state("s")["rank"]["state"] == "done"
    assert counter.read_text().split() == ["first", "d"]


def test_without_a_follow_up_request_nothing_runs_twice(runner, tmp_path):
    result = tmp_path / "rank.json"
    counter = tmp_path / "runs"
    code = "import sys; open(sys.argv[2], 'a').write('x'); open(sys.argv[1], 'w').write('{}')"
    runner.start("s", "rank", [sys.executable, "-c", code, str(result), str(counter)], result)
    wait_for(runner, "s", "rank", ("done",))
    time.sleep(0.3)
    assert counter.read_text() == "x"


def test_cancel_drops_a_waiting_follow_up(runner, tmp_path):
    result = tmp_path / "rank.json"
    runner.start("s", "rank", job(SLEEP, result), result, follow_up=True)
    runner.start("s", "rank", job(WRITE, result), result, follow_up=True)
    assert runner.cancel("s", "rank")["follow_up"] is False
    wait_for(runner, "s", "rank", ("cancelled",))
    time.sleep(0.2)
    assert runner.state("s")["rank"]["state"] == "cancelled"


def test_hold_keeps_a_heavy_job_queued_until_it_is_released(tmp_path):
    recording = threading.Event()
    recording.set()
    runner = JobRunner(hold=recording.is_set, tick=0.02)
    heavy, light = tmp_path / "h.json", tmp_path / "l.json"
    try:
        info = runner.start("s", "sep", job(WRITE, heavy), heavy, heavy=True)
        assert info["state"] == "queued"
        runner.start("s", "result", job(WRITE, light), light)
        wait_for(runner, "s", "result", ("done",))
        time.sleep(0.2)
        assert runner.state("s")["sep"]["state"] == "queued" and not heavy.exists()
        recording.clear()
        wait_for(runner, "s", "sep", ("done",))
    finally:
        runner.close(timeout=5)


def test_a_queued_job_holds_its_slot(tmp_path):
    runner = JobRunner(hold=lambda: True, tick=0.02)
    result = tmp_path / "h.json"
    runner.start("s", "sep", job(WRITE, result), result, heavy=True)
    with pytest.raises(Busy):
        runner.start("s", "sep", job(WRITE, result), result, heavy=True)
    runner.close(timeout=2)


def test_a_command_that_cannot_start_fails_with_the_reason(runner, tmp_path):
    result = tmp_path / "r.json"
    info = runner.start("s", "sep", [str(tmp_path / "no-such-binary")], result)
    assert info["state"] == "failed" and "cannot start" in info["reason"]


def test_many_threads_starting_one_slot_get_one_run_and_the_rest_busy(runner, tmp_path):
    result = tmp_path / "r.json"
    outcomes = []

    def go():
        try:
            runner.start("s", "sep", job(SLEEP, result), result)
            outcomes.append("started")
        except Busy:
            outcomes.append("busy")

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["busy"] * 7 + ["started"]


FIFTY_SLEEPS = (
    "import sys, time; p = sys.argv[1]\n"
    "for _ in range(50): time.sleep(0.02)\n"
    "open(p + '.tmp', 'w').write('{}'); import os; os.replace(p + '.tmp', p)"
)


def proc_state(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    return text[text.rindex(")") + 2]


def stopped(pid, want=True, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if (proc_state(pid) == "T") is want:
            return True
        time.sleep(0.02)
    return False


def test_a_running_heavy_job_is_stopped_while_the_hold_is_on_and_continues_after(tmp_path):
    held = threading.Event()
    runner = JobRunner(hold=held.is_set, tick=0.02, kill_after=0.3)
    try:
        result = tmp_path / "r.json"
        runner.start("s", "sep", job(FIFTY_SLEEPS, result), result, heavy=True)
        info = wait_for(runner, "s", "sep", ("running",))
        assert info["paused"] is False
        pid = info["pid"]
        held.set()
        assert stopped(pid)
        assert runner.state("s")["sep"]["paused"] is True
        assert runner.state("s")["sep"]["state"] == "running"
        time.sleep(1.3)
        assert not result.exists()
        held.clear()
        assert stopped(pid, want=False)
        wait_for(runner, "s", "sep", ("done",))
        assert result.is_file() and runner.state("s")["sep"]["paused"] is False
    finally:
        runner.close(timeout=5)


def test_a_light_job_is_not_stopped_by_the_hold(tmp_path):
    runner = JobRunner(hold=lambda: True, tick=0.02, kill_after=0.3)
    try:
        result = tmp_path / "r.json"
        runner.start("s", "sep", job(FIFTY_SLEEPS, result), result, heavy=False)
        pid = wait_for(runner, "s", "sep", ("running",))["pid"]
        time.sleep(0.2)
        assert proc_state(pid) != "T"
        wait_for(runner, "s", "sep", ("done",))
    finally:
        runner.close(timeout=5)


def test_cancelling_a_paused_job_lets_it_exit(tmp_path):
    held = threading.Event()
    # long enough that only a woken job, not the SIGKILL, can end it within the wait below
    runner = JobRunner(hold=held.is_set, tick=0.02, kill_after=30)
    marker = tmp_path / "terminated"
    graceful = (
        "import pathlib, signal, sys, time\n"
        f"def done(*_): pathlib.Path({str(marker)!r}).write_text('x'); sys.exit(1)\n"
        "signal.signal(signal.SIGTERM, done)\n"
        "print('up', flush=True)\n"
        "time.sleep(60)\n"
    )
    try:
        result = tmp_path / "r.json"
        runner.start("s", "sep", job(graceful, result), result, heavy=True)
        pid = wait_for(runner, "s", "sep", ("running",))["pid"]
        end = time.monotonic() + 5
        while "up" not in (tmp_path / "r.json.log").read_text() and time.monotonic() < end:
            time.sleep(0.02)
        held.set()
        assert stopped(pid)
        runner.cancel("s", "sep")
        info = wait_for(runner, "s", "sep", ("cancelled",), timeout=3.0)
        assert info["paused"] is False
        assert marker.is_file()
        assert not Path(f"/proc/{pid}").exists()
    finally:
        runner.close(timeout=5)


def test_a_job_left_paused_by_a_dead_server_is_reaped_at_the_next_start(tmp_path):
    from redline.song.jobs import reap_orphans

    result = tmp_path / "auto.json"
    argv = [sys.executable, "-c", "import time; time.sleep(30)", "song", "job", "--result"]
    orphan = subprocess.Popen([*argv, str(result)], start_new_session=True)
    try:
        os.kill(orphan.pid, signal.SIGSTOP)
        (tmp_path / "auto.json.pid").write_text(f"{orphan.pid}\n")
        # a pid file naming a process that is not one of ours is dropped, the process kept
        (tmp_path / "rank.json.pid").write_text(f"{os.getpid()}\n")
        assert reap_orphans(tmp_path) == [orphan.pid]
        assert orphan.wait(timeout=5) == -signal.SIGKILL
        assert not list(tmp_path.glob("*.pid"))
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_a_running_job_has_a_pid_file_and_a_finished_one_none(runner, tmp_path):
    result = tmp_path / "out.json"
    runner.start("s", "rank", job("import time; time.sleep(0.5); " + WRITE, result), result)
    pid_file = tmp_path / "out.json.pid"
    wait_for(runner, "s", "rank", ("running",))
    assert pid_file.is_file() and pid_file.stat().st_mode & 0o777 == 0o600
    wait_for(runner, "s", "rank", ("done",))
    assert not pid_file.exists()
