"""What the native engine `redline-rec` says, read into Python.

The engine speaks one JSON object a line each way (apps/redline-dsp/rec/src/protocol.rs): a
command names itself in "cmd", a reply names the command it answers in "reply", or is
{"reply": "error", "error": ...}.
"""

from dataclasses import dataclass
from pathlib import Path


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
