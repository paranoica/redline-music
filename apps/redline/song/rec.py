"""The one thread that owns the recording engine, for the song page.

EngineProcess talks one line out, one line back, and its meters are destructive (peaks since
the last call). So exactly one thread, started here, touches the engine and the Session; HTTP
handler threads queue a command and wait for its result. A 50 ms tick inside that thread is
the only caller of `meters`, and keeps the latest of them for `snapshot()`, which never
touches the engine.
"""

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.engine.settings import Settings
from redline.record import driver
from redline.record.driver import Calibrated, OnStage, Ready, Stage
from redline.record.protocol import EngineDesync, EngineError, Meters
from redline.record.session import Outcome, Session, budget_frames, monitor_ms

if TYPE_CHECKING:
    from redline.measure.separate import Separator

LOG = logging.getLogger(__name__)

TICK_S = 0.05
COMMAND_TIMEOUT_S = 30.0
CALIBRATE_TIMEOUT_S = 120.0
SHUTDOWN_TIMEOUT_S = 60.0
OPENING = ("building", "launching", "relaunching", "analysing", "level_check")


# what EngineProcess.request says when a reply did not come, or came for something else
class RecError(Exception):
    """A command was not carried out."""


class NotReady(RecError):
    """The engine is not ready for takes; the message says where it is."""


class OutOfSync(NotReady):
    """A reply came late or for something else: commands wait for a relaunch."""


class Busy(RecError):
    """An open or a calibration is already running."""


class TakeRunning(RecError):
    """A take is running (or still finishing); the command would end it."""


class CommandTimeout(RecError):
    """The owner thread did not finish the command in time."""


class RecClosed(RecError):
    """The owner is shutting down or has shut down."""


@dataclass(frozen=True)
class OpenRequest:
    """What to record over and where to put it."""

    beat: Path
    folder: Path
    work: Path
    refs: Sequence[Path] = ()
    separator: "Separator | None" = None
    settings: Settings | None = None


@dataclass(frozen=True)
class RecSnapshot:
    """The recording state at the last tick.

    stage is idle, building, launching, relaunching, analysing, level_check, ready, failed,
    calibrating or closed. Peaks are the largest since the previous `snapshot()` call; age_s
    is how long ago the engine was last read (it grows while the thread is busy with a stop).
    dropouts counts the dropouts of the takes kept since the engine opened.
    """

    stage: str
    error: str | None
    out_of_sync: bool
    stopping: bool
    recording: bool
    take: str | None
    take_seconds: float
    mic_peak: float
    out_peak: float
    xruns: int
    overflows: int
    dropouts: int
    kept: int
    rate: int | None
    quantum: int | None
    monitor_ms: float | None
    dropped: tuple[str, ...]
    age_s: float
    round_trip: tuple[str, str] | None = None
    notices: tuple[tuple[str, dict[str, Any]], ...] = ()


Opener = Callable[[OpenRequest, OnStage, Callable[[], Meters], Callable[[Path], None]], Ready]
Calibrator = Callable[[str, OnStage], Calibrated]


def open_with_driver(
    request: OpenRequest,
    on_stage: OnStage,
    meters: Callable[[], Meters],
    on_logged: Callable[[Path], None],
) -> Ready:
    """The default opener: find or build the engine, start it, set the monitor, open a session."""
    command = driver.resolve_command(on_stage)
    recording = driver.Driver(
        command,
        request.beat,
        request.folder,
        request.work,
        request.refs,
        request.separator,
        request.settings,
    )
    engine = recording.open(on_stage)
    try:
        return recording.start(engine, on_stage, meters=meters, on_logged=on_logged)
    except BaseException:
        with contextlib.suppress(EngineError):
            engine.close()
        raise


def is_desync(error: EngineError) -> bool:
    """Whether the engine's channel may now hold a reply that belongs to an earlier command."""
    return isinstance(error, EngineDesync)


class _Command:
    def __init__(self, name: str, run: Callable[["_Command"], Any], needs_ready: bool) -> None:
        self.name = name
        self.run = run
        self.needs_ready = needs_ready
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.state = "pending"
        self.value: Any = None
        self.error: BaseException | None = None

    def reply(self, value: Any = None) -> None:
        if not self.done.is_set():
            self.value = value
            self.done.set()

    def fail(self, error: BaseException) -> None:
        if not self.done.is_set():
            self.error = error
            self.done.set()

    def wait(self, timeout: float) -> Any:
        if not self.done.wait(timeout):
            with self.lock:
                if self.state == "pending":
                    self.state = "abandoned"
            raise CommandTimeout(f"{self.name}: no answer in {timeout:g} s")
        if self.error is not None:
            raise self.error
        return self.value


