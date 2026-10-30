"""The recording startup shared by `redline record` and the song page.

The steps are the same for both: find or build the engine, start it on the beat (again at the
graph's rate when that differs), read the round trip, set up the monitor, open a session.
Nothing here prints: progress comes as stages, facts as dataclasses with codes and values, and
each front end words them (the terminal in `redline.record.terminal`).
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.engine import live
from redline.engine.settings import Settings
from redline.record import run as record_run
from redline.record.latency import LatencyKey, RoundTrip, round_trip
from redline.record.process import EngineProcess
from redline.record.protocol import Meters
from redline.record.session import (
    QUANTUM_LIMIT,
    Session,
    budget_frames,
    monitor_ms,
    quantum_warning,
)
from redline.record.takes import file_sha256

if TYPE_CHECKING:
    from redline.measure.separate import Separator

STAGES = ("building", "launching", "relaunching", "analysing", "level_check", "ready", "failed")

# two openings must not run the same cargo build at once
_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class Stage:
    """Where the startup is, with what is known there.

    Names and values:
        building: the engine binary is missing and is being built.
        launching: the engine starts on the beat at a guessed rate.
        relaunching: the graph runs at another rate; rate. The engine starts again.
        analysing: the monitor numbers are measured from a take; take.
        level_check: the mic is heard for a few seconds; seconds.
        ready: a session is open.
        failed: reason.
    """

    name: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Notice:
    """Something to tell the singer, as a code and values.

    Codes and values:
        quantum_high: quantum, limit; the monitor cannot be under 10 ms.
        speakers: calibrated through speakers, so the round trip includes the air.
    """

    code: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Link:
    """What the audio graph granted and the round trip the takes are shifted by."""

    rate: int
    quantum: int
    trip: RoundTrip
    notices: tuple[Notice, ...]


@dataclass(frozen=True)
class Monitor:
    """The monitor as set up: where its numbers came from, what was switched off, how late it is.

    latency_ms is None when the round trip is unknown.
    """

    source: str
    dropped: tuple[str, ...]
    latency_ms: float | None


@dataclass
class Ready:
    """A started engine with its session and what was found on the way."""

    engine: EngineProcess
    session: Session
    link: Link
    monitor: Monitor


@dataclass(frozen=True)
class Calibrated:
    """A stored calibration."""

    key: LatencyKey
    frames: int
    ms: float
    peak_to_noise_db: float
    mode: str
    stored_in: Path
    notices: tuple[Notice, ...]


OnStage = Callable[[Stage], None]


def _ignore(stage: Stage) -> None:
    pass


def resolve_command(on_stage: OnStage = _ignore) -> list[str]:
    """The engine's binary as a command; a missing one is built first (stage building).

    Raises:
        EngineError: It is missing and cannot be built.
    """
    with _BUILD_LOCK:
        return record_run.engine_command(lambda message: on_stage(Stage("building")))


def calibrate(
    command: Sequence[str], work: Path, mode: str, wait: record_run.Wait | None = None
) -> Calibrated:
    """Run the calibration and report what was stored.

    Raises:
        EngineError: The engine did not start or record.
        CalibrationError: The sweep did not come back clearly enough.
    """
    key, delay = record_run.calibrate(command, work, mode, wait=wait)
    notices = (Notice("speakers"),) if mode == "speakers" else ()
    return Calibrated(
        key,
        delay.frames,
        1000 * delay.frames / key.rate,
        delay.peak_to_noise_db,
        mode,
        work / "record" / record_run.LATENCY_FILE,
        notices,
    )


class Driver:
    """The startup for one beat and one takes folder."""

    def __init__(
        self,
        command: Sequence[str],
        beat: Path,
        folder: Path,
        work: Path,
        refs: Sequence[Path] = (),
        separator: "Separator | None" = None,
        settings: Settings | None = None,
    ) -> None:
        """Record over beat into folder, with the caches and calibration in work."""
        self.command = list(command)
        self.beat = beat
        self.folder = folder
        self.work = work
        self.refs = list(refs)
        self.separator = separator
        self.settings = Settings() if settings is None else settings

    def open(self, on_stage: OnStage = _ignore) -> EngineProcess:
        """Start the engine on the beat at the graph's rate (stages launching, relaunching).

        Raises:
            EngineError: The engine did not start, or the graph changed rate between two starts.
            OSError: The beat cannot be read or written at the rate.
        """
        folder = self.work / "record"
        on_stage(Stage("launching"))
        return record_run.open_engine(
            self.command,
            lambda rate: record_run.prepare_beat(self.beat, rate, folder),
            on_relaunch=lambda rate: on_stage(Stage("relaunching", {"rate": rate})),
        )

    def link(self, engine: EngineProcess) -> Link:
        """The rate and quantum granted, the round trip and the warnings that follow."""
        startup = engine.startup
        notices = []
        if quantum_warning(startup.quantum):
            notices.append(
                Notice("quantum_high", {"quantum": startup.quantum, "limit": QUANTUM_LIMIT})
            )
        trip = round_trip(
            self.work / "record" / record_run.LATENCY_FILE,
            record_run.latency_key(startup),
            startup.estimate,
        )
        return Link(startup.rate, startup.quantum, trip, tuple(notices))

    def monitor(self, engine: EngineProcess, link: Link, on_stage: OnStage = _ignore) -> Monitor:
        """Measure the monitor numbers, build the rack and give it to the engine.

        Raises:
            ValueError: The level heard is silent, or the analysis fails.
            EngineError: The level check could not record, or the engine refused the rack.
        """

        def stage(name: str, values: dict[str, Any]) -> None:
            on_stage(Stage(name, values))

        numbers, source = record_run.monitor_numbers(
            engine,
            self.folder,
            self.beat,
            self.refs,
            self.work,
            self.separator,
            self.settings,
            say=lambda message: None,
            on_stage=stage,
        )
        rack = live.monitor_rack(self.settings, numbers)
        latency, dropped = engine.set_rack(rack, budget_frames(link.trip, link.rate))
        return Monitor(source, tuple(dropped), monitor_ms(link.trip, latency, link.rate))

    def start(
        self,
        engine: EngineProcess,
        on_stage: OnStage = _ignore,
        on_link: Callable[[Link], None] | None = None,
        meters: Callable[[], Meters] | None = None,
        on_logged: Callable[[Path], None] | None = None,
    ) -> Ready:
        """Everything after the launch: link, monitor, session (stage ready at the end).

        on_link is called with the link before the monitor is measured, which can take a
        while. meters and on_logged go to the session.
        """
        link = self.link(engine)
        if on_link is not None:
            on_link(link)
        monitor = self.monitor(engine, link, on_stage)
        session = Session(
            engine,
            self.folder,
            link.trip,
            link.quantum,
            file_sha256(self.beat),
            meters,
            on_logged,
        )
        on_stage(Stage("ready"))
        return Ready(engine, session, link, monitor)
