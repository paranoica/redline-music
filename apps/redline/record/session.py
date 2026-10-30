"""A recording session over a running engine: what the keys do and what the status line says.

The terminal only reads keys and prints; everything here runs against any engine that
speaks the protocol, so it is tested with a fake one.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from redline.record.latency import RoundTrip
from redline.record.protocol import EngineError, Meters, TakeReport
from redline.record.takes import discard, next_take, write_incomplete_sidecar, write_sidecar

# above this quantum the round trip alone eats most of 10 ms
QUANTUM_LIMIT = 128
MONITOR_BUDGET_MS = 10.0
METER_FLOOR_DB = -60.0
METER_WIDTH = 20


class Engine(Protocol):
    """What a session needs of the engine."""

    def start(self, path: Path, offset: int) -> TakeReport | str | None:
        """Start a take; a take whose stop timed out, or its writer error, if it came since."""
        ...

    def stop(self) -> TakeReport:
        """Finish the take."""
        ...

    def close(self) -> None:
        """Quit."""
        ...

    def meters(self) -> Meters:
        """Peaks, counters and the graph's quantum now."""
        ...

    def exited(self) -> bool:
        """Whether the engine has ended."""
        ...


def quantum_warning(quantum: int) -> str | None:
    """The plain warning when the granted quantum cannot give a monitor under 10 ms."""
    if quantum <= QUANTUM_LIMIT:
        return None
    return (
        f"quantum {quantum} is above {QUANTUM_LIMIT}: under 10 ms is not met. Ask for less "
        "(PIPEWIRE_QUANTUM, or the graph's own setting) and start again"
    )


def monitor_ms(round_trip: RoundTrip, rack_latency: int, rate: int) -> float | None:
    """What the singer hears themselves late by: the round trip plus the rack; None if unknown."""
    if round_trip.frames is None:
        return None
    return 1000 * (round_trip.frames + rack_latency) / rate


def budget_frames(round_trip: RoundTrip, rate: int) -> int | None:
    """Frames the monitor rack may add within 10 ms of a calibrated round trip.

    None otherwise: the engine then holds the rack to its own budget from the port estimate.
    """
    if round_trip.source != "calibrated" or round_trip.frames is None:
        return None
    return max(0, round(MONITOR_BUDGET_MS * rate / 1000) - round_trip.frames)


def level_meter(peak: float) -> str:
    """A bar from METER_FLOOR_DB to 0 dBFS with the peak in dB."""
    db = 20 * math.log10(peak) if peak > 0 else -math.inf
    filled = round(METER_WIDTH * min(1.0, max(0.0, 1 - db / METER_FLOOR_DB)))
    shown = f"{db:5.1f} dB" if math.isfinite(db) else f"{db:5} dB"
    clip = " CLIP" if peak >= 1.0 else ""
    return f"[{'#' * filled}{'-' * (METER_WIDTH - filled)}] {shown}{clip}"


@dataclass(frozen=True)
class Outcome:
    """What a key did, as data: a code and its values; the terminal words it.

    Codes and values:
        recording: take.
        quantum_moved: take refused; was, now (frames).
        kept, kept_late: take, seconds, dropouts.
        discarded, finished_late_discarded: take.
        still_finishing: take (not yet collected).
        nothing_to_discard.
    """

    code: str
    values: dict[str, Any] = field(default_factory=dict)


def _kept(report: TakeReport, code: str) -> Outcome:
    lost = sum(1 for d in report.dropouts if d.frames > 0)
    return Outcome(
        code,
        {"take": report.path.stem, "seconds": report.frames / report.rate, "dropouts": lost},
    )


