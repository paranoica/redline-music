"""The sound commands: measure, vocal and master rounds, run, studio."""

import argparse
import sys
from typing import TYPE_CHECKING

from redline.core.audio import AudioError

if TYPE_CHECKING:
    from redline.measure.separate import Separator
from redline.cli.common import reference_files


def _measure(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.measure.run import measure_run
    from redline.measure.separate import RoformerSeparator, SeparationError

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
    from redline.measure.separate import RoformerSeparator, SeparationError
    from redline.vocal.rounds import vocal_run

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


def _run(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.engine import settings as chain_settings
    from redline.master.run import run_track
    from redline.measure.separate import RoformerSeparator, SeparationError

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        chosen = chain_settings.load(args.settings) if args.settings else None
        folder = run_track(
            args.track, refs, args.work, args.out, separator or RoformerSeparator(), chosen
        )
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    print(folder)
    return 0


def _studio(args: argparse.Namespace, separator: "Separator | None") -> int:
    import hashlib
    import secrets
    import webbrowser

    from redline.master.run import track_engine
    from redline.measure.run import measure_inputs
    from redline.measure.separate import RoformerSeparator, SeparationError
    from redline.studio import server as studio_server

    refs = reference_files(args.ref)
    if not refs:
        print("redline: no reference audio files given", file=sys.stderr)
        return 2
    try:
        inputs = measure_inputs(args.track, refs, args.work, separator or RoformerSeparator())
        engine = track_engine(inputs)
        stems = inputs.track_stems
        digest = hashlib.sha256(str(args.track.resolve()).encode()).hexdigest()[:8]
        studio = studio_server.Studio(
            engine, stems.vocal + stems.beat, args.work / "studio", f"{args.track.stem}-{digest}"
        )
        token = secrets.token_urlsafe(32)
        httpd = studio_server.make_server(studio, token, args.port)
    except (AudioError, SeparationError, ValueError, OSError) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{httpd.server_address[1]}/?t={token}"
    print(f"studio: {url}")
    print("Ctrl+C stops it")
    if not args.no_browser:
        webbrowser.open(studio_server.browser_file(url, args.work / "studio").as_uri())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
