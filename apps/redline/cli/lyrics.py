"""The lyrics commands: check, index, write, suno, stats."""

import argparse
import functools
import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _lyrics_check(args: argparse.Namespace) -> int:
    from redline.lyrics.base.fingerprint import FingerprintIndex
    from redline.lyrics.text.parse import LyricsError, parse_text

    try:
        index = FingerprintIndex.load(args.build)
        track = parse_text(args.file.read_text(encoding="utf-8"))
        matches = index.check(track, args.min_words)
    except (LyricsError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    for match in matches:
        where = f"lines {match.first_line}-{match.last_line}"
        if match.first_line == match.last_line:
            where = f"line {match.first_line}"
        print(f"copy: {match.words} words in a row, {where}")
        for line in match.lines:
            print(f"  {line}")
    if not matches:
        print("no copies")
    return 1 if matches else 0


def _git_would_track(folder: Path) -> bool:
    """Whether a file in the folder would be tracked by the git repository it sits in."""
    import subprocess

    probe = folder.resolve() / "key"
    existing = next(p for p in probe.parents if p.exists())
    top = subprocess.run(
        ["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        return False
    ignored = subprocess.run(
        ["git", "-C", top.stdout.strip(), "check-ignore", "-q", "--no-index", str(probe)],
        capture_output=True,
        check=False,
    )
    return ignored.returncode != 0


def _lyrics_index(args: argparse.Namespace) -> int:
    from redline.lyrics.base.fingerprint import KEY_FILE, FingerprintIndex, new_key
    from redline.lyrics.base.profile import build_profiles, dumps, read_mix, read_names
    from redline.lyrics.text import stress
    from redline.lyrics.text.parse import LyricsError, parse_text, read_artists, read_track

    others = args.lyrics / "others"
    try:
        if _git_would_track(args.build):
            # the key with the digests gives the texts back, and profiles name the artists
            raise LyricsError(f"{args.build} is not ignored by git; build into private/")
        if not others.is_dir():
            raise LyricsError(f"no lyrics base in {args.lyrics}: {others} is missing")
        artists = read_artists(others / "artists.yaml")
        base = {
            folder.name: [read_track(p) for p in sorted(folder.glob("*.txt"))]
            for folder in sorted(others.iterdir())
            if folder.is_dir()
        }
        mine = [read_track(p, mine=True) for p in sorted((args.lyrics / "mine").glob("*.txt"))]
        pairs = [(slug, t) for slug, tracks in base.items() for t in tracks]
        if not args.without_mine:
            pairs += [("mine", t) for t in mine]
        # written texts are checked against, never learned from
        made = sorted((args.lyrics / "generated").glob("*/text.txt"))
        pairs += [("generated", parse_text(p.read_text(encoding="utf-8"))) for p in made]
        mix = read_mix(args.lyrics / "mix.yaml")
        key_path = args.build / KEY_FILE
        key = key_path.read_bytes() if key_path.exists() else new_key()
        index = FingerprintIndex.build(pairs, key)
        # profiles stress line-final words only, and the same words come back often
        mark = functools.cache(stress.load_stresser()) if args.stress else str
        names = read_names(args.lyrics / "names.txt")
        profiles = build_profiles(
            base, artists, mine, mark=mark, stress=args.stress, mix=mix, more_names=names
        )
        index.save(args.build, force=args.force)
        folder = args.build / "profiles"
        folder.mkdir(exist_ok=True)
        for stale in folder.glob("*.json"):
            if stale.stem not in profiles:
                stale.unlink()
        for name, profile in profiles.items():
            (folder / f"{name}.json").write_text(dumps(profile), encoding="utf-8")
    except (LyricsError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(f"{index.n_texts} texts, {len(index.digests)} digests, {len(profiles)} profiles")
    return 0


def _lazy_stresser(stress: Any) -> Callable[[str], str]:
    """Load the stress model on first use only: printing a saved text needs none."""
    import functools

    @functools.cache
    def model() -> Callable[[str], str]:
        return stress.load_stresser()

    @functools.cache
    def mark(text: str) -> str:
        return model()(text)

    return mark


def _asked_beat(args: argparse.Namespace) -> tuple[float | None, int | None, str | None]:
    from redline.lyrics.text.parse import LyricsError
    from redline.lyrics.write.beat import (
        beat_bars,
        beat_bpm,
        beat_brightness,
        delivery_of,
        half_time,
    )

    if args.beat:
        bpm = beat_bpm(args.beat)
        bars = beat_bars(args.beat, bpm)
        brightness = beat_brightness(args.beat)
        delivery = args.delivery or delivery_of(brightness)
        print(
            f"beat: {bpm:g} BPM at half time, {bpm * 2:g} at full, {bars} bars, "
            f"brightness {brightness:.0f} Hz, delivery {delivery}"
        )
        return bpm, bars, delivery
    if args.bpm is None:
        if args.delivery:
            raise LyricsError("--delivery goes with --beat or --bpm")
        return None, None, None
    if not (math.isfinite(args.bpm) and args.bpm > 0):
        raise LyricsError("--bpm takes a positive number")
    return half_time((args.bpm,)), None, args.delivery


def _session(args: argparse.Namespace) -> Any:
    """The seed's session, with its profile, index, the owner's texts and the style notes."""
    import re

    from redline.lyrics.base.fingerprint import DEFAULT_MIN_WORDS, RUN, FingerprintIndex
    from redline.lyrics.base.profile import load_profile
    from redline.lyrics.text import stress
    from redline.lyrics.text.parse import LyricsError, read_track
    from redline.lyrics.write.generate import Session
    from redline.lyrics.write.writer import HandoffWriter

    if args.seed < 0:
        raise LyricsError("a seed is a whole number from 0")
    if not RUN <= args.min_words <= DEFAULT_MIN_WORDS:
        # a looser check would let a saved text hold a longer copy than the owner allows
        raise LyricsError(f"--min-words takes {RUN} to {DEFAULT_MIN_WORDS}")
    name = args.profile or (
        "target" if (args.build / "profiles" / "target.json").exists() else "working"
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise LyricsError(f"no such profile: {name}")
    folder = args.lyrics / "generated" / str(args.seed)
    if _git_would_track(folder):
        raise LyricsError(f"{folder} is not ignored by git; keep the lyrics base in private/")
    profile = load_profile(args.build / "profiles" / f"{name}.json")
    index = FingerprintIndex.load(args.build)
    mine = [read_track(p, mine=True) for p in sorted((args.lyrics / "mine").glob("*.txt"))]
    if "stress" not in profile["meta"]:
        raise LyricsError(f"profile {name} is from an older index; run lyrics index again")
    mark = _lazy_stresser(stress) if profile["meta"]["stress"] else str
    notes = args.lyrics / "style.md"
    style = notes.read_text(encoding="utf-8") if notes.exists() else ""
    return Session(
        folder, profile, name, index, mine, HandoffWriter(folder), mark, args.min_words, style
    )


def _report(seed: int, result: Any) -> int:
    from redline.lyrics.write.generate import Failed, Pending

    if isinstance(result, Pending):
        print(f"waiting for the writer: {result.request}")
        print(f"the draft goes to: {result.draft}")
        return 3
    if isinstance(result, Failed):
        print(f"seed {seed} {result.status}:")
        for reason in result.reasons:
            print(f"  {reason}")
        return 1
    print(result.text, end="")
    return 0


def _lyrics_edit(args: argparse.Namespace) -> int:
    from redline.lyrics.text.parse import LyricsError
    from redline.lyrics.write.generate import Editor

    try:
        result = Editor(_session(args)).run(args.seed, args.change, args.force)
    except (LyricsError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    return _report(args.seed, result)


def _lyrics_write(args: argparse.Namespace) -> int:
    from redline.lyrics.text.parse import LyricsError

    try:
        session = _session(args)
        result = session.run(args.seed, args.topic, args.force, *_asked_beat(args))
    except (LyricsError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    return _report(args.seed, result)


def _lyrics_suno(args: argparse.Namespace) -> int:
    from redline.lyrics.suno import card
    from redline.lyrics.text import stress
    from redline.lyrics.text.parse import LyricsError, parse_text
    from redline.lyrics.write.generate import versions
    from redline.lyrics.write.recipe import recipe_from_json

    folder = args.lyrics / "generated" / str(args.seed)
    try:
        have = versions(folder)
        if not have:
            raise LyricsError(f"seed {args.seed} has no saved text")
        version = max(have) if args.version is None else args.version
        if version not in have:
            raise LyricsError(f"seed {args.seed} has versions {', '.join(map(str, have))}")
        track = parse_text(have[version].read_text(encoding="utf-8"))
        recipe = recipe_from_json((folder / "recipe.json").read_text(encoding="utf-8"))
        mark = _lazy_stresser(stress) if args.stress else None
        own = args.lyrics / "suno-style.txt"
        lines = own.read_text(encoding="utf-8").splitlines() if own.exists() else []
        style = ", ".join(line.strip() for line in lines if line.strip())
        named = [folder / f"title-{v}.txt" for v in range(version, 1, -1)] + [folder / "title.txt"]
        found = next((p for p in named if p.exists()), None)
        title = found.read_text(encoding="utf-8").strip() if found else None
        text = card(track, mark, style or None, recipe, title or None)
        (folder / "suno.md").write_text(text, encoding="utf-8")
    except (LyricsError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(text, end="")
    return 0


def _lyrics_marks(args: argparse.Namespace) -> int:
    import secrets
    import webbrowser

    from redline.lyrics.marks import server as marks_server
    from redline.web.local import browser_file

    generated = args.lyrics / "generated"
    if not generated.is_dir():
        print(f"redline: no saved texts in {generated}", file=sys.stderr)
        return 2
    token = secrets.token_urlsafe(32)
    try:
        httpd = marks_server.make_server(generated, token, args.port)
    except OSError as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    if args.seed is not None:
        url += f"#seed={args.seed}"
    print(f"marks: {url}", flush=True)
    print("Ctrl+C stops it", flush=True)
    if not args.no_browser:
        webbrowser.open(browser_file(url, generated).as_uri())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def _lyrics_stats(args: argparse.Namespace) -> int:
    from redline.lyrics.text import stress
    from redline.lyrics.text.parse import parse_text
    from redline.lyrics.text.rhyme import last_word, letters
    from redline.lyrics.text.syllables import count_syllables

    try:
        track = parse_text(args.file.read_text(encoding="utf-8"))
        marked = stress.load_stresser() if args.stress else str
    except (ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    for section in track.sections:
        print(f"[{section.name or section.kind}]")
        sung = [line for line in section.lines if line.plain]
        rhyme = letters([last_word(marked(line.plain)) for line in sung])
        for line, letter in zip(sung, rhyme, strict=True):
            print(f"{count_syllables(line.text):>3} {letter}  {line.text}")
    return 0
