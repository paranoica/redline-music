"""The musical checks of a beat score against its recipe.

Errors refuse the score: what Rust refuses, and what breaks the recipe (key, scale, tempo,
bars, the 808's range, an unknown sound library). Warnings never refuse; each says where its
limit comes from.
"""

import json
import re
from bisect import bisect_left
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redline_dsp

from redline.beat import library, pack
from redline.beat.recipe import BLOG, LETTERS, OWN, Recipe, parse_key, scale

RATE = 48000.0
BPM_SLACK = 0.5
BASS_RANGE = (24, 55)
# an 808 note this close to a kick starts with it
ON_KICK_S = 0.020
# the least share of kicks under the 808 that start a note of it
ON_KICK_SHARE = 0.5
# the 808s: synthesised and recorded
BASSES = ("bass808", "sample808")
MAX_SAME = 8
HATS = ("hat_closed", "hat_open", "oneshot_hat", "oneshot_open_hat")
NOTE = re.compile(r"([A-G])([#b]?)(-?\d{1,2})")
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
SHOWN_BARS = 8


@dataclass(frozen=True)
class Caution:
    """A warning: what, where, and whether a blog or an own choice set its limit."""

    kind: str
    where: str
    message: str
    source: str

    def __str__(self) -> str:
        """The warning as one line."""
        return f"{self.where}: {self.message} (limit: {self.source})"


@dataclass(frozen=True)
class Checked:
    """The errors that refuse a score and the warnings that do not."""

    errors: tuple[str, ...]
    warnings: tuple[Caution, ...]

    @property
    def ok(self) -> bool:
        """The score has no error."""
        return not self.errors


def note_number(name: str) -> int | None:
    """The MIDI number of a note name like `F#1`, read as the score format reads it."""
    found = NOTE.fullmatch(name)
    if found is None:
        return None
    pc = LETTERS[found.group(1)] + {"#": 1, "b": -1, "": 0}[found.group(2)]
    return (int(found.group(3)) + 1) * 12 + pc


def note_name(number: int) -> str:
    """The name of a MIDI number, sharps for black keys."""
    return f"{NAMES[number % 12]}{number // 12 - 1}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _instruments(body: dict[str, Any]) -> dict[str, tuple[str, float]]:
    """Each sound's instrument and its `tune` param (0 when not given)."""
    out = {}
    for name, sound in _dict(body.get("sounds")).items():
        sound = _dict(sound)
        tune = _number(_dict(sound.get("params")).get("tune")) or 0.0
        out[name] = (str(sound.get("instrument")), tune)
    return out


def _note_rows(body: dict[str, Any]) -> Iterator[tuple[str, str, list[Any]]]:
    for pname, pattern in _dict(body.get("patterns")).items():
        for sname, row in _dict(_dict(pattern).get("rows")).items():
            if isinstance(row, list):
                yield f"patterns.{pname}.rows.{sname}", sname, row


def _note_errors(note: dict[str, Any], path: str, key: str, bass: float | None) -> list[str]:
    """The scale and 808-range errors of one note; `bass` is the 808's tune, None for others."""
    name = note.get("note")
    number = note_number(name) if isinstance(name, str) else None
    if number is None:
        return []
    out = []
    if note.get("passing") is not True and number % 12 not in scale(key):
        out.append(f'{path}: {name} is outside {key}; mark it "passing": true if it passes')
    if bass is not None:
        sounding = number + round(bass)
        tuned = f" (with tune {bass:g}, {note_name(sounding)})" if bass else ""
        low, high = BASS_RANGE
        if sounding < low:
            out.append(f"{path}: {name}{tuned} is below {note_name(low)}, the 808's lowest")
        elif sounding > high:
            out.append(f"{path}: {name}{tuned} is above {note_name(high)}, the 808's highest")
    return out


def _notes(body: dict[str, Any], recipe: Recipe) -> list[str]:
    sounds = _instruments(body)
    out = []
    for path, sname, row in _note_rows(body):
        instrument, tune = sounds.get(sname, ("", 0.0))
        bass = tune if instrument in BASSES else None
        for i, note in enumerate(row):
            if isinstance(note, dict):
                out += _note_errors(note, f"{path}[{i}].note", recipe.key, bass)
    return out


