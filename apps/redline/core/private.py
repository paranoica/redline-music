"""Files only the owner may read: scores, renders and reports of their own music."""

import os
import tempfile
from pathlib import Path


def write_private(path: Path, text: str) -> None:
    """Write text readable by the owner only, replacing the file whatever its old mode.

    Each write has a temporary file of its own, so writers of the same file never collide;
    the last replace wins and a reader sees one whole text.
    """
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def make_private_dir(path: Path, root: Path) -> Path:
    """Make a folder and its missing parents under root, each readable by the owner only."""
    root = root.resolve()
    target = path.resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{path} is outside {root}")
    missing = []
    for folder in (target, *target.parents):
        if folder == root or folder.exists():
            break
        missing.append(folder)
    for folder in reversed(missing):
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
    if target.is_dir():
        target.chmod(0o700)
    return target
