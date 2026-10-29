"""Options and helpers every command shares."""

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}
LYRICS = Path("private/lyrics")
LYRICS_BUILD = LYRICS / "build"


def _inputs(command: argparse.ArgumentParser, out_help: str | None = None) -> None:
    command.add_argument("--track", type=Path, required=True, help="the track from Suno")
    command.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    command.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    if out_help is not None:
        command.add_argument("--out", type=Path, default=Path("output"), help=out_help)


def reference_files(paths: Sequence[Path]) -> list[Path]:
    """Expand folders into the audio files inside them, sorted by name."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
        else:
            files.append(path)
    return files
