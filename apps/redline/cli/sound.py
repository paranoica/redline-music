"""The sound commands: measure, vocal and master rounds, run, voice, studio."""

import argparse
import sys
from typing import TYPE_CHECKING

from redline.core.audio import AudioError

if TYPE_CHECKING:
    from redline.engine.settings import Settings
    from redline.measure.separate import Separator
    from redline.vocal.auto import AutoResult
from redline.cli.common import audio_files


def _measure(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.measure.run import measure_run
    from redline.measure.separate import RoformerSeparator, SeparationError

    refs = audio_files(args.ref)
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
    from redline.measure.separate import RoformerSeparator, SeparationError
    from redline.vocal.rounds import vocal_run

    refs = audio_files(args.ref)
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
    from redline.master.run import VOICES, bass_round, master_round, timbre_round
    from redline.measure.separate import RoformerSeparator, SeparationError

    chosen = None
    if args.timbres is not None:
        if args.axis not in ("timbre", "voice"):
            print("redline: --timbres works only with --axis timbre or voice", file=sys.stderr)
            return 2
        chosen = tuple(t.strip() for t in args.timbres.split(",") if t.strip())
        if not chosen:
            print("redline: --timbres names no timbre", file=sys.stderr)
            return 2
    refs = audio_files(args.ref)
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
        if args.axis == "voice":
            result = timbre_round(*common, chosen or VOICES, "voice")
        elif chosen:
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


def _saved_settings(args: argparse.Namespace) -> "Settings | None":
    from redline.engine import settings as chain_settings

    if args.preset is not None:
        return chain_settings.load_preset(args.work / "studio" / "presets", args.preset)
    return chain_settings.load(args.settings) if args.settings else None


def _print_auto(found: "AutoResult") -> None:
    for key in ("vocal_db", "comp_threshold_db", "drive_db"):
        print(f"{key}: {getattr(found.settings, key)}")
    print(f"distance: {found.distance_before:.2f} → {found.distance_after:.2f}", flush=True)
    if found.at_edge:
        print(f"at the edge of its range: {', '.join(found.at_edge)}", flush=True)


def _run(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.master.run import run_track
    from redline.measure.separate import RoformerSeparator, SeparationError

    refs = audio_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    takes = audio_files(args.track)
    if not takes:
        print("redline: no track audio files given", file=sys.stderr)
        return 2
    try:
        chosen = _saved_settings(args)
    except (ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    separator = separator or RoformerSeparator()
    failed = 0
    for take in takes:
        try:
            folder = run_track(take, refs, args.work, args.out, separator, chosen)
        except (AudioError, SeparationError, ValueError, OSError) as error:
            print(f"redline: {take.name}: {error}", file=sys.stderr)
            failed += 1
            continue
        print(folder, flush=True)
    if failed:
        if len(takes) > 1:
            print(f"redline: {failed} of {len(takes)} takes failed", file=sys.stderr)
        return 2
    return 0


def _voice(args: argparse.Namespace, separator: "Separator | None") -> int:
    from dataclasses import asdict

    from redline.master.run import track_engine, write_result
    from redline.measure.run import measure_own
    from redline.measure.separate import RoformerSeparator, SeparationError
    from redline.vocal.auto import auto_settings
    from redline.vocal.rounds import check_folders

    refs = audio_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    found = None
    try:
        chosen = _saved_settings(args)
        check_folders(args.work, args.out)
        inputs = measure_own(
            args.vocal,
            args.beat,
            refs,
            args.work,
            separator or RoformerSeparator(),
            0.0 if args.offset is None else args.offset,
        )
        engine = track_engine(inputs)
        if chosen is None and not args.no_auto:
            found = auto_settings(engine, inputs.refs)
            chosen = found.settings
        extra = None if found is None else {"auto": asdict(found)}
        folder = write_result(inputs, engine, args.out, args.vocal.stem, chosen, extra)
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(folder, flush=True)
    if found is not None:
        _print_auto(found)
    return 0


def _studio(args: argparse.Namespace, separator: "Separator | None") -> int:
    import hashlib
    import secrets
    import webbrowser

    from redline.core.audio import decode
    from redline.engine import settings as chain_settings
    from redline.master.run import track_engine
    from redline.measure.run import measure_inputs, measure_own
    from redline.measure.separate import RoformerSeparator, SeparationError
    from redline.studio import server as studio_server
    from redline.vocal.auto import auto_settings
    from redline.web.local import browser_file

    refs = audio_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        separator = separator or RoformerSeparator()
        if args.vocal is None:
            named = args.track
            inputs = measure_inputs(args.track, refs, args.work, separator)
            source = str(args.track.resolve())
        else:
            named = args.vocal
            offset = 0.0 if args.offset is None else args.offset
            inputs = measure_own(args.vocal, args.beat, refs, args.work, separator, offset)
            # the offset is left out, so settings tuned on a pair survive moving it
            source = f"{args.vocal.resolve()}|{args.beat.resolve()}"
        engine = track_engine(inputs)
        stems = inputs.track_stems
        digest = hashlib.sha256(source.encode()).hexdigest()[:8]
        track_key = f"{named.stem}-{digest}"
        saved = args.work / "studio" / track_key / "settings.json"
        if args.vocal is not None and not saved.exists():
            found = auto_settings(engine, inputs.refs)
            saved.parent.mkdir(parents=True, exist_ok=True)
            chain_settings.save(found.settings, saved)
            _print_auto(found)
        references = []
        for path in refs:
            decoded = decode(path)
            references.append((path.stem, decoded.samples, decoded.rate))
        studio = studio_server.Studio(
            engine,
            stems.vocal + stems.beat,
            args.work / "studio",
            track_key,
            references,
        )
        token = secrets.token_urlsafe(32)
        httpd = studio_server.make_server(studio, token, args.port)
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"studio: {url}", flush=True)
    print("Ctrl+C stops it", flush=True)
    if not args.no_browser:
        webbrowser.open(browser_file(url, args.work / "studio").as_uri())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
