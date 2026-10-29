"""Marks on the lines of generated texts, one file per seed and version, kept by line text.

A line repeated in a hook is one line to mark. The writer gets the newest marks of every
seed as lines to stay away from and lines to hold to.
"""

import json
import os
from pathlib import Path

MARKS = ("cringe", "good")
CRINGE_LINES = 15
GOOD_LINES = 10


def marks_path(folder: Path, version: int) -> Path:
    """Where a seed's marks on one version live."""
    return folder / f"marks-{version}.json"


def read_marks(folder: Path, version: int) -> dict[str, str]:
    """The marks on one version by line; a missing or damaged file has none."""
    path = marks_path(folder, version)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and v in MARKS for k, v in data.items()
    ):
        return {}
    return data


def set_mark(folder: Path, version: int, line: str, mark: str | None) -> dict[str, str]:
    """Mark a line, or take its mark off with None; returns the version's marks."""
    if mark is not None and mark not in MARKS:
        raise ValueError(f"a mark is one of {', '.join(MARKS)}")
    marks = read_marks(folder, version)
    marks.pop(line, None)
    if mark is not None:
        # the newest mark goes last, so it is read first
        marks[line] = mark
    path = marks_path(folder, version)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(marks, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return marks


def marked_lines(
    generated: Path, cringe: int = CRINGE_LINES, good: int = GOOD_LINES
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The newest cringe and good lines over every seed, each line once."""
    files = sorted(generated.glob("*/marks-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    seen: dict[str, str] = {}
    for path in files:
        version = path.stem.removeprefix("marks-")
        if not version.isdigit():
            continue
        for line, mark in reversed(read_marks(path.parent, int(version)).items()):
            seen.setdefault(line, mark)
    bad = [line for line, mark in seen.items() if mark == "cringe"]
    liked = [line for line, mark in seen.items() if mark == "good"]
    return tuple(bad[:cringe]), tuple(liked[:good])
