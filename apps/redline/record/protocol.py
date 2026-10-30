"""What the native engine `redline-rec` says, read into Python.

The engine speaks one JSON object a line each way (apps/redline-dsp/rec/src/protocol.rs): a
command names itself in "cmd", a reply names the command it answers in "reply", or is
{"reply": "error", "error": ...}.
"""

import json
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
    """Peaks since the last call, counters, and the monitor rack's latency in frames."""

    mic_peak: float
    out_peak: float
    xruns: int
    overflows: int
    recording: bool
    take_frames: int
    rack_latency: int


@dataclass(frozen=True)
class Startup:
    """What the engine was granted: rate, quantum, its ports, and the ports' round trip.

    estimate is the JACK ports' latency in and out, in frames; None when not reported.
    """

    rate: int
    quantum: int
    estimate: int | None
    devices: tuple[str, ...]


class EngineError(Exception):
    """The engine did not start, stopped, or refused a command; the message says why."""


def _positive(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EngineError(f"the engine's startup line has no usable {name}: {value!r}")
    return value


def _estimate(data: dict[str, Any]) -> int | None:
    if isinstance(data.get("latency_estimate"), int):
        return int(data["latency_estimate"])
    ranges = data.get("latency")
    if not isinstance(ranges, dict):
        return None
    try:
        # a JACK latency range is [min, max]; the worst of each way
        return int(max(ranges["capture"])) + int(max(ranges["playback"]))
    except (KeyError, TypeError, ValueError):
        return None


def parse_startup(line: str) -> Startup:
    """Read the engine's first line: the rate and quantum granted, ports and latency.

    Accepted: {"rate": N, "quantum": N} with either "latency_estimate": frames (the round
    trip) or "latency": {"capture": [min, max], "playback": [min, max]}, and "devices": a
    list of port names or an object of them. Anything else in the line is ignored.

    Raises:
        EngineError: The line is not such an object, or is the engine's error reply.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        raise EngineError(f"the engine did not start: {line.strip()[:200]}") from None
    if not isinstance(data, dict):
        raise EngineError(f"the engine's startup line is not an object: {line.strip()[:200]}")
    if data.get("reply") == "error":
        raise EngineError(str(data.get("error")))
    devices = data.get("devices", [])
    if isinstance(devices, dict):
        devices = list(devices.values())
    return Startup(
        _positive(data.get("rate"), "rate"),
        _positive(data.get("quantum"), "quantum"),
        _estimate(data),
        tuple(str(d) for d in devices) if isinstance(devices, list) else (),
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


def meters(data: dict[str, Any]) -> Meters:
    """A meters reply."""
    return Meters(
        float(data["mic_peak"]),
        float(data["out_peak"]),
        int(data["xruns"]),
        int(data["overflows"]),
        bool(data["recording"]),
        int(data["take_frames"]),
        int(data["rack_latency"]),
    )
