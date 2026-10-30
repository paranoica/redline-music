"""The native engine as a child process: found or built, started, and talked to by line."""

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import redline
from redline.record.protocol import (
    EngineDesync,
    EngineError,
    Meters,
    Startup,
    TakeReport,
    late_take,
    meters,
    parse_startup,
    take_report,
)

BINARY = "redline-rec"
DSP_ROOT = Path(redline.__file__).resolve().parent.parent / "redline-dsp"
BUILD = ["cargo", "build", "--release", "-p", BINARY, "--features", "jack"]
READY_TIMEOUT_S = 10.0
REPLY_TIMEOUT_S = 5.0
# the engine itself waits up to 10 s for the writer to close a take
STOP_TIMEOUT_S = 15.0
STDERR_LINES = 20


def find_binary(dsp_root: Path = DSP_ROOT) -> Path | None:
    """The built engine, release before debug; None when neither is there."""
    for profile in ("release", "debug"):
        path = dsp_root / "target" / profile / BINARY
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def build_binary(dsp_root: Path = DSP_ROOT) -> Path:
    """Build the engine with its JACK backend and return it.

    Raises:
        EngineError: cargo is missing or the build failed.
    """
    if shutil.which("cargo") is None:
        raise EngineError(
            f"{BINARY} is not built and cargo is not installed to build it: install Rust, or "
            f"run `{' '.join(BUILD)}` in {dsp_root}"
        )
    if not dsp_root.is_dir():
        raise EngineError(f"{BINARY} is not built and its sources are not at {dsp_root}")
    done = subprocess.run(BUILD, cwd=dsp_root, capture_output=True, text=True, check=False)
    built = find_binary(dsp_root)
    if done.returncode != 0 or built is None:
        tail = "\n".join(done.stderr.strip().splitlines()[-STDERR_LINES:])
        raise EngineError(f"building {BINARY} failed (the jack feature needs libjack):\n{tail}")
    return built


def engine_argv(binary: Sequence[str], beat: Path, quantum: int) -> list[str]:
    """The engine's command line: the beat, already at the graph rate, and the quantum asked.

    The engine asks PipeWire for the quantum itself, as a latency rather than a forced
    quantum, so the environment is passed through unchanged.
    """
    return [*binary, str(beat), "--quantum", str(quantum)]


def _pump(stream: TextIO, sink: Any) -> None:
    for line in stream:
        sink(line)
    sink(None)


class EngineProcess:
    """A running engine: one command a line in, one reply a line out."""

    def __init__(self, process: subprocess.Popen[str], startup: Startup) -> None:
        """Wrap a started process; use `launch`."""
        self._process = process
        self.startup = startup
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=STDERR_LINES)
        self._readers: list[threading.Thread] = []

    @classmethod
    def launch(
        cls,
        argv: Sequence[str],
        env: dict[str, str] | None = None,
        timeout: float = READY_TIMEOUT_S,
    ) -> "EngineProcess":
        """Start the engine and read its startup line.

        Raises:
            WrongRate: The beat is not at the graph's rate, which the error carries.
            EngineError: The engine cannot be run, stopped before it was ready (no JACK, no
                mic), or said nothing usable in time.
        """
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except OSError as error:
            raise EngineError(f"cannot run {argv[0]}: {error}") from error
        engine = cls(process, Startup(1, 1, None, ()))
        assert process.stdout is not None and process.stderr is not None
        out = threading.Thread(target=_pump, args=(process.stdout, engine._lines.put), daemon=True)
        err = threading.Thread(target=_pump, args=(process.stderr, engine._keep_err), daemon=True)
        engine._readers = [out, err]
        out.start()
        err.start()
        try:
            line = engine._lines.get(timeout=timeout)
        except queue.Empty:
            engine.close()
            raise EngineError(f"the engine said nothing in {timeout:g} s") from None
        if line is None:
            process.wait(timeout=REPLY_TIMEOUT_S)
            err.join(timeout=REPLY_TIMEOUT_S)
            raise EngineError(engine._died(f"the engine stopped (exit {process.returncode})"))
        try:
            engine.startup = parse_startup(line)
        except EngineError:
            engine.close()
            raise
        return engine

    def _keep_err(self, line: str | None) -> None:
        if line is not None and line.strip():
            self._stderr.append(line.rstrip())

    def _died(self, what: str) -> str:
        said = "\n".join(self._stderr)
        return f"{what}: {said}" if said else what

    def request(
        self, command: dict[str, Any], answer: str, timeout: float = REPLY_TIMEOUT_S
    ) -> dict[str, Any]:
        """Send a command and return its reply.

        Raises:
            EngineError: The engine refused it, replied to something else, stopped, or did
                not answer in time.
        """
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.write(json.dumps(command) + "\n")
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            raise EngineError(self._died("the engine has stopped")) from None
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise EngineDesync(
                f"the engine did not answer {command['cmd']} in {timeout:g} s"
            ) from None
        if line is None:
            raise EngineError(self._died("the engine has stopped"))
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            raise EngineDesync(f"the engine said something unreadable: {line[:200]}") from None
        if reply.get("reply") == "error":
            raise EngineError(str(reply.get("error")))
        if reply.get("reply") != answer:
            raise EngineDesync(f"the engine answered {reply.get('reply')!r} to {command['cmd']}")
        return reply

    def start(self, path: Path, offset: int) -> TakeReport | str | None:
        """Record a new take into path, dropping offset frames, the round trip.

        Returns:
            The take of a stop that timed out, or its writer error, when it came since.
        """
        reply = self.request({"cmd": "start", "path": str(path), "offset": offset}, "start")
        try:
            return late_take(reply)
        except (KeyError, TypeError, ValueError) as error:
            raise EngineError(f"the engine's late take is unreadable: {error}") from error

    def stop(self) -> TakeReport:
        """Finish the take and return the engine's report on it."""
        reply = self.request({"cmd": "stop"}, "stop", STOP_TIMEOUT_S)
        try:
            return take_report(reply["take"])
        except (KeyError, TypeError, ValueError) as error:
            raise EngineError(f"the engine's take report is unreadable: {error}") from error

    def set_rack(self, rack_json: str, budget: int | None) -> tuple[int, list[str]]:
        """Monitor through a new rack; its latency and the effects switched off to fit budget."""
        reply = self.request(
            {"cmd": "set_rack", "rack": json.loads(rack_json), "budget": budget}, "set_rack"
        )
        return int(reply["latency"]), [str(name) for name in reply["dropped"]]

    def meters(self) -> Meters:
        """Peaks since the last call, counters, the take's length so far."""
        reply = self.request({"cmd": "meters"}, "meters")
        try:
            return meters(reply)
        except (KeyError, TypeError, ValueError) as error:
            raise EngineError(f"the engine's meters are unreadable: {error}") from error

    def exited(self) -> bool:
        """Whether the process has ended."""
        return self._process.poll() is not None

    def close(self) -> None:
        """Quit the engine, killing it if it does not end in time."""
        if not self.exited():
            with contextlib.suppress(EngineError):
                self.request({"cmd": "quit"}, "quit", STOP_TIMEOUT_S)
        try:
            self._process.wait(timeout=REPLY_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        for reader in self._readers:
            reader.join(timeout=REPLY_TIMEOUT_S)
        for stream in (self._process.stdin, self._process.stdout, self._process.stderr):
            if stream is not None:
                stream.close()
