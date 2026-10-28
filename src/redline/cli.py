"""Command line entry point."""

import argparse
import functools
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline import __version__
from redline.audio import AudioError

if TYPE_CHECKING:
    from redline.separate import Separator

AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff"}
LYRICS = Path("private/lyrics")
LYRICS_BUILD = LYRICS / "build"


def _inputs(command: argparse.ArgumentParser, out_help: str | None = None) -> None:
    command.add_argument("--track", type=Path, required=True, help="the track from Suno")
    command.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    command.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    if out_help is not None:
        command.add_argument("--out", type=Path, default=Path("output"), help=out_help)


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level parser with its subcommands."""
    parser = argparse.ArgumentParser(prog="redline", description=__doc__)
    parser.add_argument("--version", action="version", version=f"redline {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    measure = commands.add_parser(
        "measure", help="measure a track against references and write a report (stage 1)"
    )
    _inputs(measure)
    vocal = commands.add_parser(
        "vocal", help="process the vocal at every dirt level and write blind listening files"
    )
    _inputs(vocal, "listening files folder")
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
    _inputs(master, "listening files folder")
    master.add_argument("--seed", type=int, help="seed of the letter shuffle")
    master.add_argument(
        "--axis",
        choices=("master", "timbre", "808"),
        default="master",
        help="what the variants change: the master's loudness, the vocal's timbre, the 808",
    )
    master.add_argument(
        "--timbres",
        help="timbre variants, comma separated; join effects with +: 'octave down + doubles'",
    )
    run = commands.add_parser(
        "run", help="process a track with the chosen settings and write the result folder"
    )
    _inputs(run, "result folder")
    lyrics = commands.add_parser("lyrics", help="lyrics: copy check and line form")
    lyrics_commands = lyrics.add_subparsers(dest="lyrics_command", metavar="command")
    check = lyrics_commands.add_parser(
        "check", help="exit 1 if the text copies 8 or more words in a row from the base"
    )
    check.add_argument("file", type=Path, help="the text to check")
    check.add_argument("--build", type=Path, default=LYRICS_BUILD, help="the index folder")
    check.add_argument(
        "--min-words", type=int, default=8, help="shortest stretch counted as a copy (5 or more)"
    )
    build = lyrics_commands.add_parser(
        "index", help="build the copy-check digests and the style profiles from the lyrics base"
    )
    build.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    build.add_argument("--build", type=Path, default=LYRICS_BUILD, help="where to write")
    build.add_argument(
        "--without-mine", action="store_true", help="leave the owner's texts out of the check"
    )
    build.add_argument(
        "--force", action="store_true", help="replace an index built from more texts"
    )
    build.add_argument(
        "--stress", action="store_true", help="rhymes by stress in profiles (stress extra)"
    )
    write = lyrics_commands.add_parser(
        "write",
        help="write a new text by seed: run it again after the writer answers (exit 3 = waiting)",
    )
    write.add_argument("--seed", type=int, required=True, help="the text's seed, 0 or more")
    write.add_argument("--topic", help="what the text is about; drawn by seed if left out")
    write.add_argument(
        "--profile", help="the profile to write from (default: target, else working)"
    )
    write.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    write.add_argument("--build", type=Path, default=LYRICS_BUILD, help="the index folder")
    write.add_argument(
        "--min-words", type=int, default=8, help="shortest stretch counted as a copy (5 or more)"
    )
    write.add_argument("--force", action="store_true", help="start a pending or failed seed over")
    stats = lyrics_commands.add_parser(
        "stats", help="syllables per line and rhyme letters per section"
    )
    stats.add_argument("file", type=Path, help="the text")
    stats.add_argument(
        "--stress", action="store_true", help="find rhymes by stress (needs the stress extra)"
    )
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
    from redline.final_run import bass_round, master_round, timbre_round
    from redline.separate import RoformerSeparator, SeparationError

    chosen = None
    if args.timbres is not None:
        if args.axis != "timbre":
            print("redline: --timbres works only with --axis timbre", file=sys.stderr)
            return 2
        chosen = tuple(t.strip() for t in args.timbres.split(",") if t.strip())
        if not chosen:
            print("redline: --timbres names no timbre", file=sys.stderr)
            return 2
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
        if chosen:
            result = timbre_round(*common, chosen)
        elif args.axis == "timbre":
            result = timbre_round(*common)
        elif args.axis == "808":
            result = bass_round(*common)
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


def _lyrics_check(args: argparse.Namespace) -> int:
    from redline.lyrics.fingerprint import FingerprintIndex
    from redline.lyrics.parse import LyricsError, parse_text

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
    from redline.lyrics import stress
    from redline.lyrics.fingerprint import KEY_FILE, FingerprintIndex, new_key
    from redline.lyrics.parse import LyricsError, parse_text, read_artists, read_track
    from redline.lyrics.profile import build_profiles, dumps, read_mix, read_names

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


def _lyrics_write(args: argparse.Namespace) -> int:
    import re

    from redline.lyrics import stress
    from redline.lyrics.fingerprint import DEFAULT_MIN_WORDS, RUN, FingerprintIndex
    from redline.lyrics.generate import Failed, Pending, Session
    from redline.lyrics.parse import LyricsError, read_track
    from redline.lyrics.profile import load_profile
    from redline.lyrics.writer import HandoffWriter

    try:
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
        session = Session(
            folder, profile, name, index, mine, HandoffWriter(folder), mark, args.min_words, style
        )
        result = session.run(args.seed, args.topic, args.force)
    except (LyricsError, OSError, ValueError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    if isinstance(result, Pending):
        print(f"waiting for the writer: {result.request}")
        print(f"the draft goes to: {result.draft}")
        return 3
    if isinstance(result, Failed):
        print(f"seed {args.seed} {result.status}:")
        for reason in result.reasons:
            print(f"  {reason}")
        return 1
    print(result.text, end="")
    return 0


def _lyrics_stats(args: argparse.Namespace) -> int:
    from redline.lyrics import stress
    from redline.lyrics.parse import parse_text
    from redline.lyrics.rhyme import last_word, letters
    from redline.lyrics.syllables import count_syllables

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
    if args.command == "lyrics" and args.lyrics_command == "check":
        return _lyrics_check(args)
    if args.command == "lyrics" and args.lyrics_command == "index":
        return _lyrics_index(args)
    if args.command == "lyrics" and args.lyrics_command == "write":
        return _lyrics_write(args)
    if args.command == "lyrics" and args.lyrics_command == "stats":
        return _lyrics_stats(args)
    parser.print_help()
    return 0
