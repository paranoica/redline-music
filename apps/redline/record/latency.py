"""Calibrated round trips, kept per devices, rate and quantum, and the fallback estimate.

A round trip holds only for the ports, rate and quantum it was measured on. A calibration
made for another of them is refused with a note, never applied: the takes would be shifted
by the wrong amount without a word. Without a calibration the JACK ports' latency is used,
and every place that shows it says it is an estimate.
"""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from redline.record.sweep import Delay

MODES = ("cup", "loopback", "speakers")
SPEAKERS_NOTE = (
    "calibrated through the speakers: an acoustic path, so the round trip includes the air "
    "delay to the mic. Recalibrate with the headphone cup at the mic for the takes' offset"
)


@dataclass(frozen=True)
class LatencyKey:
    """What a round trip holds for: the connected ports, the rate and the quantum."""

    devices: tuple[str, ...]
    rate: int
    quantum: int

    def name(self) -> str:
        """The key as one readable string."""
        return f"{', '.join(self.devices) or 'no ports'} at {self.rate} Hz, quantum {self.quantum}"


@dataclass(frozen=True)
class RoundTrip:
    """The round trip the takes are shifted by, where it came from, and why if not calibrated.

    source is "calibrated", "estimate" (the JACK ports' latency) or "unknown" (frames None).
    """

    frames: int | None
    source: str
    note: str | None = None

    def label(self) -> str:
        """How the terminal names it."""
        if self.frames is None:
            return "round trip unknown: takes are not shifted"
        if self.source == "estimate":
            return f"round trip {self.frames} frames (JACK port estimate)"
        return f"round trip {self.frames} frames (calibrated)"


def _entry_key(entry: dict[str, Any]) -> LatencyKey:
    return LatencyKey(
        tuple(str(d) for d in entry["devices"]), int(entry["rate"]), int(entry["quantum"])
    )


def _frames(entry: dict[str, Any]) -> int:
    frames = entry["frames"]
    # a round trip longer than a second is not a sound path the engine can monitor through
    if isinstance(frames, bool) or not isinstance(frames, int) or not 0 <= frames <= entry["rate"]:
        raise ValueError(f"the stored round trip {frames!r} is not 0 to {entry['rate']} frames")
    return frames


def _read(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data["calibrations"]
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("calibrations is not a list of objects")
    return entries


def save_calibration(path: Path, key: LatencyKey, delay: Delay, mode: str) -> None:
    """Store a measured round trip under its key, replacing one stored for the same key.

    Raises:
        ValueError: mode is not one of MODES, or the file holds something unreadable.
    """
    if mode not in MODES:
        raise ValueError(f"unknown calibration mode {mode!r}; one of {', '.join(MODES)}")
    entries = _read(path) if path.exists() else []
    kept = [e for e in entries if e.get("key") != key.name()]
    kept.append(
        {
            "key": key.name(),
            "devices": list(key.devices),
            "rate": key.rate,
            "quantum": key.quantum,
            "frames": delay.frames,
            # a recording without any noise has an infinite ratio, which JSON cannot hold
            "peak_to_noise_db": (
                round(delay.peak_to_noise_db, 1) if math.isfinite(delay.peak_to_noise_db) else None
            ),
            "mode": mode,
            "measured": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"calibrations": kept}, indent=1) + "\n", encoding="utf-8")


def _fallback(estimate: int | None, note: str) -> RoundTrip:
    if estimate is None:
        return RoundTrip(None, "unknown", note)
    return RoundTrip(estimate, "estimate", f"{note}; using the JACK port latency, an estimate")


def round_trip(path: Path, key: LatencyKey, estimate: int | None) -> RoundTrip:
    """The calibrated round trip for this key, else the port estimate with the reason.

    Args:
        path: The latency.json file; it may be missing.
        key: The devices, rate and quantum the engine runs at now.
        estimate: The round trip the JACK ports report, in frames; None when unknown.
    """
    again = "run `redline record --calibrate`"
    if not path.exists():
        return _fallback(estimate, f"no calibration yet: {again}")
    try:
        entries = _read(path)
        others = [e for e in entries if e.get("key") != key.name()]
        mine = [e for e in entries if e.get("key") == key.name()]
        if mine and _entry_key(mine[0]) != key:
            raise ValueError(f"the entry for {key.name()} does not match its own fields")
        if mine:
            note = SPEAKERS_NOTE if mine[0].get("mode") == "speakers" else None
            return RoundTrip(_frames(mine[0]), "calibrated", note)
        stored = "; ".join(str(e.get("key")) for e in others) or "nothing"
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        return _fallback(estimate, f"{path.name} unreadable ({error}): {again}")
    return _fallback(
        estimate, f"calibrated for {stored}, not for {key.name()}: {again} for this setup"
    )
