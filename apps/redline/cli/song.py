"""`redline song`: new, list, show, the page (studio) and its jobs."""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from redline.cli.sound import SOUND_ERRORS
from redline.song import model

if TYPE_CHECKING:
    from redline.measure.run import Inputs


def _song(args: argparse.Namespace) -> int | None:
    """Run a `redline song` subcommand; None when none was given."""
    handlers = {
        "new": _new,
        "refs": _refs,
        "list": _list,
        "show": _show,
        "studio": _studio,
        "job": _job,
    }
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
    if args.text_seed is not None:
        song = replace(song, text=_latest_text(args.text_seed))
    for stored in (beat.get("file"), suno, *refs):
        if stored is not None:
            model.resolve_inside(stored)
    try:
        saved = model.save(song, 0)
    except model.StaleRev:
        raise model.SongError(f"{args.name}: the song exists already") from None
    print(f"song {saved.name}: {model.song_dir(saved.name) / 'song.json'}", flush=True)
    return 0


def _latest_text(seed: int) -> dict[str, int]:
    from redline.lyrics.write.generate import versions

    folder = model.resolve_inside(f"private/lyrics/generated/{seed}", where="--text-seed")
    have = versions(folder) if folder.is_dir() else {}
    if not have:
        raise model.SongError(f"--text-seed: seed {seed} has no text")
    return {"seed": seed, "version": max(have)}


def _refs(args: argparse.Namespace) -> int:
    song = model.load(args.name)
    refs = tuple(model.relative_to_root(p) for p in args.ref)
    for stored in refs:
        model.resolve_inside(stored, where="--ref")
    saved = model.save(replace(song, references=refs), song.rev)
    print(f"song {saved.name}: {len(refs)} references", flush=True)
    return 0


def _list(args: argparse.Namespace) -> int:
    for name in model.list_songs():
        print(name)
    return 0


def _show(args: argparse.Namespace) -> int:
    print(json.dumps(model.load(args.name).to_json(), indent=2, ensure_ascii=False))
    return 0


def _studio(args: argparse.Namespace) -> int:
    import secrets
    import signal
    import webbrowser

    from redline.song import server
    from redline.web.local import browser_file

    model.load(args.name)
    token = secrets.token_urlsafe(32)
    app = server.SongApp(args.name)
    httpd = server.make_server(app, token, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"song {args.name}: {url}", flush=True)
    print("Ctrl+C stops it", flush=True)

    class Stop(Exception):
        pass

    def stop(signum: int, frame: Any) -> None:
        raise Stop

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        if not args.no_browser:
            webbrowser.open(browser_file(url, model.resolve_inside("work") / "studio").as_uri())
        httpd.serve_forever()
    except Stop:
        pass
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        server.close_all(httpd, app.rec, app.jobs)
    return 0


def _job(args: argparse.Namespace) -> int:
    from redline.core.private import write_private
    from redline.takes.rank import TakesError

    song = model.load(args.name)
    work = model.resolve_inside("work")
    jobs = {"separate": _job_separate, "rank": _job_rank, "auto": _job_auto, "result": _job_result}
    try:
        result = jobs[args.kind](song, work, args.seed)
    except (*SOUND_ERRORS, TakesError, model.SongError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    write_private(args.result, json.dumps(result, ensure_ascii=False))
    return 0


def _measured(inputs: model.MixInputs, work: Path) -> "Inputs":
    from redline.measure.run import measure_inputs, measure_own
    from redline.measure.separate import RoformerSeparator

    refs, separator = list(inputs.refs), RoformerSeparator()
    if inputs.beat is None:
        return measure_inputs(inputs.voice, refs, work, separator)
    return measure_own(inputs.voice, inputs.beat, refs, work, separator)


def _job_separate(song: model.Song, work: Path, seed: int) -> dict[str, Any]:
    inputs = model.mix_inputs(song)
    _measured(inputs, work)
    return {"kind": "separate", "key": inputs.key()}


def _job_rank(song: model.Song, work: Path, seed: int) -> dict[str, Any]:
    from redline.core.private import make_private_dir, write_private
    from redline.record.takes import file_sha256
    from redline.takes.rank import rank_folder
    from redline.takes.report import ranking_json, report_markdown, voice_command

    if "takes" not in song.voice:
        raise model.SongError("the song's voice is a Suno track: there are no takes to rank")
    beat = model.beat_file(song)
    folder = model.resolve_inside(song.voice["takes"])
    ranking = rank_folder(beat, folder, None, file_sha256(beat))
    best = ranking.takes[0].path if ranking.takes else None
    voice = None if best is None else voice_command(best, beat, [])
    out = make_private_dir(work / "songs" / song.name, model.repo_root())
    data = json.dumps(ranking_json(ranking, voice), ensure_ascii=False, indent=1)
    write_private(out / "ranking.json", data)
    write_private(out / "report.md", report_markdown(ranking, song.name, voice))
    return {"kind": "rank", "takes": len(ranking.takes), "skipped": len(ranking.skips)}


def song_settings(song: model.Song, work: Path) -> Any:
    """The settings in the song's settings file; None when it has none yet."""
    from redline.engine import settings as chain

    if song.mix["settings"] is not None:
        path = model.resolve_inside(song.mix["settings"], where="mix.settings")
        if path.is_file():
            return chain.load(path)
    return None


def _job_auto(song: model.Song, work: Path, seed: int) -> dict[str, Any]:
    from redline.core.private import make_private_dir, write_private
    from redline.engine import settings as chain
    from redline.engine.track import track_engine
    from redline.measure.separate import RoformerSeparator
    from redline.song.variants import auto_with_variants

    inputs = model.mix_inputs(song)
    measured = _measured(inputs, work)
    base = None
    if song.mix["settings"] is not None:
        path = model.resolve_inside(song.mix["settings"], where="mix.settings")
        base = chain.load(path) if path.is_file() else None
    found = auto_with_variants(
        track_engine(measured), measured.refs, seed, base, separator=RoformerSeparator()
    )
    found["seed"] = seed
    found["key"] = inputs.key()
    out = make_private_dir(work / "songs" / song.name, model.repo_root())
    write_private(out / "variants.json", json.dumps(found, ensure_ascii=False, indent=1))
    return {"kind": "auto", "seed": seed}


def _job_result(song: model.Song, work: Path, seed: int) -> dict[str, Any]:
    from redline.core.private import make_private_dir
    from redline.engine.track import track_engine
    from redline.master.run import write_result
    from redline.output import check_folders

    inputs = model.mix_inputs(song)
    out = make_private_dir(model.resolve_inside(f"output/{song.name}"), model.repo_root())
    check_folders(work, out)
    measured = _measured(inputs, work)
    folder = write_result(
        measured, track_engine(measured), out, song.name, song_settings(song, work)
    )
    return {"kind": "result", "folder": model.relative_to_root(folder)}
