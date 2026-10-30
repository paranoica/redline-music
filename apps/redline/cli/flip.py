"""`redline flip`: analyse a type beat, render and clean its flips, propose an arrangement."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from redline.song import model


def _flip(args: argparse.Namespace) -> int | None:
    """Run a `redline flip` subcommand; None when none was given."""
    handlers = {
        "analyse": _analyse,
        "render": _render,
        "clean": _clean,
        "propose": _propose,
    }
    handler = handlers.get(args.flip_command)
    if handler is None:
        return None
    try:
        return handler(args)
    except ValueError as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2


def _target(args: argparse.Namespace) -> tuple[Any, Path]:
    from redline.flip import pipeline

    root = model.repo_root()
    line = pipeline.lineage(model.relative_to_root(Path.cwd() / args.file, root), root)
    return line, pipeline.flip_dir(root, line.sha8)


def _analyse(args: argparse.Namespace) -> int:
    from redline.flip import pipeline

    line, _ = _target(args)
    root = model.repo_root()
    done = pipeline.analyse(
        model.resolve_inside(line.source, root),
        model.resolve_inside("work", root),
        root=root,
        bpm=args.bpm,
        half=args.half,
        double=args.double,
    )
    count = len(done["bar_starts_s"])
    print(f"{done['bpm']:.2f} BPM, {count} bars, {done['counts']['notes']} notes", flush=True)
    print(f"work/flips/{done['sha8']}", flush=True)
    return 0


def _render(args: argparse.Namespace) -> int:
    from redline.flip import pipeline

    _, folder = _target(args)
    print(pipeline.render(folder, args.flip), flush=True)
    return 0


def _used(sha8: str) -> set[int]:
    from redline.flip.pipeline import FLIP_RENDER

    used = set()
    for name in model.list_songs():
        beat = model.load(name).beat.get("file", "")
        found = FLIP_RENDER.fullmatch(beat)
        if found and found.group(1) == sha8:
            used.add(int(found.group(2)))
    return used


def _clean(args: argparse.Namespace) -> int:
    from redline.flip import pipeline

    line, folder = _target(args)
    gone = pipeline.clean(folder, _used(line.sha8)) if folder.is_dir() else []
    print(f"removed {len(gone)} renders: {gone}", flush=True)
    return 0


def _propose(args: argparse.Namespace) -> int:
    from redline.flip import pipeline
    from redline.lyrics.write.generate import versions

    line, folder = _target(args)
    seed = model.resolve_inside(f"private/lyrics/generated/{args.text_seed}", where="--text-seed")
    have = versions(seed) if seed.is_dir() else {}
    version = args.version if args.version is not None else max(have, default=0)
    if version not in have:
        raise model.SongError(f"--text-seed: seed {args.text_seed} has no version {version}")
    text = have[version].read_text(encoding="utf-8")
    source = model.resolve_inside(line.source)
    blocks = pipeline.propose_blocks(source, folder, text)
    print(json.dumps(blocks, ensure_ascii=False, indent=2), flush=True)
    return 0
