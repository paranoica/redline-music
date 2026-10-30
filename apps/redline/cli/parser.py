"""The argument parser of every command."""

import argparse
import math
from pathlib import Path

from redline import __version__
from redline.cli.common import LYRICS, LYRICS_BUILD, _inputs

MAX_OFFSET_S = 3600


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="redline",
        description="Move a Suno rap track toward the sound of reference tracks, and write "
        "lyrics in the scene's style.",
    )
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
    opened = studio.add_mutually_exclusive_group(required=True)
    opened.add_argument("--track", type=Path, help="the track from Suno")
    opened.add_argument("--vocal", type=Path, help="your own recorded vocal (with --beat)")
    studio.add_argument("--beat", type=Path, help="the beat the own vocal was recorded over")
    studio.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    _own_offset(studio)
    studio.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    studio.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    studio.add_argument(
        "--no-browser", action="store_true", help="print the address, do not open it"
    )
    _classic(studio)
    run = commands.add_parser(
        "run", help="process takes with the chosen settings and write a result folder for each"
    )
    _inputs(run, "result folder", many=True)
    chosen = run.add_mutually_exclusive_group()
    chosen.add_argument(
        "--settings", type=Path, help="settings saved from the studio (default: the picks)"
    )
    chosen.add_argument("--preset", help="a preset saved in the studio, by its name")
    _classic(run)
    voice = commands.add_parser(
        "voice",
        help="your own recorded vocal over a beat, with settings found by measurement",
    )
    voice.add_argument("--vocal", type=Path, required=True, help="your own recorded vocal")
    voice.add_argument(
        "--beat", type=Path, required=True, help="the beat the vocal was recorded over"
    )
    voice.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    _own_offset(voice)
    voice.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    voice.add_argument("--out", type=Path, default=Path("output"), help="result folder")
    picked = voice.add_mutually_exclusive_group()
    picked.add_argument("--settings", type=Path, help="settings saved from the studio")
    picked.add_argument("--preset", help="a preset saved in the studio, by its name")
    picked.add_argument(
        "--no-auto",
        action="store_true",
        help="render the picks instead of searching for the settings closest to the references",
    )
    _classic(voice)
    takes = commands.add_parser(
        "takes",
        help="rank recorded takes over a beat: timing against the other takes, clipping, "
        "noise, dropouts, beat in the mic",
    )
    takes.add_argument("--beat", type=Path, required=True, help="the beat the takes went over")
    takes.add_argument(
        "--dir", type=Path, required=True, help="the folder of take-NN.wav files of one song"
    )
    takes.add_argument(
        "--ref", type=Path, nargs="+", help="reference files or a folder of them (for --deep)"
    )
    takes.add_argument(
        "--deep",
        action="store_true",
        help="add each take's distance to the references after the auto search (~40 s a take)",
    )
    takes.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    _record(commands)
    _beat(commands)
    _song(commands)
    _flip(commands)
    effects = commands.add_parser(
        "effects", help="the effects of the rack, with their params, ranges and defaults"
    )
    effects.add_argument("--json", action="store_true", help="print the raw list as JSON")
    lyrics = commands.add_parser(
        "lyrics", help="lyrics: write, edit, Suno cards, marks, copy check"
    )
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
    write.add_argument(
        "--delivery",
        choices=("hard", "calm"),
        help="how hard the beat hits (default: measured from --beat)",
    )
    edit = lyrics_commands.add_parser(
        "edit",
        help="change a saved text by an instruction: run again after the writer answers",
    )
    edit.add_argument("change", nargs="?", help="what to change, e.g. 'усиль хук'")
    edit.add_argument("--seed", type=int, required=True, help="the saved text's seed")
    edit.add_argument("--profile", help="the profile the seed was written from")
    edit.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    edit.add_argument("--build", type=Path, default=LYRICS_BUILD, help="the index folder")
    edit.add_argument(
        "--min-words", type=int, default=8, help="shortest stretch counted as a copy (5 or more)"
    )
    edit.add_argument("--force", action="store_true", help="replace a waiting edit")
    suno = lyrics_commands.add_parser(
        "suno", help="a saved text as a Suno card: title, style, settings, tagged lyrics"
    )
    suno.add_argument("--seed", type=int, required=True, help="the saved text's seed")
    suno.add_argument("--version", type=int, help="which version (default: the newest edit)")
    suno.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    suno.add_argument(
        "--stress",
        action="store_true",
        help="capitalise the stressed vowel of line-final words (needs the stress extra)",
    )
    marks = lyrics_commands.add_parser(
        "marks", help="a page on this machine to mark lines of saved texts cringe or good"
    )
    marks.add_argument("--seed", type=int, help="the text to open first (default: the newest)")
    marks.add_argument("--lyrics", type=Path, default=LYRICS, help="the lyrics base folder")
    marks.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    marks.add_argument(
        "--no-browser", action="store_true", help="print the address, do not open it"
    )
    stats = lyrics_commands.add_parser(
        "stats", help="syllables per line and rhyme letters per section"
    )
    stats.add_argument("file", type=Path, help="the text")
    stats.add_argument(
        "--stress", action="store_true", help="find rhymes by stress (needs the stress extra)"
    )
    return parser


