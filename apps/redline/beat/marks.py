"""The owner's good/bad marks on bars of a seed's score, kept in `<root>/marks.jsonl`.

Each mark keeps the bar as written at mark time and the score's hash, so a later edit of the
score does not change what the mark was about; the next request of the genre quotes them.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from redline.beat.handoff import MARKS
from redline.beat.recipe import recipe_from_json
from redline.beat.render import RenderError, latest_score, sha256

STEPS = 16
VERDICTS = ("good", "bad")


class MarkError(Exception):
    """A mark that cannot be made as asked."""


class StaleMark(MarkError):
    """A mark asked on a score that is no longer the seed's newest."""


def _steps(row: str) -> str:
    return "".join(c for c in row if c not in "| ")


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, int | float) else str(value)


def _row_lines(name: str, row: Any, local: int) -> list[str]:
    """The row's part for bar `local` of its pattern."""
    first, end = local * STEPS, (local + 1) * STEPS
    if isinstance(row, list):
        notes = [n for n in row if isinstance(n, dict) and first <= n.get("step", -1) < end]
        if not notes:
            return []
        parts = [
            f"{n.get('note')} step {n['step'] - first} len {_number(n.get('len'))} "
            f"vel {_number(n.get('vel'))}" + (" passing" if n.get("passing") else "")
            for n in notes
        ]
        return [f"{name}: " + "; ".join(parts)]
    lanes = row if isinstance(row, dict) else {"steps": row}
    out = [f"{name}: {_steps(str(lanes.get('steps', '')))[first:end]}"]
    for lane in ("velocity", "pitch", "decay", "chance"):
        values = lanes.get(lane)
        if isinstance(values, list):
            out.append(f"  {lane}: " + " ".join(_number(v) for v in values[first:end]))
    return out


def total_bars(body: dict[str, Any]) -> int:
    """Bars of the whole song."""
    return sum(int(s["bars"]) for s in body["sections"])


def excerpt(body: dict[str, Any], bar: int) -> str:
    """The bar (1-based) of the score as written: each playing sound's steps or notes.

    Raises:
        MarkError: The bar is outside the song.
    """
    total = total_bars(body)
    if not 1 <= bar <= total:
        raise MarkError(f"bar {bar} is outside the song: it has bars 1 to {total}")
    first = 0
    for section in body["sections"]:
        if bar - 1 < first + section["bars"]:
            break
        first += section["bars"]
    local = bar - 1 - first
    patterns = body["patterns"]
    muted = set(section.get("mute", []))
    fill = section.get("fill") if local == section["bars"] - 1 else None
    filled = set(patterns[fill]["rows"]) if fill else set()
    sources = [(p, local % patterns[p]["bars"], False) for p in section["play"]]
    if fill:
        sources.append((fill, 0, True))
    lines: dict[str, list[str]] = {}
    for name, pattern_bar, is_fill in sources:
        for sound, row in patterns[name]["rows"].items():
            # mutes apply after the fill; a fill replaces the rows it has
            if sound in muted or (sound in filled and not is_fill):
                continue
            part = _row_lines(sound, row, pattern_bar)
            if part:
                where = "fill " if is_fill else ""
                part[0] = f"{part[0]}  ({where}{name})"
                lines.setdefault(sound, []).extend(part)
    head = (
        f"bar {bar} of {total}: section {section['name']}, bar {local + 1} of "
        f"{section['bars']}; {_number(body.get('bpm'))} BPM, swing {_number(body.get('swing'))}"
    )
    body_lines = [line for sound in body["sounds"] for line in lines.get(sound, [])]
    return "\n".join([head, *(body_lines or ["(nothing plays)"])])


def _genre(folder: Path) -> str:
    recipe = folder / "recipe.json"
    try:
        return recipe_from_json(recipe.read_text(encoding="utf-8")).genre
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise MarkError(f"{recipe}: the seed's genre is unknown: {error}") from error


def add_mark(
    root: Path,
    seed: int,
    bar: int,
    verdict: str,
    note: str = "",
    now: datetime | None = None,
    base: str | None = None,
) -> dict[str, Any]:
    """Append a mark on a bar of the seed's newest score to `<root>/marks.jsonl`.

    Args:
        root: The beats folder.
        seed: The beat's seed.
        bar: The bar, 1-based.
        verdict: `good` or `bad`.
        note: Why, in the owner's words.
        now: The mark's time (default: now, UTC).
        base: The score file the mark is meant for; when given, it must be the newest.

    Returns:
        The mark as written.

    Raises:
        StaleMark: `base` is not the seed's newest saved score.
        MarkError: No saved score, an unknown genre, a bar outside the song, a bad verdict.
    """
    if verdict not in VERDICTS:
        raise MarkError(f"a mark is good or bad, not {verdict!r}")
    folder = root / str(seed)
    try:
        path = latest_score(folder)
    except RenderError as error:
        raise MarkError(str(error)) from error
    if base is not None and base != path.name:
        raise StaleMark(f"{base} is not the newest saved score, {path.name}: reload before marking")
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MarkError(f"{path}: {error}") from error
    mark = {
        "seed": seed,
        "bar": bar,
        "verdict": verdict,
        "note": note,
        "excerpt": excerpt(body, bar),
        "genre": _genre(folder),
        "score": path.name,
        "score_sha256": sha256(path),
        "time": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    root.mkdir(parents=True, exist_ok=True)
    line = json.dumps(mark, ensure_ascii=False) + "\n"
    fd = os.open(root / MARKS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        out.write(line)
    return mark


def seed_marks(root: Path, seed: int) -> list[dict[str, Any]]:
    """The seed's marks, oldest first; lines that are not marks are skipped."""
    path = root / MARKS
    if not path.exists():
        return []
    found = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            mark = json.loads(line)
        except ValueError:
            continue
        if isinstance(mark, dict) and mark.get("seed") == seed:
            found.append(mark)
    return found
