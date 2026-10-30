"""The takes folder: take-NN.wav files, each with the take-NN.json log `redline takes` reads."""

import json
from pathlib import Path

from redline.record.latency import RoundTrip
from redline.record.protocol import TakeReport
from redline.takes.rank import TAKE_NAME


def next_take(folder: Path) -> Path:
    """The path of the next take: one past the highest number in the folder, made if missing."""
    folder.mkdir(parents=True, exist_ok=True)
    numbers = [
        int(match[1])
        for p in folder.iterdir()
        if p.is_file() and (match := TAKE_NAME.fullmatch(p.name))
    ]
    return folder / f"take-{max(numbers, default=0) + 1:02d}.wav"


def write_sidecar(
    report: TakeReport, round_trip: RoundTrip, quantum: int, quantum_now: int | None = None
) -> Path:
    """Write the take's log next to it and return its path.

    quantum_now is the graph's quantum after the take when it is not the one the session
    started at: the log is then marked "quantum_changed", and the take's offset and
    dropouts are not to be trusted.

    "dropouts" holds the [start, end) frame ranges that were filled with silence, as
    `redline.takes.rank.read_dropouts` reads them; an xrun that lost no frames is counted
    apart, in "xruns_without_loss".
    """
    lost = [d for d in report.dropouts if d.frames > 0]
    data = {
        "dropouts": [[d.start, d.start + d.frames] for d in lost],
        "dropout_causes": [list(d.causes) for d in lost],
        "xruns_without_loss": sum(1 for d in report.dropouts if d.frames == 0),
        "rate": report.rate,
        "frames": report.frames,
        "quantum": quantum,
        "offset": report.offset,
        "offset_source": round_trip.source,
        "complete": True,
    }
    if quantum_now is not None:
        data |= {"quantum_changed": True, "quantum_now": quantum_now}
    sidecar = report.path.with_suffix(".json")
    sidecar.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return sidecar


def write_incomplete_sidecar(take: Path, error: str, round_trip: RoundTrip, quantum: int) -> Path:
    """Log a take whose stop failed: no report came, so its dropouts are unknown."""
    data = {
        "complete": False,
        "error": error,
        "quantum": quantum,
        "offset": round_trip.frames or 0,
        "offset_source": round_trip.source,
    }
    sidecar = take.with_suffix(".json")
    sidecar.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return sidecar


def discard(take: Path) -> None:
    """Delete a take and its log."""
    take.unlink(missing_ok=True)
    take.with_suffix(".json").unlink(missing_ok=True)
