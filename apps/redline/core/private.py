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