def _record(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    record = commands.add_parser(
        "record",
        help="record takes over a beat, hearing yourself through the vocal rack (needs JACK)",
    )
    record.add_argument("--beat", type=Path, help="the beat to record over")
    record.add_argument("--song", help="the song's name: takes go to <takes>/<song>/take-NN.wav")
    record.add_argument(
        "--ref", type=Path, nargs="+", help="references, to analyse the last take for the monitor"
    )
    picked = record.add_mutually_exclusive_group()
    picked.add_argument("--settings", type=Path, help="settings saved from the studio")
    picked.add_argument("--preset", help="a preset saved in the studio, by its name")
    record.add_argument(
        "--calibrate",
        action="store_true",
        help="measure the round trip with a sweep through the singer's path instead",
    )
    record.add_argument(
        "--mode",
        choices=("cup", "loopback", "speakers"),
        help="with --calibrate: a headphone cup at the mic (default), a cable, or speakers",
    )
    record.add_argument(
        "--takes", type=Path, default=Path("input/takes"), help="the folder of the songs' takes"
    )
    record.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")


def _beat(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register `redline beat` and its subcommands."""
    beat = commands.add_parser("beat", help="the beat studio: scores by seed, sample library")
    beat_commands = beat.add_subparsers(dest="beat_command", metavar="command")
    write = beat_commands.add_parser(
        "write",
        help="write a beat score by seed through the beatmaker agent: run it again after the "
        "agent answers",
    )
    write.add_argument("--seed", type=int, required=True, help="the beat's seed, 0 or more")
    write.add_argument("--genre", help="the genre (default: drawn by seed)")
    write.add_argument("--bpm", type=float, help="the tempo (default: drawn in the genre's range)")
    write.add_argument("--key", help="the key, like 'F# minor' (default: drawn by seed)")
    write.add_argument("--root", type=Path, default=Path("private/beats"), help="the beats folder")
    write.add_argument(
        "--force", action="store_true", help="start the seed over; saved scores stay"
    )
    rendered = beat_commands.add_parser(
        "render", help="render a seed's newest score to <work>/beats/<seed>/: beat and stems"
    )
    rendered.add_argument("--seed", type=int, required=True, help="the beat's seed")
    rendered.add_argument("--score", type=Path, help="a score file instead of the newest")
    rendered.add_argument("--rate", type=int, default=48000, help="the render rate")
    measured = beat_commands.add_parser(
        "measure", help="measure a seed's render against the beat stems of references"
    )
    measured.add_argument("--seed", type=int, required=True, help="the beat's seed")
    measured.add_argument(
        "--ref", type=Path, nargs="+", required=True, help="reference files or a folder of them"
    )
    marked = beat_commands.add_parser(
        "marks", help="mark a bar of a seed's newest score good or bad, or list the marks"
    )
    marked.add_argument("--seed", type=int, required=True, help="the beat's seed")
    marked.add_argument("--bar", type=int, help="the bar, from 1")
    marked.add_argument("verdict", nargs="?", choices=("good", "bad"), help="the mark")
    marked.add_argument("--note", default="", help="why, in a few words")
    marked.add_argument("--list", action="store_true", help="print the seed's marks")
    studio = beat_commands.add_parser(
        "studio", help="a page on this machine that edits, checks, saves and renders a seed"
    )
    studio.add_argument("--seed", type=int, required=True, help="the beat's seed")
    studio.add_argument("--port", type=int, default=0, help="the port (default: a free one)")
    studio.add_argument(
        "--no-open",
        "--no-browser",
        dest="no_open",
        action="store_true",
        help="print the address, do not open it",
    )
    for command in (rendered, marked, studio):
        command.add_argument(
            "--root", type=Path, default=Path("private/beats"), help="the beats folder"
        )
    for command in (rendered, measured, studio):
        command.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")
    sounds = beat_commands.add_parser("sounds", help="the sample instruments: list, fetch")
    sounds_commands = sounds.add_subparsers(dest="sounds_command", metavar="command")
    listed = sounds_commands.add_parser(
        "list", help="every instrument with its licence, size and whether it is fetched"
    )
    fetch = sounds_commands.add_parser(
        "fetch", help="download an instrument into <work>/sounds/<name>/, checking every file"
    )
    fetch.add_argument("name", help="the instrument's name, as listed")
    for command in (listed, fetch):
        command.add_argument("--work", type=Path, default=Path("work"), help="cache and run folder")


def _flip_number(text: str) -> int:
    if not text.isascii() or not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a flip number (1 or more)")
    return int(text)


def _flip(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register `redline flip` and its subcommands."""
    flip = commands.add_parser("flip", help="type beats: stems, grid, 808 notes, flips")
    flip_commands = flip.add_subparsers(dest="flip_command", metavar="command")
    analyse = flip_commands.add_parser("analyse", help="separate a beat and find its grid")
    analyse.add_argument("file", type=Path, help="the beat file")
    analyse.add_argument("--bpm", type=float, help="use this tempo instead of the tracker's")
    analyse.add_argument("--half", action="store_true", help="halve the tracker's tempo")
    analyse.add_argument("--double", action="store_true", help="double the tracker's tempo")
    render = flip_commands.add_parser("render", help="render one flip of a beat")
    render.add_argument("file", type=Path, help="the beat file")
    render.add_argument("--flip", type=_flip_number, required=True, help="the flip's number")
    clean = flip_commands.add_parser("clean", help="remove old renders but the newest and used")
    clean.add_argument("file", type=Path, help="the beat file")
    propose = flip_commands.add_parser("propose", help="print an arrangement for a text (JSON)")
    propose.add_argument("file", type=Path, help="the beat file")
    propose.add_argument("--text-seed", type=int, required=True, help="a written text, by seed")
    propose.add_argument("--version", type=int, help="the text's version (default: latest)")


def _song(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register `redline song` and its subcommands."""
    song = commands.add_parser("song", help="songs: one folder of choices per track")
    song_commands = song.add_subparsers(dest="song_command", metavar="command")
    new = song_commands.add_parser("new", help="start a song under private/songs/<name>/")
    new.add_argument("name", help="the song's name, one folder name")
    beat = new.add_mutually_exclusive_group(required=True)
    beat.add_argument("--beat-seed", type=int, help="an own beat, by its seed")
    beat.add_argument("--beat", type=Path, help="a beat file (a type beat)")
    new.add_argument("--suno", type=Path, help="the Suno track, instead of recording takes")
    new.add_argument("--text-seed", type=int, help="a written text, by its seed (latest version)")
    new.add_argument("--ref", type=Path, nargs="+", help="reference files")
    refs = song_commands.add_parser("refs", help="set a song's reference files")
    refs.add_argument("name", help="the song's name")
    refs.add_argument("--ref", type=Path, nargs="+", required=True, help="reference files")
    song_commands.add_parser("list", help="the songs' names")
    show = song_commands.add_parser("show", help="print a song's file")
    show.add_argument("name", help="the song's name")
    studio = song_commands.add_parser("studio", help="open the song's page")
    studio.add_argument("name", help="the song's name")
    studio.add_argument("--port", type=int, default=0, help="port to listen on (default: free)")
    studio.add_argument("--no-browser", action="store_true", help="only print the address")
    job = song_commands.add_parser("job", help="one long piece of work of the page (internal)")
    job.add_argument(
        "kind", choices=("separate", "rank", "auto", "result", "flip_stems", "flip_render")
    )
    job.add_argument("name", help="the song's name")
    job.add_argument("--seed", type=int, default=0, help="chooses the auto mix's variants")
    job.add_argument("--flip", type=_flip_number, help="the flip's number (flip_render)")
    job.add_argument("--result", type=Path, required=True, help="where the JSON result goes")


def check_record(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse a session without --beat and --song, and mixing them with --calibrate."""
    if args.calibrate:
        if args.beat is not None or args.song is not None:
            parser.error("--calibrate takes no --beat or --song")
        return
    if args.beat is None or args.song is None:
        parser.error("record needs --beat and --song (or --calibrate)")
    if args.mode is not None:
        parser.error("--mode goes with --calibrate")
    song = args.song
    if not song.strip() or song in (".", "..") or "/" in song or "\\" in song:
        parser.error(f"--song is a folder name, not {song!r}")


def _classic(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--classic",
        action="store_true",
        help="render with the old Python engine instead of the racks the studio plays live",
    )


def _own_offset(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--offset",
        type=offset_seconds,
        help="seconds to delay the vocal by against the beat; negative drops its start",
    )


def offset_seconds(text: str) -> float:
    """The --offset value: a finite number of seconds, less than an hour either way."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or abs(value) >= MAX_OFFSET_S:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number of seconds between -{MAX_OFFSET_S} and {MAX_OFFSET_S}"
        )
    return value


def check_studio(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse --beat and --offset without --vocal, and --vocal without --beat."""
    if args.vocal is not None and args.beat is None:
        parser.error("--vocal needs --beat")
    if args.vocal is None and args.beat is not None:
        parser.error("--beat goes with --vocal")
    if args.vocal is None and args.offset is not None:
        parser.error("--offset goes with --vocal")
