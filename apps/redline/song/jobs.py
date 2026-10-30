"""Jobs as subprocesses: one slot per song and kind, cancel by process group, results by rename."""

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

QUEUED = "queued"
RUNNING = "running"
CANCELLING = "cancelling"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
ACTIVE = (QUEUED, RUNNING, CANCELLING)
NICENESS = 10
KILL_AFTER_S = 5.0
TICK_S = 0.1
TAIL_LINES = 8


class Busy(Exception):
    """The song already has a job of this kind that has not finished."""


@dataclass
class _Request:
    argv: list[str]
    result: Path
    heavy: bool


@dataclass
class _Job:
    request: _Request
    state: str = QUEUED
    reason: str | None = None
    process: subprocess.Popen[bytes] | None = None
    follow_up: _Request | None = None
    started: float | None = None
    finished: float | None = None
    thread: threading.Thread | None = field(default=None, repr=False)

    def info(self) -> dict[str, Any]:
        """The job as plain data for a poll."""
        return {
            "state": self.state,
            "reason": self.reason,
            "result": str(self.request.result) if self.state == DONE else None,
            "log": str(_log_of(self.request.result)),
            "follow_up": self.follow_up is not None,
            "pid": self.process.pid if self.process and self.state in ACTIVE else None,
            "started": self.started,
            "finished": self.finished,
        }


def _log_of(result: Path) -> Path:
    return result.with_name(result.name + ".log")


def _tmp_of(result: Path) -> Path:
    return result.with_name(result.name + ".tmp")


def _renice() -> None:
    os.nice(NICENESS)


def _tail(log: Path) -> str:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-TAIL_LINES:])


