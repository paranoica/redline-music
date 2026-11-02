"""Writing a beat by seed: the recipe, requests to the beatmaker agent, checks and rounds.

A seed's folder holds `recipe.json`, `state.json`, `request-N.md` (the request of round N),
`score-draft-N.json` (the agent's answer) and the saved `score-K.json` files.
"""

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import redline_dsp

from redline.beat import library, pack
from redline.beat.checks import Caution, check_score, note_name
from redline.beat.pack import PackSound
from redline.beat.recipe import (
    BASS_STYLES,
    CHARACTER,
    DEVICES,
    GENRES,
    Recipe,
    make_recipe,
    pack_kit,
    parse_key,
    recipe_from_json,
    recipe_json,
)
from redline.core.private import write_private

PROMPT = Path(__file__).with_name("beatmaker_prompt.md")
ROOT = Path("private/beats")
MARKS = "marks.jsonl"
REFUSAL = "REFUSED:"
ROUNDS = 4
MARKS_EACH = 4
SAVED = re.compile(r"score-(\d+)\.json")
FENCE = re.compile(r"\A```(?:json)?\n(.*)\n```\Z", re.S)


class BeatError(Exception):
    """A seed that cannot go on as asked."""


@dataclass(frozen=True)
class Saved:
    """The seed's score, checked and saved, with the warnings it kept."""

    path: Path
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pending:
    """The request is out; run again once the agent has written the draft."""

    request: Path
    draft: Path


@dataclass(frozen=True)
class Failed:
    """The seed ran out of rounds, or the agent refused."""

    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Marks:
    """The owner's marks on bars of earlier beats of a genre."""

    bad: tuple[dict[str, Any], ...] = ()
    good: tuple[dict[str, Any], ...] = ()


def _write(path: Path, text: str) -> None:
    write_private(path, text)


def write_new_score(folder: Path, number: int, text: str) -> str:
    """Write text as `score-<number>.json`, or the next free number; never replaces a file.

    The file is created readable by the owner only. Both the beat writer and the studio save
    through here, so a name taken by one between picking the number and writing is skipped.

    Returns:
        The name written.
    """
    tmp = folder / f".score.{os.getpid()}.{threading.get_ident()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        while True:
            name = f"score-{number}.json"
            try:
                # a link, unlike a rename, fails when the name is taken
                os.link(tmp, folder / name)
                return name
            except FileExistsError:
                number += 1
    finally:
        tmp.unlink(missing_ok=True)


def prompt_text() -> str:
    """The beatmaker's prompt."""
    return PROMPT.read_text(encoding="utf-8")


def prompt_hash() -> str:
    """A short hash of the prompt, kept with every score written under it."""
    return hashlib.sha256(prompt_text().encode()).hexdigest()[:12]


