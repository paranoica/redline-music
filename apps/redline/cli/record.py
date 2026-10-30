"""`redline record`: takes over a beat through the native engine, and its calibration."""

import argparse
import sys
from typing import TYPE_CHECKING

from redline.cli.common import audio_files
from redline.cli.sound import SOUND_ERRORS, _saved_settings

if TYPE_CHECKING:
    from redline.measure.separate import Separator
    from redline.record.driver import Driver, Link, Stage
    from redline.record.process import EngineProcess


def _say(message: str) -> None:
    print(message, flush=True)


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _show(stage: "Stage") -> None:
    from redline.record import terminal

    for line in terminal.render_stage(stage):
        _say(line)


def _record(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.record import driver
    from redline.record.process import EngineError
    from redline.record.sweep import CalibrationError

    try:
        command = driver.resolve_command(_show)
        if args.calibrate:
            return _calibrate(args, command)
        settings = _saved_settings(args)
        refs = audio_files(args.ref) if args.ref else []
        recording = driver.Driver(
            command, args.beat, args.takes / args.song, args.work, refs, separator, settings
        )
        engine = recording.open(_show)
    except (EngineError, CalibrationError, *SOUND_ERRORS) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    return _session(args, recording, engine)


def _calibrate(args: argparse.Namespace, command: list[str]) -> int:
    from redline.record import driver, terminal

    mode = args.mode or "cup"
    _say(terminal.SWEEP_INSTRUCTION)
    for line in terminal.render_calibrated(driver.calibrate(command, args.work, mode)):
        _say(line)
    return 0


def _session(args: argparse.Namespace, recording: "Driver", engine: "EngineProcess") -> int:
    from redline.record import terminal
    from redline.record.process import EngineError

    session = None
    code = 0
    try:

        def show_link(link: "Link") -> None:
            for line in terminal.render_link(link):
                _say(line)

        ready = recording.start(engine, _show, show_link)
        session = ready.session
        for line in terminal.render_monitor(ready.monitor):
            _say(line)
        with terminal.raw_keys() as read_key:
            terminal.loop(session, engine, read_key, _write)
    except KeyboardInterrupt:
        pass
    except (EngineError, *SOUND_ERRORS) as error:
        print(f"\nredline: {error}", file=sys.stderr)
        code = 2
    try:
        # a take still running is kept
        if session is not None:
            session.close()
        else:
            engine.close()
    except EngineError as error:
        print(f"\nredline: {error}", file=sys.stderr)
        code = 2
    if code:
        return code
    _say(f"takes in {recording.folder}")
    _say(f"rank them: redline takes --beat {args.beat} --dir {recording.folder}")
    return 0
