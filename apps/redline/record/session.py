"""A recording session over a running engine: what the keys do and what the status line says.

The terminal only reads keys and prints; everything here runs against any engine that
speaks the protocol, so it is tested with a fake one.
"""

import math
from pathlib import Path
from typing import Protocol

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

    def start(self, path: Path, offset: int) -> None:
        """Start a take."""
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


class Session:
    """Takes recorded into one song's folder: start, stop, discard, quit."""

    def __init__(self, engine: Engine, folder: Path, round_trip: RoundTrip, quantum: int):
        """Record into folder, shifting takes by round_trip (0 frames when it is unknown)."""
        self.engine = engine
        self.folder = folder
        self.round_trip = round_trip
        self.quantum = quantum
        self.current: Path | None = None
        self.recorded: list[Path] = []

    def toggle(self) -> str:
        """Start the next take, or finish the one running; what happened, in a line."""
        if self.current is None:
            now = self.engine.meters().quantum
            if now is not None and now != self.quantum:
                return (
                    f"take refused: the audio graph's quantum moved from {self.quantum} to "
                    f"{now} frames, so the round trip the takes are shifted by no longer "
                    "holds. Quit and restart `redline record`"
                )
            path = next_take(self.folder).resolve()
            self.engine.start(path, self.round_trip.frames or 0)
            self.current = path
            return f"recording {path.stem}"
        report = self._finish()
        lost = sum(1 for d in report.dropouts if d.frames > 0)
        note = f", {lost} dropouts" if lost else ""
        return f"{report.path.stem} kept: {report.frames / report.rate:.1f} s{note}"

    def _finish(self) -> TakeReport:
        assert self.current is not None
        path, self.current = self.current, None
        try:
            report = self.engine.stop()
        except EngineError as error:
            # `redline takes` must not read a missing log as a take without dropouts
            write_incomplete_sidecar(path, str(error), self.round_trip, self.quantum)
            self.recorded.append(path)
            raise
        write_sidecar(report, self.round_trip, self.quantum)
        self.recorded.append(path)
        return report

    def discard_last(self) -> str:
        """Throw away the take running, else the last one kept in this session."""
        if self.current is not None:
            path, self.current = self.current, None
            try:
                self.engine.stop()
            finally:
                discard(path)
            return f"{path.stem} discarded"
        if not self.recorded:
            return "nothing recorded in this session to discard"
        path = self.recorded.pop()
        discard(path)
        return f"{path.stem} discarded"

    def close(self) -> None:
        """Keep a take still running, then quit the engine."""
        try:
            if self.current is not None:
                self._finish()
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