def _libraries(body: dict[str, Any], known: Iterable[str]) -> list[str]:
    known = set(known)
    out = []
    for name, sound in _dict(body.get("sounds")).items():
        lib = _dict(sound).get("library")
        if isinstance(lib, str) and lib not in known:
            out.append(f"sounds.{name}.library: {lib} is not in the sound library")
    return out


def _total_bars(body: dict[str, Any]) -> int | None:
    sections = body.get("sections")
    if not isinstance(sections, list):
        return None
    bars = [_number(_dict(s).get("bars")) for s in sections]
    # the core takes 64.0 as 64 bars, so a float with no fraction counts
    whole = [int(b) for b in bars if b is not None and b.is_integer()]
    return sum(whole) if len(whole) == len(bars) else None


def _recipe_errors(body: dict[str, Any], recipe: Recipe) -> list[str]:
    out = []
    key = body.get("key")
    parsed = parse_key(key) if isinstance(key, str) else None
    if parsed is not None and parsed != parse_key(recipe.key):
        out.append(f"key: {key} is not the recipe's {recipe.key}")
    bpm = _number(body.get("bpm"))
    if bpm is not None and abs(bpm - recipe.bpm) > BPM_SLACK:
        out.append(f"bpm: {bpm:g} is not the recipe's {recipe.bpm:g}")
    total = _total_bars(body)
    low, high = recipe.bars
    if total is not None and not low <= total <= high:
        out.append(f"sections: {total} bars in all; the recipe takes {low} to {high}")
    return out


def _run_spans(values: list[Any]) -> Iterator[tuple[int, int]]:
    """Each run of equal neighbours longer than MAX_SAME, as first and last index."""
    first = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[first]:
            if i - first > MAX_SAME:
                yield first, i - 1
            first = i


# what a recorded pack sound plays the part of, by its category
PACK_ROLES = {
    "kick": "kick",
    "snare": "snare",
    "clap": "clap",
    "hat_closed": "hat_closed",
    "hat_open": "hat_open",
    "808": "sample808",
}


def _roles(sounds: list[dict[str, Any]], body: dict[str, Any], path: Path | None) -> list[str]:
    """Each sound's instrument, a pack one-shot's taken as the drum its category names."""
    try:
        catalog = pack.load(pack.CATALOG if path is None else path)
    except pack.PackError:
        catalog = {}
    out = []
    for s in sounds:
        library_name = _dict(_dict(body.get("sounds")).get(s["name"])).get("library")
        entry = None
        if isinstance(library_name, str) and library.is_pack(library_name):
            entry = catalog.get(library_name[len(library.PACK_PREFIX) :])
        out.append(PACK_ROLES.get(entry.category, s["instrument"]) if entry else s["instrument"])
    return out


class _Table:
    """The event table with what the warnings ask of it."""

    def __init__(self, score_json: str, body: dict[str, Any], catalog: Path | None) -> None:
        table = json.loads(redline_dsp.beat_events(score_json, RATE))
        self.instruments = _roles(table["sounds"], body, catalog)
        self.names = [s["name"] for s in table["sounds"]]
        self.events = table["events"]
        self.sections = [(str(s["name"]), int(s["bars"])) for s in body["sections"]]
        self.bars = sum(bars for _, bars in self.sections)
        self.step = RATE * 60.0 / float(body["bpm"]) / 4.0

    def of(self, instruments: tuple[str, ...]) -> list[dict[str, Any]]:
        return [e for e in self.events if self.instruments[e["sound"]] in instruments]


def _density(table: _Table, recipe: Recipe) -> list[Caution]:
    out = []
    for label, instruments, (low, high) in (
        ("hat", HATS, recipe.hats_per_bar),
        ("kick", ("kick",), recipe.kicks_per_bar),
    ):
        hits = [e["section"] for e in table.of(instruments)]
        for i, (name, bars) in enumerate(table.sections):
            count = hits.count(i)
            if count and not low <= count / bars <= high:
                out.append(
                    Caution(
                        "density",
                        f"sections[{i}] ({name})",
                        f"{count / bars:.1f} {label} hits a bar; {recipe.genre} keeps "
                        f"{low:g} to {high:g}",
                        OWN,
                    )
                )
    return out


