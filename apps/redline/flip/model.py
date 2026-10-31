"""Flip format 1: what a flip holds, how it is parsed, and where its versions are saved."""

import fcntl
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redline.core import native
from redline.core.private import write_private
from redline.song.model import SongError, _check_relative

FORMAT = 1
MAX_BARS = 512
# a flip renders whole in memory; this many seconds of it at most
MAX_PLAY_S = 15 * 60
# the first bar line may sit this far before the file: cut off, or the fit a little early
DOWNBEAT_LEAD_S = 0.02
# the beat score's tempo range, which renders our 808 and hats
SYNTH_BPM = (40, 220)
BEATS_PER_BAR = 4
TAPE_MAX_S = 4.0
BASS_MODES = ("original", "ours", "off")
HATS_MODES = ("original", "removed", "ours")
HATS_REMOVABLE = ("hh", "ride", "crash")
EFFECTS = ("reverse", "tape_stop", "tape_start", "stutter", "granular")
RUST_PARAMS: dict[str, tuple[str, ...]] = {
    "reverse": (),
    "tape_stop": ("time", "duration", "curve", "resume"),
    "tape_start": ("time", "duration", "curve"),
    "stutter": ("length", "repeats", "fall", "step"),
    "granular": ("size", "density", "scatter", "jitter", "stretch", "seed"),
}
WHOLE = ("repeats", "seed")
PATTERN_NAME = re.compile(r"[a-z0-9_]{1,32}")
FLIP_FILE = re.compile(r"flip-(\d+)\.json")
DRAFT = "draft.json"
DRAFT_REV = "draft.rev"
DRUM_ROWS = ("kick", "snare", "clap", "hat", "hat_open", "perc")
STEPS = 16
MAX_NOTES_PER_BAR = 64


class FlipError(ValueError):
    """A flip file or field that is refused."""


@dataclass(frozen=True)
class BlockNote:
    """An 808 note of a block: `step` counts sixteenths from the block's start."""

    step: int
    length: int
    note: int
    vel: int


@dataclass(frozen=True)
class Block:
    """Source bars [from_bar, to_bar) under a label; blocks are listed in play order.

    `notes` None plays the source's notes, an empty tuple is silence; `drums` None has no drums,
    else maps a row to bars*16 characters of "x" (hit) and "." (rest).
    """

    label: str
    from_bar: int
    to_bar: int
    notes: tuple[BlockNote, ...] | None = None
    drums: dict[str, str] | None = None

    def to_json(self) -> dict[str, Any]:
        """The block's content; absent fields are left out."""
        out: dict[str, Any] = {
            "label": self.label,
            "from_bar": self.from_bar,
            "to_bar": self.to_bar,
        }
        if self.notes is not None:
            out["notes"] = [
                {"step": n.step, "len": n.length, "note": n.note, "vel": n.vel} for n in self.notes
            ]
        if self.drums is not None:
            out["drums"] = dict(self.drums)
        return out


@dataclass(frozen=True)
class Effect:
    """An effect on play-order bars [from_bar, to_bar)."""

    effect: str
    from_bar: int
    to_bar: int
    params: dict[str, float]


@dataclass(frozen=True)
class Flip:
    """Flip format 1; `source` is a path relative to the repository, resolved at every use."""

    source: str
    bpm: float
    downbeat_s: float
    swing: float
    bass_mode: str
    bass_params: dict[str, float]
    hats_mode: str
    hats_remove: tuple[str, ...]
    hats_patterns: tuple[str, ...]
    gains: dict[str, float]
    arrangement: tuple[Block, ...]
    effects: tuple[Effect, ...]
    ours_gain: float | None = None

    @property
    def play_bars(self) -> int:
        """The length of the arrangement in bars."""
        return sum(b.to_bar - b.from_bar for b in self.arrangement)

    def to_json(self) -> dict[str, Any]:
        """The file's content."""
        return {
            "format": FORMAT,
            "source": self.source,
            "grid": {"bpm": self.bpm, "downbeat_s": self.downbeat_s, "swing": self.swing},
            "bass808": {"mode": self.bass_mode, "params": dict(self.bass_params)},
            "hats": {
                "mode": self.hats_mode,
                "remove": list(self.hats_remove),
                "patterns": list(self.hats_patterns),
            },
            "gains": {**self.gains, **({} if self.ours_gain is None else {"ours": self.ours_gain})},
            "arrangement": [b.to_json() for b in self.arrangement],
            "effects": [
                {"effect": e.effect, "from_bar": e.from_bar, "to_bar": e.to_bar, "params": e.params}
                for e in self.effects
            ],
        }


