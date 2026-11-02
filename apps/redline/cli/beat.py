"""`redline beat`: the beat studio's commands: scores by seed and the sample library."""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from redline.beat import library, pack

if TYPE_CHECKING:
    from redline.measure.separate import Separator


def _beat(args: argparse.Namespace, separator: "Separator | None" = None) -> int | None:
    """Run a `redline beat` subcommand; None when none was given."""
    if args.beat_command == "write":
        return _beat_write(args)
    if args.beat_command == "render":
        return _beat_render(args)
    if args.beat_command == "measure":
        return _beat_measure(args, separator)
    if args.beat_command == "marks":
        return _beat_marks(args)
    if args.beat_command == "studio":
        return _beat_studio(args)
    handlers = {"list": _sounds_list, "fetch": _sounds_fetch, "scan": _sounds_scan}
    if args.beat_command != "sounds" or args.sounds_command not in handlers:
        return None
    try:
        return handlers[args.sounds_command](args)
    except (library.LibraryError, pack.PackError) as error:
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


def _sounds_scan(args: argparse.Namespace) -> int:
    catalog = args.work / "sounds" / "pack" / "catalog.json"
    print(f"scanning {args.pack} ...", flush=True)
    sounds, problems = pack.scan(args.pack, catalog)
    for line in problems:
        print(f"  cannot read: {line}", flush=True)
    found = pack.counts(sounds)
    print(
        f"{len(sounds)} sounds: "
        + ", ".join(f"{name} {n}" for name, n in found.items() if n)
        + f"; catalogue {catalog}"
    )
    unsure = sum(1 for s in sounds if s.by == "sound")
    if unsure:
        print(f"{unsure} sorted by their sound alone (no telling word in the name)")
    return 0


def _beat_write(args: argparse.Namespace) -> int:
    from redline.beat import handoff
    from redline.beat.recipe import RecipeError

    try:
        result = handoff.write_beat(
            args.root, args.seed, args.genre, args.bpm, args.key, args.force, args.kit
        )
    except (handoff.BeatError, RecipeError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    if isinstance(result, handoff.Pending):
        print(f"waiting for the beatmaker: {result.request}")
        print(f"the draft goes to: {result.draft}")
        return 0
    if isinstance(result, handoff.Failed):
        print(f"seed {args.seed} {result.status}:")
        for reason in result.reasons:
            print(f"  {reason}")
        return 1
    print(f"saved: {result.path}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    return 0


def _beat_render(args: argparse.Namespace) -> int:
    from redline.beat.render import RenderError, render_seed

    try:
        done = render_seed(args.seed, args.root, args.work, args.rate, args.score)
    except (RenderError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(f"{done.folder / 'beat.wav'}: {done.frames / args.rate:.1f} s, stems: {len(done.sounds)}")
    if done.non_commercial:
        print(f"  non-commercial sounds, not for a sold beat: {', '.join(done.non_commercial)}")
    return 0


def _beat_measure(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.beat.render import RenderError, measure_seed
    from redline.cli.sound import SOUND_ERRORS, _references
    from redline.measure.separate import RoformerSeparator

    refs = _references(args)
    if refs is None:
        return 2
    try:
        done = measure_seed(args.seed, refs, args.work, separator or RoformerSeparator())
    except (RenderError, *SOUND_ERRORS) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(done.markdown)
    return 0


def _beat_marks(args: argparse.Namespace) -> int:
    from redline.beat import marks
    from redline.beat.render import RenderError, latest_score, sha256

    if args.list:
        try:
            current = sha256(latest_score(args.root / str(args.seed)))
        except RenderError:
            current = None
        for mark in marks.seed_marks(args.root, args.seed):
            older = "" if mark.get("score_sha256") == current else ", on an older score"
            note = f": {mark['note']}" if mark.get("note") else ""
            print(
                f"bar {mark.get('bar')} {mark.get('verdict')} "
                f"({mark.get('score')}, {mark.get('time')}{older}){note}"
            )
        return 0
    if args.bar is None or args.verdict is None:
        print("redline: a mark needs --bar and good or bad (or --list)", file=sys.stderr)
        return 2
    try:
        mark = marks.add_mark(args.root, args.seed, args.bar, args.verdict, args.note)
    except (marks.MarkError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(f"seed {args.seed} bar {args.bar}: {args.verdict}")
    print(mark["excerpt"])
    return 0


def _beat_studio(args: argparse.Namespace) -> int:
    import secrets
    import webbrowser

    from redline.beat import server
    from redline.beat.render import RenderError, latest_score
    from redline.web.local import browser_file

    try:
        latest_score(args.root / str(args.seed))
        beat = server.BeatStudio(args.seed, args.root, args.work)
        token = secrets.token_urlsafe(32)
        httpd = server.make_server(beat, token, args.port)
    except (RenderError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"beat studio, seed {args.seed}: {url}", flush=True)
    print("Ctrl+C stops it", flush=True)
    if not args.no_open:
        webbrowser.open(browser_file(url, args.work / "beat-studio").as_uri())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
