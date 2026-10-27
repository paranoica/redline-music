"""Command line entry point."""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from redline import __version__
from redline.audio import AudioError

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


def _measure(args: argparse.Namespace) -> int:
    from redline.run import measure_run
    from redline.separate import RoformerSeparator

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        run_dir = measure_run(args.track, refs, args.work, RoformerSeparator())
    except (AudioError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(run_dir / "report.md")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line and return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "measure":
        return _measure(args)
    parser.print_help()
    return 0
