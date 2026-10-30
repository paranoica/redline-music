"""The terminal side of `redline record`: keys in, one status line out."""

import contextlib
import select
import sys
import termios
import tty
from collections.abc import Callable, Iterator

from redline.record.driver import Calibrated, Link, Monitor, Notice, Stage
from redline.record.process import EngineError, EngineProcess
from redline.record.session import Outcome, Session, quantum_warning

POLL_S = 0.05
KEYS_HELP = "space: start/stop a take   r: discard the last take   q: quit"
CLEAR = "\r\x1b[K"

ReadKey = Callable[[float], str | None]

SPEAKER_WARNING = (
    "calibrated through speakers: the round trip includes the air to the mic (about 3 ms a "
    "metre); for takes sung on headphones, calibrate with a cup at the mic or a loopback"
)
SWEEP_INSTRUCTION = (
    "playing a sweep: keep the headphones off your ears; hold one cup to the mic "
    "(or connect a loopback cable)"
)


def _take_length(values: dict) -> str:
    lost = values["dropouts"]
    note = f", {lost} dropouts" if lost else ""
    return f"{values['seconds']:.1f} s{note}"


def render(outcome: Outcome) -> str:
    """The sentence for what a key did."""
    values = outcome.values
    match outcome.code:
        case "recording":
            return f"recording {values['take']}"
        case "quantum_moved":
            return (
                f"take refused: the audio graph's quantum moved from {values['was']} to "
                f"{values['now']} frames, so the round trip the takes are shifted by no longer "
                "holds. Quit and restart `redline record`"
            )
        case "kept":
            return f"{values['take']} kept: {_take_length(values)}"
        case "kept_late":
            return f"{values['take']} finished late, kept: {_take_length(values)}"
        case "finished_late_discarded":
            return f"{values['take']} finished late; it was discarded"
        case "discarded":
            return f"{values['take']} discarded"
        case "still_finishing":
            return f"{values['take']} is still finishing: press space to collect it first"
        case "nothing_to_discard":
            return "nothing recorded in this session to discard"
    raise ValueError(f"no wording for {outcome.code!r}")


def render_notice(notice: Notice) -> str:
    """The sentence for a warning."""
    if notice.code == "quantum_high":
        return quantum_warning(notice.values["quantum"]) or ""
    if notice.code == "speakers":
        return SPEAKER_WARNING
    raise ValueError(f"no wording for {notice.code!r}")


def render_stage(stage: Stage) -> list[str]:
    """The lines for a stage; the stages the terminal shows nothing for give none."""
    if stage.name == "building":
        return ["redline-rec is not built; building it (cargo build --release, feature jack)..."]
    if stage.name == "analysing":
        return [f"analysing {stage.values['take']} over the beat for the monitor..."]
    if stage.name == "level_check":
        seconds = stage.values["seconds"]
        return [f"level check: sing or speak as in a take for {seconds:g} s..."]
    return []


def render_link(link: Link) -> list[str]:
    """The lines after the engine started: rate, quantum, warnings, round trip."""
    lines = [f"JACK: {link.rate} Hz, quantum {link.quantum}"]
    lines += [render_notice(notice) for notice in link.notices]
    lines.append(link.trip.label())
    if link.trip.note:
        lines.append(link.trip.note)
    return lines


def render_monitor(monitor: Monitor) -> list[str]:
    """The lines after the monitor was set up."""
    lines = [f"monitor numbers from {monitor.source}"]
    if monitor.dropped:
        lines.append(f"switched off to stay under 10 ms: {', '.join(monitor.dropped)}")
    late = monitor.latency_ms
    lines.append("monitor latency unknown" if late is None else f"monitor latency {late:.1f} ms")
    return lines


def render_calibrated(done: Calibrated) -> list[str]:
    """The lines after a calibration."""
    lines = [
        f"round trip {done.frames} frames ({done.ms:.1f} ms) for {done.key.name()}; "
        f"peak-to-noise {done.peak_to_noise_db:.0f} dB",
        f"stored in {done.stored_in}",
    ]
    return lines + [render_notice(notice) for notice in done.notices]


@contextlib.contextmanager
def raw_keys() -> Iterator[ReadKey]:
    """Read single keys from the terminal without echo, until the block ends.

    Raises:
        EngineError: stdin is not a terminal.
    """
    stdin = sys.stdin
    if not stdin.isatty():
        raise EngineError("redline record needs a terminal for its keys")
    fd = stdin.fileno()
    saved = termios.tcgetattr(fd)

    def read_key(timeout: float) -> str | None:
        ready, _, _ = select.select([fd], [], [], timeout)
        return stdin.read(1) if ready else None

    tty.setcbreak(fd)
    try:
        yield read_key
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def loop(
    session: Session,
    engine: EngineProcess,
    read_key: ReadKey,
    write: Callable[[str], None],
    poll_s: float = POLL_S,
) -> None:
    """Run the keys against the session and redraw the status line until q.

    Raises:
        EngineError: The engine stopped.
    """
    write(KEYS_HELP + "\n")
    while True:
        key = read_key(poll_s)
        if key in ("q", "Q", "\x03", "\x04"):
            write(CLEAR)
            return
        message = None
        try:
            if key == " ":
                message = render(session.toggle())
            elif key in ("r", "R"):
                message = render(session.discard_last())
        except EngineError as error:
            if engine.exited():
                raise
            message = f"engine: {error}"
        if message:
            write(f"{CLEAR}{message}\n")
        try:
            status = session.status(engine.meters())
        except EngineError as error:
            if engine.exited():
                raise
            status = f"engine: {error}"
        write(CLEAR + status)