def _fields(
    value: object, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FlipError(f"{where}: must be an object")
    for key in value:
        if key not in required and key not in optional:
            raise FlipError(f"{where}.{key}: unknown field")
    for key in required:
        if key not in value:
            raise FlipError(f"{where}.{key}: missing")
    return value


def _number(value: object, where: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise FlipError(f"{where}: must be a number")
    if not low <= value <= high:
        raise FlipError(f"{where}: {value} is outside {low:g}..{high:g}")
    return value


def _bar(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BARS * 64:
        raise FlipError(f"{where}: must be a whole bar number, 0 or more")
    return value


def _choice(value: object, where: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise FlipError(f"{where}: must be one of {', '.join(choices)}")
    return str(value)


def _list(value: object, where: str, limit: int) -> list[Any]:
    if not isinstance(value, list):
        raise FlipError(f"{where}: must be a list")
    if len(value) > limit:
        raise FlipError(f"{where}: at most {limit} entries")
    return value


def _source(value: object) -> str:
    try:
        return _check_relative(value, "source")
    except SongError as error:
        raise FlipError(str(error)) from None


def _bass_params(value: object) -> dict[str, float]:
    if not isinstance(value, dict) or len(value) > 32:
        raise FlipError("bass808.params: must be an object of at most 32 numbers")
    return {
        str(key): _number(item, f"bass808.params.{key}", -1e6, 1e6) for key, item in value.items()
    }


def _whole(value: object, where: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FlipError(f"{where}: must be a whole number")
    if not low <= value <= high:
        raise FlipError(f"{where}: {value} is outside {low}..{high}")
    return value


def _block_notes(value: object, where: str, bars: int) -> tuple[BlockNote, ...]:
    size = bars * STEPS
    out = []
    for i, raw in enumerate(_list(value, where, MAX_NOTES_PER_BAR * bars)):
        at = f"{where}[{i}]"
        raw = _fields(raw, at, ("step", "len", "note", "vel"))
        step = _whole(raw["step"], f"{at}.step", 0, size - 1)
        length = _whole(raw["len"], f"{at}.len", 1, size + STEPS - step)
        out.append(
            BlockNote(
                step,
                length,
                _whole(raw["note"], f"{at}.note", 0, 127),
                _whole(raw["vel"], f"{at}.vel", 1, 127),
            )
        )
    return tuple(out)


def _block_drums(value: object, where: str, bars: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise FlipError(f"{where}: must be an object")
    out = {}
    for row, text in value.items():
        if row not in DRUM_ROWS:
            raise FlipError(f"{where}.{row}: unknown row; one of {', '.join(DRUM_ROWS)}")
        if not isinstance(text, str) or len(text) != bars * STEPS or text.strip("x.") != "":
            raise FlipError(f'{where}.{row}: must be {bars * STEPS} characters of "x" and "."')
        out[row] = text
    return out


def _arrangement(value: object) -> tuple[Block, ...]:
    blocks = []
    for i, raw in enumerate(_list(value, "arrangement", MAX_BARS)):
        where = f"arrangement[{i}]"
        raw = _fields(raw, where, ("label", "from_bar", "to_bar"), ("notes", "drums"))
        label = raw["label"]
        if not isinstance(label, str) or not 1 <= len(label) <= 64 or not label.isprintable():
            raise FlipError(f"{where}.label: must be a short printable text")
        start, end = _bar(raw["from_bar"], f"{where}.from_bar"), _bar(
            raw["to_bar"], f"{where}.to_bar"
        )
        if end <= start:
            raise FlipError(f"{where}.to_bar: must be after from_bar")
        notes = drums = None
        if "notes" in raw:
            notes = _block_notes(raw["notes"], f"{where}.notes", end - start)
        if "drums" in raw:
            drums = _block_drums(raw["drums"], f"{where}.drums", end - start)
        blocks.append(Block(label, start, end, notes, drums))
    if not blocks:
        raise FlipError("arrangement: needs at least one block")
    total = sum(b.to_bar - b.from_bar for b in blocks)
    if total > MAX_BARS:
        raise FlipError(f"arrangement: {total} bars in play order; at most {MAX_BARS}")
    return tuple(blocks)


def _ranges() -> dict[str, dict[str, tuple[float, float]]]:
    listed = native.rack_effects()
    if listed is None:
        raise FlipError("effects: the native module is missing, so their ranges are unknown")
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for item in listed:
        if item["effect"] in RUST_PARAMS:
            out[item["effect"]] = {p["name"]: (p["low"], p["high"]) for p in item["params"]}
    return out


def _effect_params(name: str, raw: object, where: str, range_s: float) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise FlipError(f"{where}: must be an object")
    ranges = _ranges()[name] if RUST_PARAMS[name] else {}
    params: dict[str, float] = {}
    for key, item in raw.items():
        if key not in RUST_PARAMS[name]:
            raise FlipError(f"{where}.{key}: unknown field")
        low, high = ranges[key]
        value = _number(item, f"{where}.{key}", low, high)
        if key in WHOLE and value != int(value):
            raise FlipError(f"{where}.{key}: must be a whole number")
        params[key] = value
    if name in ("tape_stop", "tape_start"):
        duration = params.get("duration", 0.5)
        cap = min(TAPE_MAX_S, range_s)
        if duration > cap:
            raise FlipError(f"{where}.duration: {duration} is longer than {cap:.3g} s allowed here")
        if params.get("time", 0.0) >= range_s:
            raise FlipError(f"{where}.time: starts after the range ends")
    return params


def _effects(value: object, bpm: float, total: int) -> tuple[Effect, ...]:
    out = []
    for i, raw in enumerate(_list(value, "effects", 256)):
        where = f"effects[{i}]"
        raw = _fields(raw, where, ("effect", "from_bar", "to_bar", "params"))
        name = _choice(raw["effect"], f"{where}.effect", EFFECTS)
        start, end = _bar(raw["from_bar"], f"{where}.from_bar"), _bar(
            raw["to_bar"], f"{where}.to_bar"
        )
        if end <= start:
            raise FlipError(f"{where}.to_bar: must be after from_bar")
        if end > total:
            raise FlipError(f"{where}.to_bar: {end} is past the {total} bars of the arrangement")
        range_s = (end - start) * BEATS_PER_BAR * 60.0 / bpm
        params = _effect_params(name, raw["params"], f"{where}.params", range_s)
        out.append(Effect(name, start, end, params))
    return tuple(out)


def _presets() -> tuple[str, ...]:
    from redline.flip.score import HATS

    return tuple(HATS)


def synth_tempo_problem(flip: "Flip") -> str | None:
    """Why the synth cannot play this flip's own parts at its tempo; None when it can."""
    own_layer = any(b.drums or b.notes for b in flip.arrangement)
    if "ours" not in (flip.bass_mode, flip.hats_mode) and not own_layer:
        return None
    low, high = SYNTH_BPM
    if low <= flip.bpm <= high:
        return None
    return f"grid.bpm: {flip.bpm:g}; our 808, hats and drums play at {low}..{high} BPM"


def parse(data: object) -> Flip:
    """Parse a flip from JSON data; unknown or missing fields and bad values raise FlipError."""
    top = _fields(
        data,
        "flip",
        ("format", "source", "grid", "bass808", "hats", "gains", "arrangement", "effects"),
    )
    if top["format"] != FORMAT or isinstance(top["format"], bool):
        raise FlipError(f"format: {top['format']!r} is not {FORMAT}")
    source = _source(top["source"])
    grid = _fields(top["grid"], "grid", ("bpm", "downbeat_s", "swing"))
    bpm = _number(grid["bpm"], "grid.bpm", 30, 300)
    downbeat = _number(grid["downbeat_s"], "grid.downbeat_s", -DOWNBEAT_LEAD_S, 3600)
    swing = _number(grid["swing"], "grid.swing", 50, 75)
    bass = _fields(top["bass808"], "bass808", ("mode", "params"))
    bass_mode = _choice(bass["mode"], "bass808.mode", BASS_MODES)
    bass_params = _bass_params(bass["params"])
    hats = _fields(top["hats"], "hats", ("mode", "remove", "patterns"))
    hats_mode = _choice(hats["mode"], "hats.mode", HATS_MODES)
    remove = [
        _choice(item, f"hats.remove[{i}]", HATS_REMOVABLE)
        for i, item in enumerate(_list(hats["remove"], "hats.remove", len(HATS_REMOVABLE)))
    ]
    if len(set(remove)) != len(remove):
        raise FlipError("hats.remove: a stem is listed twice")
    if hats_mode != "original" and "hh" not in remove:
        raise FlipError("hats.remove: must hold hh when the hats are removed or replaced")
    patterns = _list(hats["patterns"], "hats.patterns", MAX_BARS)
    for i, item in enumerate(patterns):
        if not isinstance(item, str) or not PATTERN_NAME.fullmatch(item):
            raise FlipError(f"hats.patterns[{i}]: must be a pattern name")
        if item not in _presets():
            raise FlipError(
                f"hats.patterns[{i}]: no preset {item!r}; one of {', '.join(_presets())}"
            )
    gains = _fields(top["gains"], "gains", ("drums", "other", "bass"), ("ours",))
    gain_db = {k: _number(gains[k], f"gains.{k}", -60, 24) for k in ("drums", "other", "bass")}
    ours_gain = _number(gains["ours"], "gains.ours", -60, 24) if "ours" in gains else None
    arrangement = _arrangement(top["arrangement"])
    if hats_mode == "ours" and len(patterns) != len(arrangement):
        raise FlipError("hats.patterns: needs one pattern per arrangement block")
    play = sum(b.to_bar - b.from_bar for b in arrangement)
    if play * BEATS_PER_BAR * 60.0 / bpm > MAX_PLAY_S:
        raise FlipError(
            f"arrangement: {play} bars at {bpm:g} BPM play over {MAX_PLAY_S // 60} minutes"
        )
    effects = _effects(top["effects"], bpm, play)
    return Flip(
        source,
        bpm,
        downbeat,
        swing,
        bass_mode,
        bass_params,
        hats_mode,
        tuple(remove),
        tuple(patterns),
        gain_db,
        arrangement,
        effects,
        ours_gain,
    )


def load(path: Path) -> Flip:
    """Read and parse a flip file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FlipError(f"{path}: {error}") from None
    return parse(data)


def _text(flip: Flip) -> str:
    return json.dumps(flip.to_json(), indent=2, ensure_ascii=False) + "\n"


def _taken(folder: Path) -> int:
    found = [int(m.group(1)) for p in folder.iterdir() if (m := FLIP_FILE.fullmatch(p.name))]
    return max(found, default=0)


def save_new(folder: Path, flip: Flip) -> int:
    """Save the flip as the next flip-<n>.json, never replacing a file; returns n."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = _text(flip)
    tmp = folder / f".new.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        n = _taken(folder) + 1
        while True:
            try:
                os.link(tmp, folder / f"flip-{n}.json")
                return n
            except FileExistsError:
                n += 1
    finally:
        tmp.unlink(missing_ok=True)


def draft_rev(folder: Path) -> int:
    """How many times the draft was saved; 0 for a folder that never had one."""
    try:
        return int((folder / DRAFT_REV).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def save_draft(folder: Path, flip: Flip, base: int | None = None) -> int | None:
    """Replace draft.json atomically, readable by the owner only, and count the save.

    Args:
        folder: The flip folder of the source.
        flip: The new draft.
        base: The revision the writer last saw; when it is not the current one nothing is written.

    Returns:
        The new revision, or None when `base` is stale.
    """
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(folder / ".draft.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = draft_rev(folder)
        if base is not None and base != current:
            return None
        write_private(folder / DRAFT, _text(flip))
        write_private(folder / DRAFT_REV, f"{current + 1}\n")
        return current + 1
    finally:
        os.close(lock)
