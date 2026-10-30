"""What the native engine `redline-rec` says, read into Python.

The engine speaks one JSON object a line each way (apps/redline-dsp/rec/src/protocol.rs): a
command names itself in "cmd", a reply names the command it answers in "reply", or is
{"reply": "error", "error": ...}.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Dropout:
    """Silence the engine wrote where input was lost: take frames start..start + frames."""

    start: int
    frames: int
    causes: tuple[str, ...]


@dataclass(frozen=True)
class TakeReport:
    """A finished take as the engine reports it; offset is the round trip it dropped."""

    path: Path
    rate: int
    frames: int
    offset: int
    dropouts: list[Dropout]


@dataclass(frozen=True)
class Meters:
    """Peaks since the last call, counters, and the monitor rack's latency in frames.

    quantum is the graph's period now; None from an engine that does not report it.
    """

    mic_peak: float
    out_peak: float
    xruns: int
    overflows: int
    recording: bool
    take_frames: int
    rack_latency: int
    quantum: int | None = None


@dataclass(frozen=True)
class Startup:
    """What the engine was granted: rate, quantum, its ports, and the ports' round trip.

    estimate is the JACK ports' latency in and out, in frames; None when they report none.
    devices are the ports the engine connected to: mic, left, right, each with its aliases
    in brackets when JACK reports any (under jackd the names alone are system:capture_1).
    """

    rate: int
    quantum: int
    estimate: int | None
    devices: tuple[str, ...]


class EngineError(Exception):
    """The engine did not start, stopped, or refused a command; the message says why."""


class WrongRate(EngineError):
    """The engine refused a beat at beat_rate: the audio graph runs at rate."""

    def __init__(self, rate: int, beat_rate: int) -> None:
        """Carry both rates."""
        super().__init__(f"the beat is at {beat_rate} Hz and the audio graph runs at {rate} Hz")
        self.rate = rate
        self.beat_rate = beat_rate


def _positive(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EngineError(f"the engine's startup line has no usable {name}: {value!r}")
    return value


def _frames(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _devices(data: dict[str, Any], shown: str) -> tuple[str, ...]:
    # an engine before the aliases sends none
    devices = []
    for name in ("mic", "left", "right"):
        port, aliases = data.get(name), data.get(f"{name}_aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise EngineError(f"the engine's startup line has no usable {name} aliases: {shown}")
        if isinstance(port, str) and port:
            devices.append(f"{port} ({', '.join(aliases)})" if aliases else port)
    return tuple(devices)


def parse_startup(line: str) -> Startup:
    """Read the engine's first line: the rate and quantum granted, its ports and latency.

    The line is {"event": "started", ...} as rec/src/protocol.rs writes it. The estimate is
    the ports' worst capture plus worst playback latency, as the engine computes it when no
    round trip is passed in; the devices are the ports it connected to.

    Raises:
        WrongRate: The engine refused the beat ({"event": "wrong_rate", ...}).
        EngineError: The line is not the engine's startup line, or is its error reply.
    """
    shown = line.strip()[:200]
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        raise EngineError(f"the engine did not start: {shown}") from None
    if not isinstance(data, dict):
        raise EngineError(f"the engine's startup line is not an object: {shown}")
    if data.get("reply") == "error":
        raise EngineError(str(data.get("error")))
    if data.get("event") == "wrong_rate":
        raise WrongRate(
            _positive(data.get("rate"), "rate"), _positive(data.get("beat_rate"), "beat rate")
        )
    if data.get("event") != "started":
        raise EngineError(f"the engine's first line is not its startup line: {shown}")
    ranges: list[Any] = [data.get("capture_latency"), data.get("playback_latency")]
    if not all(isinstance(r, list) and len(r) == 2 and all(map(_frames, r)) for r in ranges):
        raise EngineError(f"the engine's startup line has no usable port latency: {shown}")
    source = data.get("round_trip_source")
    if source not in ("port_latency_estimate", "calibrated") or not _frames(data.get("round_trip")):
        raise EngineError(f"the engine's startup line has no usable round trip: {shown}")
    if source == "port_latency_estimate":
        estimate = data["round_trip"]
    else:
        estimate = ranges[0][1] + ranges[1][1]
    return Startup(
        _positive(data.get("rate"), "rate"),
        _positive(data.get("quantum"), "quantum"),
        # ports that report no latency say nothing about the round trip
        estimate or None,
        _devices(data, shown),
    )


def take_report(data: dict[str, Any]) -> TakeReport:
    """A stop reply's take."""
    return TakeReport(
        Path(data["path"]),
        int(data["rate"]),
        int(data["frames"]),
        int(data["offset"]),
        [
            Dropout(int(d["start"]), int(d["frames"]), tuple(str(c) for c in d["causes"]))
            for d in data["dropouts"]
        ],
    )


def _peak(value: Any) -> float:
    # JSON has no infinity: an engine that let one through wrote null
    return math.inf if value is None else float(value)


def meters(data: dict[str, Any]) -> Meters:
    """A meters reply; a null peak is a non-finite one, read as infinite."""
    return Meters(
        _peak(data["mic_peak"]),
        _peak(data["out_peak"]),
        int(data["xruns"]),
        int(data["overflows"]),
        bool(data["recording"]),
        int(data["take_frames"]),
        int(data["rack_latency"]),
        None if data.get("quantum") is None else int(data["quantum"]),
    )
