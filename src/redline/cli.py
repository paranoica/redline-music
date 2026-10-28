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
    vocal.add_argument(
        "--axis",
        choices=("dirt", "balance"),
        default="dirt",
        help="what the variants change: the dirt level, or the vocal level against the beat",
    )
    vocal.add_argument(
        "--levels",
        default="eq,light,medium,heavy",
        help="dirt axis levels, comma separated: eq, light, medium, heavy, extra, loudest",
    )
    vocal.add_argument(
        "--vocal-db", type=float, default=0.0, help="vocal against the beat on the dirt axis, dB"
    )
    master = commands.add_parser(
        "master", help="blind round on the master: the chosen vocal, clipped to reference loudness"
    )
    master.add_argument(
        "--axis",
        choices=("master", "timbre"),
        default="master",
        help="what the variants change: the master's loudness, or the vocal's timbre",
    )
    master.add_argument("--track", type=Path, required=True, help="the track from Suno")
    master.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    master.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    master.add_argument("--out", type=Path, default=Path("output"), help="listening files folder")
    master.add_argument("--seed", type=int, help="seed of the letter shuffle")
    master.add_argument(
        "--timbres",
        help="timbre variants, comma separated; join effects with +: 'octave down + doubles'",
    )
    run = commands.add_parser(
        "run", help="process a track with the chosen settings and write the result folder"
    )
    run.add_argument("--track", type=Path, required=True, help="the track from Suno")
    run.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    run.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    run.add_argument("--out", type=Path, default=Path("output"), help="result folder")
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
            args.track,
            refs,
            args.work,
            args.out,
            separator or RoformerSeparator(),
            args.seed,
            args.axis,
            tuple(name.strip() for name in args.levels.split(",") if name.strip()),
            args.vocal_db,
        )
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(result.listen_dir)
    return 0


def _master(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.final_run import master_round, timbre_round
    from redline.separate import RoformerSeparator, SeparationError

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        common = (
            args.track,
            refs,
            args.work,
            args.out,
            separator or RoformerSeparator(),
            args.seed,
        )
        if args.axis == "timbre" and args.timbres:
            chosen = tuple(t.strip() for t in args.timbres.split(",") if t.strip())
            result = timbre_round(*common, chosen)
        elif args.axis == "timbre":
            result = timbre_round(*common)
        else:
            result = master_round(*common)
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(result.listen_dir)
    return 0


def _run(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.final_run import run_track
    from redline.separate import RoformerSeparator, SeparationError

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        folder = run_track(args.track, refs, args.work, args.out, separator or RoformerSeparator())
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(folder)
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
    if args.command == "master":
        return _master(args, separator)
    if args.command == "run":
        return _run(args, separator)
    parser.print_help()
    return 0