_STOP = object()


@dataclass
class _State:
    stage: str = "idle"
    error: str | None = None
    out_of_sync: bool = False
    stopping: bool = False
    closing: bool = False
    opening: bool = False
    take: str | None = None
    kept: int = 0
    dropouts: int = 0
    rate: int | None = None
    quantum: int | None = None
    monitor_ms: float | None = None
    dropped: tuple[str, ...] = ()
    round_trip: tuple[str, str] | None = None
    notices: tuple[tuple[str, dict[str, Any]], ...] = ()
    latest: Meters | None = None
    peaks: tuple[float, float] | None = None
    last_peaks: tuple[float, float] = (0.0, 0.0)
    at: float = field(default_factory=time.monotonic)


class RecOwner:
    """The recording engine and its session, owned by one thread."""

    def __init__(
        self,
        opener: Opener = open_with_driver,
        calibrator: Calibrator | None = None,
        on_take_logged: Callable[[Path], None] | None = None,
        work: Path | None = None,
        tick_s: float = TICK_S,
        timeout_s: float = COMMAND_TIMEOUT_S,
    ) -> None:
        """Start the owner thread.

        Args:
            opener: Builds the Ready (engine, session, link, monitor); a fake in tests.
            calibrator: Runs a calibration for a mode; by default the driver's, in work.
            on_take_logged: Called in the owner thread with a take's path once its complete
                log is written; it must return quickly.
            work: The cache folder the default calibrator stores the round trip in.
            tick_s: The sampler's period.
            timeout_s: How long a caller waits for a command.
        """
        self._opener = opener
        self._calibrator = calibrator or self._default_calibrator
        self._on_take_logged = on_take_logged
        self._work = work
        self._tick_s = tick_s
        self._timeout_s = timeout_s
        self._queue: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._state = _State()
        self._ready: Ready | None = None
        self._request: OpenRequest | None = None
        self._thread = threading.Thread(target=self._run, name="rec-owner", daemon=True)
        self._thread.start()

    def open(self, request: OpenRequest, timeout: float | None = None) -> None:
        """Start the engine on a beat; returns once accepted, the stages follow in `snapshot()`.

        A session already open is closed first, which a running take forbids.

        Raises:
            Busy: An open or a calibration is running.
            TakeRunning: A take is running.
            RecClosed: The owner is shutting down.
        """
        self._submit("open", lambda cmd: self._open(cmd, request), False, timeout, opening=True)

    def toggle(self, timeout: float | None = None) -> Outcome:
        """Start the next take, or finish the one running.

        Raises:
            NotReady: Not ready for takes; OutOfSync when a reply was late.
            EngineError: The engine refused or failed.
            CommandTimeout: No answer in time.
        """
        return self._submit("toggle", self._toggle, True, timeout)

    def discard_last(self, timeout: float | None = None) -> Outcome:
        """Throw away the take running, else the last one kept in this session."""
        return self._submit("discard", self._discard, True, timeout)

    def set_rack(self, rack_json: str, timeout: float | None = None) -> tuple[int, tuple[str, ...]]:
        """Monitor through a new rack; its latency in frames and the effects switched off."""
        return self._submit("set_rack", lambda cmd: self._set_rack(rack_json), True, timeout)

    def calibrate(self, mode: str, timeout: float | None = None) -> Calibrated:
        """Close the session, run the calibration, relaunch on the same beat.

        Returns when the calibration is done; the relaunch goes on in the background.

        Raises:
            Busy, TakeRunning, RecClosed: As `open`.
            EngineError, CalibrationError: The calibration failed (the relaunch still follows).
        """
        return self._submit(
            "calibrate",
            lambda cmd: self._calibrate(cmd, mode),
            False,
            timeout or CALIBRATE_TIMEOUT_S,
            opening=True,
        )

    def close(self, timeout: float | None = None) -> None:
        """Keep a take still running, quit the engine, and leave the owner idle."""
        self._submit("close", self._close, False, timeout)

    def snapshot(self) -> RecSnapshot:
        """The state at the last tick, without touching the engine.

        Peaks are the largest since the previous call.
        """
        with self._lock:
            state = self._state
            peaks = state.peaks if state.peaks is not None else state.last_peaks
            state.peaks = None
            latest = state.latest
            rate = state.rate
            return RecSnapshot(
                stage=state.stage,
                error=state.error,
                out_of_sync=state.out_of_sync,
                stopping=state.stopping,
                recording=bool(latest and latest.recording),
                take=state.take,
                take_seconds=latest.take_frames / rate if latest and rate else 0.0,
                mic_peak=peaks[0],
                out_peak=peaks[1],
                xruns=latest.xruns if latest else 0,
                overflows=latest.overflows if latest else 0,
                dropouts=state.dropouts,
                kept=state.kept,
                rate=rate,
                quantum=state.quantum,
                monitor_ms=state.monitor_ms,
                dropped=state.dropped,
                age_s=time.monotonic() - state.at,
                round_trip=state.round_trip,
                notices=state.notices,
            )

    def taking(self) -> bool:
        """Whether a take is recording or finishing; reads the state, not the engine."""
        with self._lock:
            state = self._state
            return bool(state.stopping or state.take or (state.latest and state.latest.recording))

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_S) -> None:
        """Refuse new commands, close the session in the owner thread, and join it.

        A running take is kept and logged. A command already running (a stop can take 15 s)
        finishes first.
        """
        with self._lock:
            if not self._state.closing:
                self._state.closing = True
                self._queue.put(_STOP)
        self._thread.join(timeout)

    def _submit(
        self,
        name: str,
        run: Callable[[_Command], Any],
        needs_ready: bool,
        timeout: float | None,
        opening: bool = False,
    ) -> Any:
        command = _Command(name, run, needs_ready)
        with self._lock:
            state = self._state
            if state.closing:
                raise RecClosed("the recording engine is shut down")
            if needs_ready:
                self._require_ready()
            if opening:
                if state.opening:
                    raise Busy("the engine is opening or calibrating")
                state.opening = True
            self._queue.put(command)
        return command.wait(self._timeout_s if timeout is None else timeout)

    def _require_ready(self) -> None:
        state = self._state
        if state.out_of_sync:
            raise OutOfSync("the engine answered late: reopen it")
        if state.opening or state.stage != "ready":
            raise NotReady(f"the engine is not ready ({state.stage})")

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self._state, name, value)

    def _run(self) -> None:
        next_tick = time.monotonic() + self._tick_s
        while True:
            try:
                item = self._queue.get(timeout=max(0.0, next_tick - time.monotonic()))
            except queue.Empty:
                item = None
            if item is _STOP:
                break
            if item is not None:
                self._handle(item)
            if time.monotonic() >= next_tick:
                self._tick()
                next_tick = time.monotonic() + self._tick_s
        self._finish()

    def _handle(self, command: _Command) -> None:
        with command.lock:
            skipped = command.state == "abandoned"
            command.state = "running"
        if skipped:
            if command.name in ("open", "calibrate"):
                self._set(opening=False)
            return
        try:
            if command.needs_ready:
                with self._lock:
                    self._require_ready()
            command.reply(command.run(command))
        except EngineError as error:
            self._engine_error(error)
            command.fail(error)
        except Exception as error:  # the thread must outlive a bad command  # noqa: BLE001
            command.fail(error)
        finally:
            if command.name in ("open", "calibrate"):
                self._set(opening=False)
            self._view()

    def _tick(self) -> None:
        state = self._state
        if self._ready is None or state.out_of_sync or state.opening or state.stage != "ready":
            return
        with contextlib.suppress(EngineError):
            self._sample()

    def _sample(self) -> Meters:
        ready = self._ready
        if ready is None or self._state.out_of_sync:
            raise EngineError("the engine is not in step: reopen it")
        try:
            meters = ready.engine.meters()
        except EngineError as error:
            self._engine_error(error)
            raise
        with self._lock:
            state = self._state
            now = (meters.mic_peak, meters.out_peak)
            state.peaks = (
                now
                if state.peaks is None
                else (
                    max(state.peaks[0], now[0]),
                    max(state.peaks[1], now[1]),
                )
            )
            state.last_peaks = now
            state.latest = meters
            state.at = time.monotonic()
            if state.error is not None and not state.out_of_sync and state.stage == "ready":
                state.error = None
        return meters

    def _engine_error(self, error: EngineError) -> None:
        ready = self._ready
        with self._lock:
            state = self._state
            if state.stage in ("failed", "closed", "idle"):
                return
            if ready is not None and ready.engine.exited():
                state.stage = "failed"
                state.error = str(error)
            elif is_desync(error):
                state.out_of_sync = True
                state.error = str(error)
            else:
                state.error = str(error)

    def _view(self) -> None:
        ready = self._ready
        if ready is None:
            return
        session = ready.session
        current = session.current or session.pending
        with self._lock:
            self._state.take = current.stem if current is not None else None
            self._state.kept = len(session.recorded)

    def _holds_take(self) -> bool:
        ready = self._ready
        return ready is not None and (
            ready.session.current is not None or ready.session.pending is not None
        )

    def _logged(self, path: Path) -> None:
        if self._on_take_logged is None:
            return
        try:
            self._on_take_logged(path)
        except Exception:  # a ranking that fails must not lose the take
            LOG.exception("on_take_logged failed for %s", path.name)

    def _stage(self, stage: Stage) -> None:
        if stage.name in driver.STAGES and stage.name not in ("ready", "failed"):
            self._set(stage=stage.name)

    def _session(self) -> Session:
        # commands that need a session are refused before they reach the owner while not ready
        assert self._ready is not None
        return self._ready.session

    def _toggle(self, command: _Command) -> Outcome:
        session = self._session()
        self._set(stopping=session.current is not None or session.pending is not None)
        try:
            outcome = session.toggle()
        finally:
            self._set(stopping=False)
        self._count(outcome)
        return outcome

    def _discard(self, command: _Command) -> Outcome:
        session = self._session()
        self._set(stopping=session.current is not None)
        try:
            return session.discard_last()
        finally:
            self._set(stopping=False)

    def _count(self, outcome: Outcome) -> None:
        if outcome.code in ("kept", "kept_late"):
            with self._lock:
                self._state.dropouts += int(outcome.values["dropouts"])

    def _set_rack(self, rack_json: str) -> tuple[int, tuple[str, ...]]:
        ready = self._ready
        assert ready is not None
        budget = budget_frames(ready.link.trip, ready.link.rate)
        latency, dropped = ready.engine.set_rack(rack_json, budget)
        self._set(
            monitor_ms=monitor_ms(ready.link.trip, latency, ready.link.rate),
            dropped=tuple(dropped),
        )
        return latency, tuple(dropped)

    def _close_session(self) -> None:
        ready = self._ready
        if ready is None:
            return
        try:
            ready.session.close()
        except (EngineError, OSError) as error:
            self._set(error=str(error))
        finally:
            self._ready = None

    def _close(self, command: _Command) -> None:
        self._close_session()
        self._set(stage="closed", out_of_sync=False, take=None, stopping=False)
        self._request = None

    def _open(self, command: _Command, request: OpenRequest) -> None:
        if self._holds_take() and not self._state.out_of_sync:
            raise TakeRunning("a take is running: stop it before changing the beat")
        command.reply()
        self._close_session()
        self._request = request
        self._launch(request)

    def _launch(self, request: OpenRequest) -> None:
        self._set(
            stage="launching",
            error=None,
            out_of_sync=False,
            take=None,
            kept=0,
            dropouts=0,
            latest=None,
            peaks=None,
            last_peaks=(0.0, 0.0),
            monitor_ms=None,
            dropped=(),
            round_trip=None,
            notices=(),
            rate=None,
            quantum=None,
            at=time.monotonic(),
        )
        try:
            ready = self._opener(request, self._stage, self._sample_for_session, self._logged)
        except Exception as error:  # the page shows why, the thread goes on  # noqa: BLE001
            self._set(stage="failed", error=str(error) or type(error).__name__)
            return
        self._ready = ready
        self._set(
            stage="ready",
            rate=ready.link.rate,
            quantum=ready.link.quantum,
            monitor_ms=ready.monitor.latency_ms,
            dropped=ready.monitor.dropped,
            round_trip=(ready.link.trip.label(), ready.link.trip.source),
            notices=tuple((n.code, dict(n.values)) for n in ready.link.notices),
            at=time.monotonic(),
        )
        with contextlib.suppress(EngineError):
            self._sample()

    def _sample_for_session(self) -> Meters:
        return self._sample()

    def _default_calibrator(self, mode: str, on_stage: OnStage) -> Calibrated:
        if self._work is None:
            raise RecError("no work folder to store the calibration in")
        return driver.calibrate(driver.resolve_command(on_stage), self._work, mode)

    def _calibrate(self, command: _Command, mode: str) -> None:
        if self._holds_take():
            raise TakeRunning("a take is running: stop it before calibrating")
        self._close_session()
        self._set(stage="calibrating", error=None, out_of_sync=False, latest=None)
        try:
            command.reply(self._calibrator(mode, self._stage))
        except Exception as error:  # noqa: BLE001
            command.fail(error)
        if self._request is not None:
            self._launch(self._request)
        else:
            self._set(stage="closed")

    def _finish(self) -> None:
        self._close_session()
        self._set(stage="closed", stopping=False, take=None)
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, _Command):
                item.fail(RecClosed("the recording engine is shut down"))
