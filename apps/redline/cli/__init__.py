"""Command line entry point."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from redline.cli.beat import _beat
from redline.cli.lyrics import (
    _lyrics_check,
    _lyrics_edit,
    _lyrics_index,
    _lyrics_marks,
    _lyrics_stats,
    _lyrics_suno,
    _lyrics_write,
)
from redline.cli.parser import build_parser, check_record, check_studio
from redline.cli.record import _record
from redline.cli.song import _song
from redline.cli.sound import (
    _effects,
    _master,
    _measure,
    _run,
    _studio,
    _takes,
    _vocal,
    _voice,
)

if TYPE_CHECKING:
    from redline.measure.separate import Separator

__all__ = ["build_parser", "main"]


def main(argv: Sequence[str] | None = None, separator: "Separator | None" = None) -> int:
    """Run the command line and return the exit code; tests pass their own separator."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "studio":
        check_studio(parser, args)
    if args.command == "record":
        check_record(parser, args)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sound = {
        "measure": _measure,
        "vocal": _vocal,
        "master": _master,
        "run": _run,
        "studio": _studio,
        "voice": _voice,
        "takes": _takes,
        "record": _record,
        "effects": _effects,
    }
    lyrics = {
        "check": _lyrics_check,
        "index": _lyrics_index,
        "write": _lyrics_write,
        "edit": _lyrics_edit,
        "suno": _lyrics_suno,
        "marks": _lyrics_marks,
        "stats": _lyrics_stats,
    }
    if args.command in sound:
        return sound[args.command](args, separator)
    if args.command == "beat":
        done = _beat(args, separator)
        if done is not None:
            return done
    if args.command == "song":
        done = _song(args)
        if done is not None:
            return done
    if args.command == "lyrics" and args.lyrics_command in lyrics:
        return lyrics[args.lyrics_command](args)
    parser.print_help()
    return 0
