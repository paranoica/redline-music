"""The terminal side of `redline record`: keys in, one status line out."""

import contextlib
import select
import sys
import termios
import tty
from collections.abc import Callable, Iterator

from redline.record.process import EngineError, EngineProcess
from redline.record.session import Session

POLL_S = 0.05
KEYS_HELP = "space: start/stop a take   r: discard the last take   q: quit"
CLEAR = "\r\x1b[K"

ReadKey = Callable[[float], str | None]


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
                message = session.toggle()
            elif key in ("r", "R"):
                message = session.discard_last()
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