def _repeats(table: _Table) -> list[Caution]:
    contents: list[list[tuple[Any, ...]]] = [[] for _ in range(table.bars)]
    playing: list[set[str]] = [set() for _ in range(table.bars)]
    for e in table.events:
        # in steps: frame lengths differ by rounding from bar to bar
        length = None if e["off"] is None else round((e["off"] - e["start"]) / table.step)
        contents[e["bar"]].append(
            (e["sound"], e["step"], e["roll"], e["note"], e["vel"], e["decay"], length)
        )
        playing[e["bar"]].add(table.names[e["sound"]])
    out = []
    for first, last in _run_spans([sorted(c) for c in contents]):
        out.append(
            Caution(
                "repeat",
                f"bars {first + 1}-{last + 1}",
                f"the same bar plays {last - first + 1} times in a row; change one thing "
                "every 4 to 8 bars",
                BLOG,
            )
        )
    for first, last in _run_spans(playing):
        sounds = ", ".join(sorted(playing[first])) or "nothing"
        out.append(
            Caution(
                "unchanged",
                f"bars {first + 1}-{last + 1}",
                f"the same sounds ({sounds}) play for {last - first + 1} bars; bring one in "
                "or out every 8 bars at most",
                BLOG,
            )
        )
    return out


def _off_kick(table: _Table) -> list[Caution]:
    notes = table.of(BASSES)
    starts = sorted(e["start"] for e in notes)
    # a note sounds to its written end; a one-shot 808 played as a drum to where it is cut
    spans = [
        (e["start"], e["off"] if e["off"] is not None else e["end"])
        for e in notes
        if e["off"] is not None or e["end"] is not None
    ]
    near = ON_KICK_S * RATE
    # an 808 note starting this soon after a kick follows it instead of landing with it
    late = table.step * 2 + near
    on = 0
    off_bars = []
    for kick in table.of(("kick",)):
        if kick["roll"] > 0:
            continue
        at = kick["start"]
        i = bisect_left(starts, at - near)
        if i < len(starts) and starts[i] <= at + near:
            on += 1
        elif any(start <= at < stop for start, stop in spans) or (
            i < len(starts) and starts[i] <= at + late
        ):
            off_bars.append(kick["bar"] + 1)
    counted = on + len(off_bars)
    if not counted or on >= ON_KICK_SHARE * counted:
        return []
    bars = sorted(set(off_bars))
    shown = ", ".join(map(str, bars[:SHOWN_BARS]))
    more = " ..." if len(bars) > SHOWN_BARS else ""
    return [
        Caution(
            "off_kick",
            f"bars {shown}{more}",
            f"{on} of {counted} kicks under the 808 start an 808 note; start the 808 on most "
            "kicks, extra notes in the gaps",
            BLOG,
        )
    ]


def check_score(
    score_json: str,
    recipe: Recipe,
    libraries: Iterable[str] | None = None,
    catalog: Path | None = None,
) -> Checked:
    """Check a score's text against its recipe.

    Args:
        score_json: The score as the beatmaker wrote it.
        recipe: The seed's recipe.
        libraries: The sound library names a sampler may play; None reads the manifest.
        catalog: The owner's pack catalogue (default: `pack.CATALOG`), for a pack sound's part.

    Returns:
        The errors as `path: message` lines, and the warnings (only for a score Rust takes).
    """
    errors = list(redline_dsp.beat_check(score_json))
    try:
        body = json.loads(score_json)
    except ValueError:
        return Checked(tuple(errors), ())
    if not isinstance(body, dict):
        return Checked(tuple(errors), ())
    known = (
        (library.load().keys() | library.pack_names(catalog)) if libraries is None else libraries
    )
    rust_ok = not errors
    errors += _recipe_errors(body, recipe)
    errors += _notes(body, recipe)
    errors += _libraries(body, known)
    if not rust_ok:
        return Checked(tuple(errors), ())
    table = _Table(score_json, body, catalog)
    return Checked(tuple(errors), (*_density(table, recipe), *_repeats(table), *_off_kick(table)))