class JobRunner:
    """Runs subprocess jobs; every method is safe to call from any thread."""

    def __init__(
        self,
        hold: Callable[[], bool] | None = None,
        kill_after: float = KILL_AFTER_S,
        tick: float = TICK_S,
    ) -> None:
        """Set up the runner.

        Args:
            hold: Heavy jobs stay queued while this returns True (a take is recording).
            kill_after: Seconds between SIGTERM and SIGKILL on a cancel.
            tick: Seconds between looks at the hold while a job is queued.
        """
        self._hold = hold or (lambda: False)
        self._kill_after = kill_after
        self._tick = tick
        self._lock = threading.RLock()
        self._jobs: dict[tuple[str, str], _Job] = {}
        self._watcher: threading.Thread | None = None
        self._closed = False

    def start(
        self,
        song: str,
        kind: str,
        argv: Sequence[str],
        result: Path,
        heavy: bool = False,
        follow_up: bool = False,
    ) -> dict[str, Any]:
        """Start a job, or queue it behind the hold.

        Args:
            song: The song's name.
            kind: The kind of job; a song has at most one unfinished job of a kind.
            argv: The command; the job writes its JSON to `<result>.tmp` and renames it.
            result: Where the finished job's JSON is.
            heavy: Wait while the hold is on.
            follow_up: When one is unfinished, keep this request as the one run after it
                instead of raising; later requests replace it.

        Returns:
            The job's state as data.

        Raises:
            Busy: The slot is taken and follow_up is off.
        """
        request = _Request(list(argv), result, heavy)
        with self._lock:
            if self._closed:
                raise Busy("the runner is closed")
            current = self._jobs.get((song, kind))
            if current is not None and current.state in ACTIVE:
                if not follow_up:
                    raise Busy(f"{kind} is already {current.state} for {song}")
                current.follow_up = request
                return current.info()
            job = _Job(request)
            self._jobs[(song, kind)] = job
            self._promote()
            return job.info()

    def cancel(self, song: str, kind: str) -> dict[str, Any] | None:
        """Stop a job: a queued one is dropped, a running one gets SIGTERM, then SIGKILL.

        The slot stays taken until the process has exited. A waiting follow-up is dropped.

        Returns:
            The job's state, or None when the song has no such job.
        """
        with self._lock:
            job = self._jobs.get((song, kind))
            if job is None:
                return None
            job.follow_up = None
            if job.state == QUEUED:
                job.state, job.finished = CANCELLED, time.time()
            elif job.state == RUNNING:
                job.state = CANCELLING
                self._signal(job, signal.SIGTERM)
                threading.Thread(target=self._reap, args=(job,), daemon=True).start()
            return job.info()

    def state(self, song: str) -> dict[str, dict[str, Any]]:
        """A snapshot of the song's jobs by kind."""
        with self._lock:
            return {k: j.info() for (s, k), j in self._jobs.items() if s == song}

    def active(self, kind: str | None = None) -> bool:
        """Any job, or any job of the kind, queued, running or cancelling."""
        with self._lock:
            return any(j.state in ACTIVE for (_, k), j in self._jobs.items() if kind in (None, k))

    def close(self, timeout: float = 15.0) -> None:
        """Refuse new jobs, cancel the rest and wait for the processes to exit."""
        with self._lock:
            self._closed = True
            keys = list(self._jobs)
        for song, kind in keys:
            self.cancel(song, kind)
        deadline = time.monotonic() + timeout
        with self._lock:
            threads = [j.thread for j in self._jobs.values() if j.thread]
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    def _signal(self, job: _Job, sig: int) -> None:
        process = job.process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, sig)

    def _reap(self, job: _Job) -> None:
        process = job.process
        if process is None:
            return
        try:
            process.wait(self._kill_after)
        except subprocess.TimeoutExpired:
            with self._lock:
                self._signal(job, signal.SIGKILL)

    def _promote(self) -> None:
        """Launch every queued job the hold lets go; keep a watcher while some wait."""
        waiting = False
        for job in self._jobs.values():
            if job.state != QUEUED:
                continue
            if job.request.heavy and self._hold():
                waiting = True
            else:
                self._launch(job)
        if waiting and self._watcher is None and not self._closed:
            self._watcher = threading.Thread(target=self._watch, daemon=True)
            self._watcher.start()

    def _watch(self) -> None:
        while True:
            time.sleep(self._tick)
            with self._lock:
                self._promote()
                if not any(j.state == QUEUED for j in self._jobs.values()):
                    self._watcher = None
                    return

    def _launch(self, job: _Job) -> None:
        result = job.request.result
        try:
            result.parent.mkdir(parents=True, exist_ok=True)
            result.unlink(missing_ok=True)
            _tmp_of(result).unlink(missing_ok=True)
            fd = os.open(_log_of(result), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                job.process = subprocess.Popen(
                    job.request.argv,
                    stdin=subprocess.DEVNULL,
                    stdout=fd,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    preexec_fn=_renice,
                )
            finally:
                os.close(fd)
        except (OSError, ValueError) as error:
            job.state, job.reason, job.finished = FAILED, f"cannot start: {error}", time.time()
            return
        job.state, job.started = RUNNING, time.time()
        job.thread = threading.Thread(target=self._wait, args=(job,), daemon=True)
        job.thread.start()

    def _wait(self, job: _Job) -> None:
        process = job.process
        assert process is not None
        code = process.wait()
        result = job.request.result
        with self._lock:
            cancelled = job.state == CANCELLING
            if cancelled:
                job.state = CANCELLED
            elif code == 0 and result.is_file():
                job.state = DONE
            else:
                tail = _tail(_log_of(result))
                why = f"exit code {code}" if code != 0 else "no result was written"
                job.state, job.reason = FAILED, f"{why}\n{tail}" if tail else why
            if job.state != DONE:
                result.unlink(missing_ok=True)
                _tmp_of(result).unlink(missing_ok=True)
            job.finished = time.time()
            if job.follow_up is not None and not self._closed:
                request, job.follow_up = job.follow_up, None
                again = _Job(request)
                key = next(k for k, j in self._jobs.items() if j is job)
                self._jobs[key] = again
                self._promote()
