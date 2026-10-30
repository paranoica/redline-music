"""The takes folder: take-NN.wav files, each with the take-NN.json log `redline takes` reads."""

import hashlib
import json
from pathlib import Path

from redline.core.private import write_private
from redline.record.latency import RoundTrip
from redline.record.protocol import TakeReport
from redline.takes.rank import TAKE_NAME


def next_take(folder: Path) -> Path:
    """The path of the next take: one past the highest number in the folder, made if missing."""
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    numbers = [
        int(match[1])
        for p in folder.iterdir()
        if p.is_file() and (match := TAKE_NAME.fullmatch(p.name))
    ]
    return folder / f"take-{max(numbers, default=0) + 1:02d}.wav"


def file_sha256(path: Path) -> str:
    """The hex SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def write_sidecar(
    report: TakeReport,
    round_trip: RoundTrip,
    quantum: int,
    quantum_now: int | None = None,
    quantum_unchecked: bool = False,
    beat_sha256: str | None = None,
) -> Path:
    """Write the take's log next to it and return its path.

    quantum_now is the graph's quantum after the take when it is not the one the session
    started at: the log is then marked "quantum_changed", and the take's offset and
    dropouts are not to be trusted. quantum_unchecked marks it the same way, with no
    quantum_now, when the quantum could not be read after the take.

    "dropouts" holds the [start, end) frame ranges that were filled with silence, as
    `redline.takes.rank.read_dropouts` reads them; an xrun that lost no frames is counted
    apart, in "xruns_without_loss". beat_sha256 is the hash of the beat the take was
    recorded over (null when not known).
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
        "beat_sha256": beat_sha256,
    }
    if quantum_now is not None or quantum_unchecked:
        data |= {"quantum_changed": True, "quantum_now": quantum_now}
    sidecar = report.path.with_suffix(".json")
    write_private(sidecar, json.dumps(data, indent=1) + "\n")
    return sidecar


def write_incomplete_sidecar(
    take: Path,
    error: str,
    round_trip: RoundTrip,
    quantum: int,
    beat_sha256: str | None = None,
) -> Path:
    """Log a take whose stop failed: no report came, so its dropouts are unknown."""
    data = {
        "complete": False,
        "error": error,
        "quantum": quantum,
        "offset": round_trip.frames or 0,
        "offset_source": round_trip.source,
        "beat_sha256": beat_sha256,
    }
    sidecar = take.with_suffix(".json")
    write_private(sidecar, json.dumps(data, indent=1) + "\n")
    return sidecar


def discard(take: Path) -> None:
    """Delete a take and its log."""
    take.unlink(missing_ok=True)
    take.with_suffix(".json").unlink(missing_ok=True)
