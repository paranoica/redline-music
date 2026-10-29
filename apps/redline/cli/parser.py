"""The argument parser of every command."""

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass
from redline import __version__
from redline.cli.common import LYRICS, LYRICS_BUILD, _inputs


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
        choices=("master", "timbre", "voice", "808"),
        default="master",
        help="what the variants change: the master's loudness, the vocal's timbre, the voice "
        "itself (formants, hard tune, octave up), the 808",
    )
    master.add_argument(
        "--timbres",
        help="timbre variants, comma separated; join effects with +: 'octave down + doubles'",
    )
    studio = commands.add_parser(
        "studio", help="a page on this machine that plays the track and re-renders it live"
    )
    _inputs(studio, None)
    studio.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    studio.add_argument(
        "--no-browser", action="store_true", help="print the address, do not open it"
    )
    run = commands.add_parser(
        "run", help="process a track with the chosen settings and write the result folder"
    )
    _inputs(run, "result folder")
    run.add_argument(
        "--settings", type=Path, help="settings saved from the studio (default: the picks)"
    )
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
    beat = write.add_mutually_exclusive_group()
    beat.add_argument("--beat", type=Path, help="the beat the text is for; its tempo is measured")
    beat.add_argument("--bpm", type=float, help="the beat's tempo, when it is known")
    suno = lyrics_commands.add_parser(
        "suno", help="a saved text as a Suno card: title, style, settings, tagged lyrics"
    )
    suno.add_argument("--seed", type=int, required=True, help="the saved text's seed")
    suno.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    suno.add_argument(
        "--stress",
        action="store_true",
        help="capitalise the stressed vowel of line-final words (needs the stress extra)",
    )
    stats = lyrics_commands.add_parser(
        "stats", help="syllables per line and rhyme letters per section"
    )
    stats.add_argument("file", type=Path, help="the text")
    stats.add_argument(
        "--stress", action="store_true", help="find rhymes by stress (needs the stress extra)"
    )
    return parser
