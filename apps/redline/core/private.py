"""Files only the owner may read: scores, renders and reports of their own music."""

import os
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Write text readable by the owner only, replacing the file whatever its old mode."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
