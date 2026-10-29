"""Marks on the lines of generated texts, one file per seed and version, kept by line text.

A line repeated in a hook is one line to mark. Each mark keeps when it was made, and the
writer gets the newest ones over every seed as lines to stay away from and to hold to.
"""

import json
import os
import time
from pathlib import Path

MARKS = ("cringe", "good")
CRINGE_LINES = 15
GOOD_LINES = 10


class DamagedMarks(ValueError):
    """A marks file is there but cannot be read; writing over it would lose its marks."""


def marks_path(folder: Path, version: int) -> Path:
    """Where a seed's marks on one version live."""
    return folder / f"marks-{version}.json"


def _entries(path: Path) -> dict[str, dict] | None:
    """Every mark in the file with its time; {} for no file, None for a damaged one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    for value in data.values():
        if not (
            isinstance(value, dict)
            and value.get("mark") in MARKS
            and isinstance(value.get("at"), int)
            and not isinstance(value.get("at"), bool)
        ):
            return None
    return data


def read_marks(folder: Path, version: int) -> dict[str, str]:
    """The marks on one version by line; a missing or damaged file has none."""
    entries = _entries(marks_path(folder, version)) or {}
    return {line: entry["mark"] for line, entry in entries.items()}


def set_mark(folder: Path, version: int, line: str, mark: str | None) -> dict[str, str]:
    """Mark a line, or take its mark off with None; returns the version's marks.

    Not safe to call for the same file from two threads at once: the caller holds a lock.
    """
    if mark is not None and mark not in MARKS:
        raise ValueError(f"a mark is one of {', '.join(MARKS)}")
    path = marks_path(folder, version)
    entries = _entries(path)
    if entries is None:
        raise DamagedMarks(f"{path} cannot be read; fix or remove it first")
    entries.pop(line, None)
    if mark is not None:
        entries[line] = {"mark": mark, "at": time.time_ns()}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(json.dumps(entries, ensure_ascii=False, indent=1))
    tmp.replace(path)
    return {kept: entry["mark"] for kept, entry in entries.items()}


def marked_lines(
    generated: Path, cringe: int = CRINGE_LINES, good: int = GOOD_LINES
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The newest cringe and good lines over every seed, each line once, by when marked."""
    found = []
    for path in generated.glob("*/marks-*.json"):
        found += [(e["at"], line, e["mark"]) for line, e in (_entries(path) or {}).items()]
    seen: dict[str, str] = {}
    for _, line, mark in sorted(found, key=lambda item: item[0], reverse=True):
        seen.setdefault(line, mark)
    bad = [line for line, mark in seen.items() if mark == "cringe"]
    liked = [line for line, mark in seen.items() if mark == "good"]
    return tuple(bad[:cringe]), tuple(liked[:good])
