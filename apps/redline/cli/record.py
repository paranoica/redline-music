"""`redline record`: takes over a beat through the native engine, and its calibration."""

import argparse
import sys
from typing import TYPE_CHECKING

from redline.cli.common import audio_files
from redline.cli.sound import SOUND_ERRORS, _saved_settings

if TYPE_CHECKING:
    from pathlib import Path

    from redline.engine.settings import Settings
    from redline.measure.separate import Separator
    from redline.record.process import EngineProcess

SPEAKER_WARNING = (
    "calibrated through speakers: the round trip includes the air to the mic (about 3 ms a "
    "metre); for takes sung on headphones, calibrate with a cup at the mic or a loopback"
)


def _say(message: str) -> None:
    print(message, flush=True)


def _write(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _record(args: argparse.Namespace, separator: "Separator | None") -> int:
    from redline.record import run as record_run
    from redline.record.process import EngineError
    from redline.record.sweep import CalibrationError

    try:
        command = record_run.engine_command(_say)
        if args.calibrate:
            return _calibrate(args, command)
        settings = _saved_settings(args)
        refs = audio_files(args.ref) if args.ref else []
        folder = args.work / "record"
        engine = record_run.open_engine(
            command, lambda rate: record_run.prepare_beat(args.beat, rate, folder)
        )
    except (EngineError, CalibrationError, *SOUND_ERRORS) as error:
        print(f"redline: {error}", file=sys.stderr)
        return 2
    return _session(args, engine, settings, refs, separator)


def _calibrate(args: argparse.Namespace, command: list[str]) -> int:
    from redline.record import run as record_run

    mode = args.mode or "cup"
    _say(
        "playing a sweep: keep the headphones off your ears; hold one cup to the mic "
        "(or connect a loopback cable)"
    )
    key, delay = record_run.calibrate(command, args.work, mode)
    ms = 1000 * delay.frames / key.rate
    _say(
        f"round trip {delay.frames} frames ({ms:.1f} ms) for {key.name()}; "
        f"peak-to-noise {delay.peak_to_noise_db:.0f} dB"
    )
    _say(f"stored in {args.work / 'record' / record_run.LATENCY_FILE}")
    if mode == "speakers":
        _say(SPEAKER_WARNING)
    return 0


def _session(
    args: argparse.Namespace,
    engine: "EngineProcess",
    settings: "Settings | None",
    refs: "list[Path]",
    separator: "Separator | None",
) -> int:
    from redline.engine import live
    from redline.engine.settings import Settings
    from redline.record import run as record_run
    from redline.record import terminal
    from redline.record.latency import round_trip
    from redline.record.process import EngineError
    from redline.record.session import Session, budget_frames, monitor_ms, quantum_warning

    chosen = Settings() if settings is None else settings
    startup = engine.startup
    folder = args.takes / args.song
    session = None
    code = 0
    try:
        _say(f"JACK: {startup.rate} Hz, quantum {startup.quantum}")
        warning = quantum_warning(startup.quantum)
        if warning:
            _say(warning)
        trip = round_trip(
            args.work / "record" / record_run.LATENCY_FILE,
            record_run.latency_key(startup),
            startup.estimate,
        )
        _say(trip.label())
        if trip.note:
            _say(trip.note)
        numbers, source = record_run.monitor_numbers(
            engine, folder, args.beat, refs, args.work, separator, chosen, _say
        )
        _say(f"monitor numbers from {source}")
        rack = live.monitor_rack(chosen, numbers)
        latency, dropped = engine.set_rack(rack, budget_frames(trip, startup.rate))
        if dropped:
            _say(f"switched off to stay under 10 ms: {', '.join(dropped)}")
        late = monitor_ms(trip, latency, startup.rate)
        _say("monitor latency unknown" if late is None else f"monitor latency {late:.1f} ms")
        session = Session(engine, folder, trip, startup.quantum)
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
    _say(f"takes in {folder}")
    _say(f"rank them: redline takes --beat {args.beat} --dir {folder}")
    return 0