class Session:
    """Takes recorded into one song's folder: start, stop, discard, quit."""

    def __init__(
        self,
        engine: Engine,
        folder: Path,
        round_trip: RoundTrip,
        quantum: int,
        beat_sha256: str | None = None,
        meters: Callable[[], Meters] | None = None,
        on_logged: Callable[[Path], None] | None = None,
    ):
        """Record into folder, shifting takes by round_trip (0 frames when it is unknown).

        beat_sha256 goes into every take's log. meters is where the session reads the
        engine's meters from (the engine's own by default); a caller that has a sampler
        reading them passes that, so the peaks it hands out stay whole. on_logged is called
        with the take's path once its log is written complete.
        """
        self.engine = engine
        self.beat_sha256 = beat_sha256
        self._meters = meters or engine.meters
        self._on_logged = on_logged
        self.folder = folder
        self.round_trip = round_trip
        self.quantum = quantum
        self.current: Path | None = None
        self.recorded: list[Path] = []
        # a take whose stop timed out: the engine may still finish it, and the next key asks
        self.pending: Path | None = None

    def toggle(self) -> Outcome:
        """Start the next take, or finish the one running; what happened.

        After a stop timed out, the key asks the engine for that take again instead.
        """
        if self.pending is not None:
            return self._collect()
        if self.current is None:
            now = self._meters().quantum
            if now is not None and now != self.quantum:
                return Outcome("quantum_moved", {"was": self.quantum, "now": now})
            path = next_take(self.folder).resolve()
            late = self.engine.start(path, self.round_trip.frames or 0)
            self.current = path
            if not isinstance(late, str) and late is not None and late.path in self.recorded:
                self._log(late)
            return Outcome("recording", {"take": path.stem})
        return _kept(self._finish(), "kept")

    def _finish(self) -> TakeReport:
        assert self.current is not None
        path, self.current = self.current, None
        self.recorded.append(path)
        try:
            report = self.engine.stop()
        except EngineError as error:
            # `redline takes` must not read a missing log as a take without dropouts
            write_incomplete_sidecar(
                path, str(error), self.round_trip, self.quantum, self.beat_sha256
            )
            if self._holds_a_take():
                self.pending = path
            raise
        self._log(report)
        return report

    def _holds_a_take(self) -> bool:
        # a writer error ends the take in the engine; only a stop that timed out leaves one
        try:
            return self._meters().recording
        except EngineError:
            # a live engine that missed one reply may still be finishing the take
            return not self.engine.exited()

    def _log(self, report: TakeReport) -> None:
        try:
            now = self._meters().quantum
        except EngineError:
            write_sidecar(
                report,
                self.round_trip,
                self.quantum,
                quantum_unchecked=not self.engine.exited(),
                beat_sha256=self.beat_sha256,
            )
            self._logged(report)
            raise
        moved = now if now is not None and now != self.quantum else None
        write_sidecar(report, self.round_trip, self.quantum, moved, beat_sha256=self.beat_sha256)
        self._logged(report)

    def _logged(self, report: TakeReport) -> None:
        if self._on_logged is not None:
            self._on_logged(report.path)

    def _collect(self) -> Outcome:
        assert self.pending is not None
        path = self.pending
        try:
            report = self.engine.stop()
        except EngineError as error:
            if not self._holds_a_take():
                # the writer failed, or the engine holds no take: nothing more will come
                self.pending = None
                if path in self.recorded:
                    write_incomplete_sidecar(
                        path, str(error), self.round_trip, self.quantum, self.beat_sha256
                    )
            raise
        self.pending = None
        if path not in self.recorded:
            return Outcome("finished_late_discarded", {"take": path.stem})
        self._log(report)
        return _kept(report, "kept_late")

    def discard_last(self) -> Outcome:
        """Throw away the take running, else the last one kept in this session."""
        if self.current is not None:
            path, self.current = self.current, None
            try:
                self.engine.stop()
            except EngineError:
                if self._holds_a_take():
                    self.pending = path
                raise
            finally:
                discard(path)
            return Outcome("discarded", {"take": path.stem})
        if self.pending is not None and self.pending not in self.recorded:
            return Outcome("still_finishing", {"take": self.pending.stem})
        if not self.recorded:
            return Outcome("nothing_to_discard")
        path = self.recorded.pop()
        discard(path)
        return Outcome("discarded", {"take": path.stem})

    def close(self) -> None:
        """Keep a take still running, then quit the engine."""
        try:
            if self.current is not None:
                self._finish()
            elif self.pending is not None:
                self._collect()
        finally:
            self.engine.close()

    def status(self, meters: Meters, rate: int | None = None) -> str:
        """The status line: take, mic level, rate and quantum, monitor latency, xruns."""
        rate = rate or getattr(getattr(self.engine, "startup", None), "rate", 0)
        if self.current is not None:
            seconds = meters.take_frames / rate if rate else 0.0
            state = f"REC {self.current.stem} {seconds:5.1f} s"
        else:
            state = f"ready ({len(self.recorded)} kept)"
        late = monitor_ms(self.round_trip, meters.rack_latency, rate) if rate else None
        monitor = "monitor ? ms" if late is None else f"monitor {late:.1f} ms"
        if self.round_trip.source == "estimate":
            monitor += " (estimate)"
        return (
            f"{state} | mic {level_meter(meters.mic_peak)} | {rate} Hz, quantum "
            f"{self.quantum} | {monitor} | xruns {meters.xruns}"
        )
