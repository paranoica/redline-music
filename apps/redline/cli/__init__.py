"""Command line entry point."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from redline.cli.lyrics import (
    _lyrics_check,
    _lyrics_edit,
    _lyrics_index,
    _lyrics_stats,
    _lyrics_suno,
    _lyrics_write,
)
from redline.cli.parser import build_parser
from redline.cli.sound import _master, _measure, _run, _studio, _vocal

if TYPE_CHECKING:
    from redline.measure.separate import Separator

__all__ = ["build_parser", "main"]


def main(argv: Sequence[str] | None = None, separator: "Separator | None" = None) -> int:
    """Run the command line and return the exit code; tests pass their own separator."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sound = {
        "measure": _measure,
        "vocal": _vocal,
        "master": _master,
        "run": _run,
        "studio": _studio,
    }
    lyrics = {
        "check": _lyrics_check,
        "index": _lyrics_index,
        "write": _lyrics_write,
        "edit": _lyrics_edit,
        "suno": _lyrics_suno,
        "stats": _lyrics_stats,
    }
    if args.command in sound:
        return sound[args.command](args, separator)
    if args.command == "lyrics" and args.lyrics_command in lyrics:
        return lyrics[args.lyrics_command](args)
    parser.print_help()
    return 0
