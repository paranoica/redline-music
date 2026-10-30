"""`redline record`: the engine started on the beat, the monitor set up, the calibration.

The beat goes to the engine at the rate the graph runs at. That rate is known only once the
engine has started, so the beat is first written at GUESS_RATE and, when the engine reports
another rate, written again and the engine started once more.
"""

import json
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from redline.core.audio import decode
from redline.core.loudness import integrated_lufs
from redline.engine import live
from redline.engine.settings import Settings
from redline.record.latency import LatencyKey, save_calibration
from redline.record.process import (
    EngineError,
    EngineProcess,
    build_binary,
    engine_argv,
    engine_env,
    find_binary,
)
from redline.record.protocol import Startup
from redline.record.sweep import Delay, exp_sweep, find_delay
from redline.takes.rank import TakesError, find_takes

if TYPE_CHECKING:
    from redline.measure.separate import Separator

REQUESTED_QUANTUM = 64
GUESS_RATE = 48000
LEVEL_CHECK_S = 5.0
# the sweep's last frames come back a round trip later, and the stop lands a period after that
CALIBRATION_TAIL_S = 1.0
# the mic must not reach the headphones while the sweep is measured
MUTED_RACK = json.dumps({"effects": [{"effect": "gain", "on": True, "params": {"db": -60.0}}] * 2})
LATENCY_FILE = "latency.json"

Wait = Callable[[float], None]


def engine_command(say: Callable[[str], None]) -> list[str]:
    """The engine's binary as a command, built first when it is missing.

    Raises:
        EngineError: It is missing and cannot be built.
    """
    binary = find_binary()
    if binary is None:
        say("redline-rec is not built; building it (cargo build --release, feature jack)...")
        binary = build_binary()
    return [str(binary)]


def prepare_beat(beat: Path, rate: int, folder: Path) -> Path:
    """The beat as stereo float WAV at rate, in folder as beat-<rate>.wav."""
    decoded = decode(beat)
    samples = decoded.samples
    if decoded.rate != rate:
        divisor = math.gcd(rate, decoded.rate)
        samples = resample_poly(samples, rate // divisor, decoded.rate // divisor, axis=0)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"beat-{rate}.wav"
    sf.write(out, np.asarray(samples, dtype=np.float32), rate, subtype="FLOAT")
    return out


def open_engine(
    command: Sequence[str],
    make_beat: Callable[[int], Path],
    quantum: int = REQUESTED_QUANTUM,
) -> EngineProcess:
    """Start the engine with the beat at the graph's rate.

    Args:
        command: The binary, as a command.
        make_beat: Writes the beat at a rate and returns its path.
        quantum: The quantum to ask for.

    Raises:
        EngineError: The engine did not start, or changed rate between two starts.
    """
    engine = EngineProcess.launch(
        engine_argv(command, make_beat(GUESS_RATE), quantum), engine_env(GUESS_RATE, quantum)
    )
    rate = engine.startup.rate
    if rate == GUESS_RATE:
        return engine
    engine.close()
    engine = EngineProcess.launch(
        engine_argv(command, make_beat(rate), quantum), engine_env(rate, quantum)
    )
    if engine.startup.rate != rate:
        engine.close()
        raise EngineError(
            f"the graph's rate moved from {rate} to {engine.startup.rate} Hz between two starts"
        )
    return engine


def latency_key(startup: Startup) -> LatencyKey:
    """What a calibration is stored under for this engine."""
    return LatencyKey(startup.devices, startup.rate, startup.quantum)


def recorded_lufs(path: Path) -> float:
    """The loudness of a mono take, read as `redline takes` reads it (on both channels)."""
    decoded = decode(path)
    return integrated_lufs(decoded.samples, decoded.rate)


def _record_for(engine: EngineProcess, path: Path, seconds: float, wait: Wait | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    engine.start(path.resolve(), 0)
    (wait or time.sleep)(seconds)
    return engine.stop().path


def level_check(
    engine: EngineProcess, folder: Path, seconds: float = LEVEL_CHECK_S, wait: Wait | None = None
) -> float:
    """Record seconds of the mic over the beat and return its loudness; the file is removed."""
    path = _record_for(engine, folder / "level-check.wav", seconds, wait)
    try:
        return recorded_lufs(path)
    finally:
        path.unlink(missing_ok=True)


def last_take(folder: Path) -> Path | None:
    """The highest-numbered take in the folder; None without one."""
    try:
        return find_takes(folder)[-1]
    except TakesError:
        return None


def monitor_numbers(
    engine: EngineProcess | None,
    takes: Path,
    beat: Path,
    refs: list[Path],
    work: Path,
    separator: "Separator | None",
    settings: Settings,
    say: Callable[[str], None] = print,
    wait: Wait | None = None,
) -> tuple[dict[str, Any], str]:
    """The numbers the monitor rack is built with, and where they came from.

    With the song's last take and references: `live.analysis` on an engine over the take
    and the beat. With a take and no references: the take's level and neutral numbers.
    With no take: a level check on the mic and neutral numbers.

    Raises:
        ValueError: The level heard is silent, or the analysis fails.
        EngineError: The level check could not record.
    """
    take = last_take(takes)
    if take is not None and refs:
        from redline.engine.track import track_engine
        from redline.measure.run import measure_own
        from redline.measure.separate import RoformerSeparator

        say(f"analysing {take.name} over the beat for the monitor...")
        inputs = measure_own(take, beat, refs, work, separator or RoformerSeparator())
        return live.analysis(track_engine(inputs), settings), f"the analysis of {take.name}"
    if take is not None:
        loudness = recorded_lufs(take)
        source = f"the level of {take.name}, the rest neutral (no --ref to analyse it)"
    else:
        if engine is None:
            raise EngineError("no take and no engine for a level check")
        say(f"level check: sing or speak as in a take for {LEVEL_CHECK_S:g} s...")
        loudness = level_check(engine, work / "record", wait=wait)
        source = "a level check, the rest neutral until a take exists"
    rate = engine.startup.rate if engine is not None else GUESS_RATE
    return live.level_numbers(loudness, rate, settings), source


def calibrate(
    command: Sequence[str],
    work: Path,
    mode: str,
    quantum: int = REQUESTED_QUANTUM,
    wait: Wait | None = None,
) -> tuple[LatencyKey, Delay]:
    """Play a sweep through the singer's path, find the round trip and store it under its key.

    Raises:
        EngineError: The engine did not start or record.
        CalibrationError: The sweep did not come back clearly enough.
    """
    folder = work / "record"
    sweeps = {}

    def make_sweep(rate: int) -> Path:
        sweeps[rate] = exp_sweep(rate)
        path = folder / f"sweep-{rate}.wav"
        folder.mkdir(parents=True, exist_ok=True)
        mono = sweeps[rate].samples
        sf.write(path, np.column_stack([mono, mono]), rate, subtype="FLOAT")
        return path

    engine = open_engine(command, make_sweep, quantum)
    try:
        engine.set_rack(MUTED_RACK, None)
        sweep = sweeps[engine.startup.rate]
        seconds = len(sweep.samples) / sweep.rate + CALIBRATION_TAIL_S
        path = _record_for(engine, folder / "calibration.wav", seconds, wait)
        key = latency_key(engine.startup)
    finally:
        engine.close()
    try:
        delay = find_delay(decode(path).samples[:, 0], sweep)
    finally:
        path.unlink(missing_ok=True)
    save_calibration(folder / LATENCY_FILE, key, delay, mode)
    return key, delay
