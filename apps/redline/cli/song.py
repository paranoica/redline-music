"""`redline song`: new, list and show for the songs kept under private/songs/."""

import argparse
import json
import sys

from redline.song import model


def _song(args: argparse.Namespace) -> int | None:
    """Run a `redline song` subcommand; None when none was given."""
    handlers = {"new": _new, "list": _list, "show": _show}
    handler = handlers.get(args.song_command)
    if handler is None:
        return None
    try:
        return handler(args)
    except model.SongError as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2


def _new(args: argparse.Namespace) -> int:
    if args.beat_seed is not None:
        beat = {"own": args.beat_seed}
    else:
        beat = {"file": model.relative_to_root(args.beat)}
    suno = None if args.suno is None else model.relative_to_root(args.suno)
    refs = tuple(model.relative_to_root(p) for p in args.ref or ())
    song = model.new_song(args.name, beat, suno, refs)
    for stored in (beat.get("file"), suno, *refs):
        if stored is not None:
            model.resolve_inside(stored)
    try:
        saved = model.save(song, 0)
    except model.StaleRev:
        raise model.SongError(f"{args.name}: the song exists already") from None
    print(f"song {saved.name}: {model.song_dir(saved.name) / 'song.json'}", flush=True)
    return 0


def _list(args: argparse.Namespace) -> int:
    for name in model.list_songs():
        print(name)
    return 0


def _show(args: argparse.Namespace) -> int:
    print(json.dumps(model.load(args.name).to_json(), indent=2, ensure_ascii=False))
    return 0