def read_marks(root: Path, genre: str) -> Marks:
    """The last few bad and good marks of the genre from `<root>/marks.jsonl`.

    Lines that are not a mark with an excerpt are skipped: the file is another tool's.
    """
    path = root / MARKS
    if not path.exists():
        return Marks()
    found: dict[str, list[dict[str, Any]]] = {"bad": [], "good": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            mark = json.loads(line)
        except ValueError:
            continue
        if not isinstance(mark, dict) or mark.get("genre") != genre:
            continue
        verdict = mark.get("verdict")
        if verdict in found and isinstance(mark.get("excerpt"), str):
            found[verdict].append(mark)
    return Marks(tuple(found["bad"][-MARKS_EACH:]), tuple(found["good"][-MARKS_EACH:]))


def _param(p: dict[str, Any]) -> str:
    if p.get("choices"):
        return f"{p['name']} ({'|'.join(p['choices'])}, default {p['choices'][int(p['default'])]})"
    unit = f" {p['unit']}" if p.get("unit") else ""
    return f"{p['name']} {p['low']:g}..{p['high']:g}{unit} (default {p['default']:g})"


def _instruments() -> list[str]:
    out = []
    for item in json.loads(redline_dsp.beat_instruments()):
        kind = "drum, a step string" if item["kind"] == "drum" else "notes"
        mono = ", mono (overlap glides)" if item.get("mono") else ""
        params = "; ".join(_param(p) for p in item["params"])
        out.append(f"- {item['instrument']} ({kind}{mono}): {params}")
    return out


def _effects() -> list[str]:
    return [
        f"- {e['effect']}: {'; '.join(_param(p) for p in e['params']) or 'no params'}"
        for e in json.loads(redline_dsp.rack_effects())
        if not e["offline_only"]
    ]


def _recipe(recipe: Recipe) -> list[str]:
    genre = GENRES[recipe.genre]
    feel = genre.feel or (
        "half time: the snare on beat 3" if recipe.half_time else "the snare on 2 and 4"
    )
    out = [
        "```json",
        recipe_json(recipe),
        "```",
        "",
        f"- Genre: {recipe.genre} ({feel}). Its facts are from producer blogs and tutorials, "
        "unverified; "
        f"own choices: {', '.join(genre.invented)}.",
        f"- Tempo {recipe.bpm:g} BPM, key {recipe.key}, swing {recipe.swing:g}, seed "
        f"{recipe.seed}.",
        f"- Hats: {genre.hats}. Keep {recipe.hats_per_bar[0]:g} to "
        f"{recipe.hats_per_bar[1]:g} hat hits a bar in a section with hats.",
        f"- 808: {genre.bass}.",
        f"- Kick: {genre.kick}. Keep {recipe.kicks_per_bar[0]:g} to "
        f"{recipe.kicks_per_bar[1]:g} kick hits a bar.",
        "- Sounds: "
        + "; ".join(
            f"{s.name} = {s.instrument}"
            + (f" {dict(s.params)}" if s.params else "")
            + (f", library {s.library}" if s.library else "")
            for s in recipe.sounds
        ),
        "- Structure: " + ", ".join(f"{p.name} {p.bars}" for p in recipe.structure),
        f"- Total bars: {recipe.bars[0]} to {recipe.bars[1]}.",
        "- Devices, all of them:",
    ]
    out += [f"  - {DEVICES[d].text} ({DEVICES[d].source})" for d in recipe.devices]
    if any(s.name == "lead" for s in recipe.sounds):
        out.append(
            "- `lead` is the beat's main melodic voice: write the melody on it, do not swap it "
            "for a bell."
        )
    if recipe.bass_style:
        style = BASS_STYLES[recipe.bass_style][0]
        out.append(f"- 808 style ({recipe.bass_style}): {style.text} ({style.source}).")
    if recipe.character:
        out.append(
            "- Character, all of them: this beat's own moves; where one clashes with a device "
            'or with "Making it musical", the move wins.'
        )
        out += [f"  - {CHARACTER[c].text} ({CHARACTER[c].source})" for c in recipe.character]
    return out


def _marks(marks: Marks) -> list[str]:
    if not (marks.bad or marks.good):
        return []
    out = ["", "## The owner's marks on bars of earlier beats of this genre", ""]
    for title, items in (
        ("Bars the owner marked bad: do not write bars like these.", marks.bad),
        ("Bars the owner marked good: the level to hold, not a pattern to copy.", marks.good),
    ):
        if not items:
            continue
        out += [title, ""]
        for mark in items:
            note = str(mark.get("note") or "").strip()
            out += [f"- {note or 'no note'}:", "```", mark["excerpt"].rstrip(), "```"]
        out.append("")
    return out


def _sent_back(state: dict[str, Any], previous: str | None) -> list[str]:
    if not (state["reasons"] or state["warnings"]):
        return []
    out = ["", f"## Round {state['round']}: the last draft was sent back", ""]
    if state["reasons"]:
        out += ["Errors (each must be fixed):", "", *[f"- {r}" for r in state["reasons"]], ""]
    if state["warnings"]:
        out += ["Warnings (fix them unless the recipe asks otherwise):", ""]
        out += [f"- {w}" for w in state["warnings"]]
        out.append("")
    if previous is not None:
        out += ["The last draft:", "", "```json", previous.rstrip(), "```", ""]
    out.append("Write the whole score again with these fixed.")
    return out


def render(
    recipe: Recipe,
    marks: Marks,
    state: dict[str, Any],
    draft: Path,
    previous: str | None,
    catalog: Path | None = None,
) -> str:
    """The request of a round as one Markdown page."""
    out = [prompt_text().rstrip(), "", "## Recipe", "", *_recipe(recipe)]
    out += _pack_lines(recipe, pack.CATALOG if catalog is None else catalog)
    out += ["", "## Instruments and their params", "", *_instruments()]
    out += ["", "## Effects and their params", "", *_effects()]
    out += _marks(marks)
    out += _sent_back(state, previous)
    out += ["", f"Write the score JSON, and nothing else, to `{draft}`."]
    return "\n".join(out) + "\n"


def _unfenced(text: str) -> str:
    found = FENCE.match(text.strip())
    return found.group(1) if found else text


def _next_score(folder: Path) -> Path:
    taken = [int(m.group(1)) for p in folder.iterdir() if (m := SAVED.fullmatch(p.name))]
    return folder / f"score-{max(taken, default=0) + 1}.json"


def _other_ask(recipe: Recipe, genre: str | None, bpm: float | None, key: str | None) -> str:
    """How the options differ from the started recipe; empty when they do not."""
    differ = (
        (genre is not None and genre != recipe.genre)
        or (bpm is not None and bpm != recipe.bpm)
        or (key is not None and parse_key(key) != parse_key(recipe.key))
    )
    if not differ:
        return ""
    return f"as {recipe.genre} at {recipe.bpm:g} BPM in {recipe.key}"


class Session:
    """One seed's folder and its rounds."""

    def __init__(self, root: Path, seed: int, catalog: Path | None = None) -> None:
        """Work on `<root>/<seed>/`; `root` also holds the marks file.

        `catalog` is the owner's pack catalogue a kit is drawn from (default: `pack.CATALOG`).
        """
        self.root = root
        self.seed = seed
        self.folder = root / str(seed)
        self.catalog = pack.CATALOG if catalog is None else catalog

    def _state(self) -> dict[str, Any] | None:
        path = self.folder / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save_state(self, state: dict[str, Any]) -> None:
        _write(self.folder / "state.json", json.dumps(state, ensure_ascii=False, indent=1))

    def _start(
        self, genre: str | None, bpm: float | None, key: str | None, kit: str
    ) -> dict[str, Any]:
        recipe = make_recipe(self.seed, genre, bpm, key, _kit(kit, self.catalog))
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir leaves a folder that already exists at its old mode
        self.folder.chmod(0o700)
        # saved scores stay: a restarted seed saves the next free score-K
        for old in self.folder.iterdir():
            if not SAVED.fullmatch(old.name):
                old.unlink()
        _write(self.folder / "recipe.json", recipe_json(recipe))
        state = {
            "seed": self.seed,
            "prompt": prompt_hash(),
            "round": 1,
            "status": "pending",
            "reasons": [],
            "warnings": [],
            "score": None,
        }
        self._save_state(state)
        return state

    def _settled(self, state: dict[str, Any]) -> Saved | Failed | None:
        if not (isinstance(state.get("round"), int) and 1 <= state["round"] <= ROUNDS):
            raise BeatError(f"seed {self.seed}: its state file is damaged; --force to restart it")
        if state["status"] == "saved":
            return Saved(self.folder / state["score"], tuple(state["warnings"]))
        if state["status"] in ("failed", "refused"):
            return Failed(state["status"], tuple(state["reasons"]))
        return None

    def run(
        self,
        genre: str | None = None,
        bpm: float | None = None,
        key: str | None = None,
        force: bool = False,
        kit: str = "auto",
    ) -> Saved | Pending | Failed:
        """Take the seed one step on: start it, wait, check a draft, ask again or save."""
        state = self._state()
        if state is not None and not force:
            recipe = recipe_from_json((self.folder / "recipe.json").read_text(encoding="utf-8"))
            other = _other_ask(recipe, genre, bpm, key)
            if other:
                raise BeatError(f"seed {self.seed} was started {other}; --force to start over")
            settled = self._settled(state)
            if settled is not None:
                return settled
        else:
            state = self._start(genre, bpm, key, kit)
        recipe = recipe_from_json((self.folder / "recipe.json").read_text(encoding="utf-8"))
        while True:
            draft = self.folder / f"score-draft-{state['round']}.json"
            if not draft.exists():
                return self._ask(recipe, state, draft)
            text = draft.read_text(encoding="utf-8")
            if text.lstrip().startswith(REFUSAL):
                reason = text.lstrip()[len(REFUSAL) :].strip()
                state.update(status="refused", reasons=[reason], warnings=[])
                self._save_state(state)
                return Failed("refused", (reason,))
            done = self._judge(recipe, state, _unfenced(text))
            if done is not None:
                return done

    def _ask(self, recipe: Recipe, state: dict[str, Any], draft: Path) -> Pending:
        request = self.folder / f"request-{state['round']}.md"
        if not request.exists():
            last = self.folder / f"score-draft-{state['round'] - 1}.json"
            previous = last.read_text(encoding="utf-8") if last.exists() else None
            marks = read_marks(self.root, recipe.genre)
            _write(request, render(recipe, marks, state, draft, previous, self.catalog))
        return Pending(request, draft)

    def _judge(self, recipe: Recipe, state: dict[str, Any], text: str) -> Saved | Failed | None:
        """Check a draft: save it, fail the seed, or move to the next round (None)."""
        checked = check_score(text, recipe)
        warnings = [str(w) for w in checked.warnings]
        _write(
            self.folder / f"checks-{state['round']}.json",
            json.dumps(
                {
                    "errors": list(checked.errors),
                    "warnings": [_caution(w) for w in checked.warnings],
                },
                ensure_ascii=False,
                indent=1,
            ),
        )
        if checked.ok:
            first = int(_next_score(self.folder).stem.removeprefix("score-"))
            body = json.dumps(json.loads(text), ensure_ascii=False, indent=1) + "\n"
            path = self.folder / write_new_score(self.folder, first, body)
            state.update(status="saved", reasons=[], warnings=warnings, score=path.name)
            self._save_state(state)
            return Saved(path, tuple(warnings))
        if state["round"] >= ROUNDS:
            state.update(status="failed", reasons=list(checked.errors), warnings=warnings)
            self._save_state(state)
            return Failed("failed", checked.errors)
        state.update(round=state["round"] + 1, reasons=list(checked.errors), warnings=warnings)
        self._save_state(state)
        return None


def _caution(w: Caution) -> dict[str, str]:
    return {"kind": w.kind, "where": w.where, "message": w.message, "source": w.source}


def write_beat(
    root: Path,
    seed: int,
    genre: str | None = None,
    bpm: float | None = None,
    key: str | None = None,
    force: bool = False,
    kit: str = "auto",
) -> Saved | Pending | Failed:
    """Take a seed's beat one step on.

    Args:
        root: The beats folder; the seed works in `<root>/<seed>/`, marks are read from
            `<root>/marks.jsonl`.
        seed: The beat's seed.
        genre: A genre, or None to draw one (a started seed keeps its own).
        bpm: A tempo, or None to draw one.
        key: A key like `F# minor`, or None to draw one.
        force: Start the seed over; its saved scores stay and the next one takes the next
            free number.
        kit: Where a new seed's drums and 808 come from: ``synth``, ``pack`` (the owner's
            scanned pack) or ``auto`` (the pack when it holds a kick, a snare or clap and a
            closed hat).

    Raises:
        BeatError: Options other than the started seed's, without `force`, or a pack kit
            asked of a pack that cannot make one.
        RecipeError: An option the recipe cannot take.
    """
    return Session(root, seed).run(genre, bpm, key, force, kit)


KITS = ("auto", "synth", "pack")


def _kit(kit: str, catalog: Path) -> dict[str, list[PackSound]] | None:
    """The pack's sounds by category for a kit, or None for a synthesised one."""
    if kit not in KITS:
        raise BeatError(f"no kit {kit!r}: one of {', '.join(KITS)}")
    if kit == "synth":
        return None
    try:
        sounds = pack_kit(pack.load(catalog).values())
    except pack.PackError as error:
        raise BeatError(str(error)) from error
    usable = bool(sounds.get("kick")) and bool(sounds.get("snare") or sounds.get("clap"))
    usable = usable and bool(sounds.get("hat_closed"))
    if usable:
        return sounds
    if kit == "pack":
        raise BeatError(
            "the pack has no kick, snare or clap and closed hat to make a kit of: put a pack in "
            "input/sounds and run `redline beat sounds scan`"
        )
    return None


def _pack_lines(recipe: Recipe, catalog: Path) -> list[str]:
    """Which pack file each pack sound plays, for the beatmaker to judge its character."""
    used = [s for s in recipe.sounds if library.is_pack(s.library or "")]
    if not used:
        return []
    try:
        entries = pack.load(catalog)
    except pack.PackError:
        return []
    out = ["- From the owner's pack (play them as the drums and 808 they are):"]
    for s in used:
        entry = entries.get((s.library or "")[len(library.PACK_PREFIX) :])
        if entry is None:
            continue
        root = f", root {note_name(entry.note)}" if entry.note is not None else ""
        out.append(f"  - {s.name} = {entry.path} ({entry.category}{root}, {s.instrument})")
    return out
