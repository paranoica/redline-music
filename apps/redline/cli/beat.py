"""`redline beat`: the beat studio's commands, starting with its sample library."""

import argparse
import sys
from pathlib import Path

from redline.beat import library


def _beat(args: argparse.Namespace) -> int | None:
    """Run a `redline beat` subcommand; None when none was given."""
    handlers = {"list": _sounds_list, "fetch": _sounds_fetch}
    if args.beat_command != "sounds" or args.sounds_command not in handlers:
        return None
    try:
        return handlers[args.sounds_command](args)
    except library.LibraryError as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2


def _status(entry: library.Source, root: Path) -> str:
    if entry.kind != "fetch":
        return "place by hand"
    have = library.present(entry.name, root)
    if have == len(entry.files):
        return "fetched"
    return "not fetched" if have == 0 else f"partial {have}/{len(entry.files)}"


def _sounds_list(args: argparse.Namespace) -> int:
    root = args.work / "sounds"
    for entry in library.load().values():
        size = sum(item.size for item in entry.files)
        shown = f"{size / 1e6:6.1f} MB" if entry.files else "      -  "
        sale = "commercial" if entry.commercial else "non-commercial"
        print(
            f"{entry.name:20} {entry.licence:34} {sale:15} {shown} {_status(entry, root)}",
            flush=True,
        )
    return 0


def _sounds_fetch(args: argparse.Namespace) -> int:
    root = args.work / "sounds"
    result = library.fetch(
        args.name, root, progress=lambda path: print(f"downloading {path}", flush=True)
    )
    print(
        f"{args.name}: {len(result.downloaded)} downloaded, {len(result.present)} already there,"
        f" in {root / args.name}"
    )
    return 0
