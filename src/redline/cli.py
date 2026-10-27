"""Command line entry point."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from redline import __version__
from redline.audio import AudioError

if TYPE_CHECKING:
    from redline.separate import Separator

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level parser with its subcommands."""
    parser = argparse.ArgumentParser(prog="redline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"redline {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    measure = commands.add_parser(
        "measure", help="measure a track against references and write a report (stage 1)"
    )
    measure.add_argument("--track", type=Path, required=True, help="the track from Suno")
    measure.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    measure.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    vocal = commands.add_parser(
        "vocal", help="process the vocal at every dirt level and write blind listening files"
    )
    vocal.add_argument("--track", type=Path, required=True, help="the track from Suno")
    vocal.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    vocal.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    vocal.add_argument("--out", type=Path, default=Path("output"), help="listening files folder")
    vocal.add_argument("--seed", type=int, help="seed of the letter shuffle")
    return parser


def reference_files(paths: Sequence[Path]) -> list[Path]:
    """Expand folders into the audio files inside them, sorted by name."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files += sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)
        else:
            files.append(path)
    return files


def _measure(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.run import measure_run
    from redline.separate import RoformerSeparator, SeparationError

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        run_dir = measure_run(args.track, refs, args.work, separator or RoformerSeparator())
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(run_dir / "report.md")
    return 0


def _vocal(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.separate import RoformerSeparator, SeparationError
    from redline.vocal_run import vocal_run

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        result = vocal_run(
            args.track, refs, args.work, args.out, separator or RoformerSeparator(), args.seed
        )
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(result.listen_dir)
    return 0


def main(argv: Sequence[str] | None = None, separator: "Separator | None" = None) -> int:
    """Run the command line and return the exit code; tests pass their own separator."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "measure":
        return _measure(args, separator)
    if args.command == "vocal":
        return _vocal(args, separator)
    parser.print_help()
    return 0
