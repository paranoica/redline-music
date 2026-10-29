"""Writing a text by seed: recipe, requests to the writer, checks, rounds and state."""

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

import numpy as np

from redline.lyrics.base.fingerprint import RUN, FingerprintIndex
from redline.lyrics.marks.store import marked_lines
from redline.lyrics.text.parse import LyricsError, Track, parse_text
from redline.lyrics.write.form import Problem, check_draft
from redline.lyrics.write.recipe import make_recipe, recipe_from_json, recipe_json
from redline.lyrics.write.title import check_title, split_title
from redline.lyrics.write.worn import worn_problems, worn_words
from redline.lyrics.write.writer import (
    Draft,
    HandoffWriter,
    Refusal,
    Waiting,
    Writer,
    WriterRequest,
    prompt_hash,
    prompt_text,
)

ROUNDS = 4
EXCERPTS = 2
EXCERPT_LINES = (4, 8)
OWNER = "mine"
MADE = "generated"
Stresser = Callable[[str], str]


@dataclass(frozen=True)
class Saved:
    """The seed's text, final."""

    path: Path
    text: str


@dataclass(frozen=True)
class Pending:
    """The request is out; run again once the writer has answered."""

    request: Path
    draft: Path


@dataclass(frozen=True)
class Failed:
    """The seed ran out of rounds, or the writer refused."""

    status: str
    reasons: tuple[str, ...]


def _write(path: Path, text: str) -> None:
    # written aside and moved in, readable by the owner only: these files hold private text
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def excerpts(mine: Sequence[Track], index: FingerprintIndex, seed: int) -> tuple[str, ...]:
    """Up to two stretches of the owner's own sections, chosen by seed.

    A stretch that shares five words with another artist's text is never shown.
    """
    candidates = []
    low, high = EXCERPT_LINES
    for track in mine:
        for section in track.sections:
            sung = [line.text for line in section.lines if line.plain]
            if section.who or len(sung) < low:
                continue
            candidates.append("\n".join(sung[:high]))
    # the excerpt is the owner's own text, so only other artists' texts may not share a run
    clean = [
        text
        for text in dict.fromkeys(candidates)
        if not index.check(parse_text(text), min_words=RUN, ignore=(OWNER, MADE))
    ]
    if not clean:
        return ()
    rng = np.random.Generator(np.random.PCG64(seed))
    picked = rng.choice(len(clean), size=min(EXCERPTS, len(clean)), replace=False)
    return tuple(clean[int(i)] for i in sorted(picked))


LISTED = re.compile(r"^(- [^:]+: )(.+)$")
MIN_KEPT = 3
# a bullet with this many comma-separated names is a list; fewer is a sentence
MIN_LIST = 10


def _items(text: str) -> list[str]:
    """Comma-separated items, commas inside brackets left alone."""
    items, depth, current = [], 0, ""
    for char in text:
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    return [*items, current.strip()]


def style_for(notes: str, seed: int) -> str:
    """The style notes with each long list of names cut to a part chosen by seed.

    Every writer seeing the same examples writes the same jokes; a different part each seed
    keeps the texts apart. Shorter bullets are left whole: they read as sentences.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    out = []
    for line in notes.splitlines():
        found = LISTED.match(line)
        items = _items(found.group(2).rstrip(".")) if found else []
        if found is None or len(items) < MIN_LIST:
            out.append(line)
            continue
        keep = max(MIN_KEPT, len(items) // 2)
        picked = sorted(rng.choice(len(items), size=keep, replace=False).tolist())
        out.append(found.group(1) + ", ".join(items[i] for i in picked))
    return "\n".join(out)


# a re-measured beat may land a tenth of a beat per minute off
BPM_SLACK = 0.5


def _other_ask(
    state: dict[str, Any], topic: str | None, bpm: float | None, delivery: str | None = None
) -> str | None:
    """How a started seed differs from what is asked now; None when it does not."""
    if topic and topic not in (state["asked_topic"], state["topic"]):
        return f"about {state['topic']!r}"
    started = state.get("bpm")
    if bpm is not None and (started is None or abs(bpm - started) > BPM_SLACK):
        return f"for a beat at {started:g} BPM" if started else "without a beat"
    # seeds started before deliveries were kept have no say
    if delivery is not None and "delivery" in state and delivery != state["delivery"]:
        return f"with a {state.get('delivery') or 'plain'} delivery"
    return None


class Session:
    """One seed's folder: its frozen recipe, state, requests and drafts."""

    def __init__(
        self,
        folder: Path,
        profile: dict[str, Any],
        profile_name: str,
        index: FingerprintIndex,
        mine: Sequence[Track],
        writer: Writer,
        mark: Stresser = str,
        min_words: int = 8,
        style: str = "",
    ) -> None:
        """Hold what one seed needs; `style` is the private notes on the scene's style."""
        if OWNER not in index.artists:
            raise LyricsError("the lyrics index holds no texts of the owner; rebuild it with them")
        self.folder = folder
        self.profile = profile
        self.profile_name = profile_name
        self.index = index
        self.mine = mine
        self.writer = writer
        self.mark = mark
        self.min_words = min_words
        self.style = style
        if style.strip() and index.check(
            parse_text("\n" + style), min_words=RUN, ignore=(OWNER, MADE)
        ):
            raise LyricsError("the style notes hold a stretch of another artist's text")

    def _state(self) -> dict[str, Any] | None:
        path = self.folder / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save_state(self, state: dict[str, Any]) -> None:
        _write(self.folder / "state.json", json.dumps(state, ensure_ascii=False, indent=1))

    def _start(
        self,
        seed: int,
        topic: str | None,
        bpm: float | None,
        bars: int | None,
        delivery: str | None = None,
    ) -> dict[str, Any]:
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for old in self.folder.glob("*"):
            old.unlink()
        worn = worn_words(self.folder.parent, skip=seed)
        recipe = make_recipe(self.profile, seed, topic, bpm, bars, delivery, worn)
        _write(self.folder / "recipe.json", recipe_json(recipe))
        state = {
            "seed": seed,
            "topic": recipe.topic,
            "asked_topic": topic,
            "bpm": bpm,
            "bars": bars,
            "delivery": delivery,
            "profile": self.profile_name,
            "profile_digest": self.profile["meta"]["digest"],
            "prompt": prompt_hash(),
            "min_words": self.min_words,
            "round": 1,
            "status": "pending",
            "reasons": [],
        }
        self._save_state(state)
        return state

    def _saved_texts(self) -> list[tuple[str, Track]]:
        made = self.folder.parent.glob("*/text*.txt")
        return [
            (MADE, parse_text(p.read_text(encoding="utf-8")))
            for p in made
            if p.parent != self.folder
        ]

    def _copies(self, track: Track) -> list[Problem]:
        # generated texts in the index include this seed's own; other seeds are read from
        # their folders below, versions and all
        matches = self.index.check(track, self.min_words, ignore=(MADE,))
        saved = self._saved_texts()
        if saved:
            matches += FingerprintIndex.build(saved, self.index.key).check(track, self.min_words)
        return [
            Problem(
                "copy",
                f"lines {m.first_line}-{m.last_line}",
                f"{m.words} words in a row are an existing text: " + " / ".join(m.lines),
            )
            for m in matches
        ]

    def _settled(
        self,
        state: dict[str, Any],
        seed: int,
        topic: str | None,
        bpm: float | None,
        force: bool,
        delivery: str | None = None,
    ) -> Saved | Failed | None:
        """A saved or given-up seed's answer; None when the seed goes on or starts over."""
        if not (isinstance(state.get("round"), int) and 1 <= state["round"] <= ROUNDS):
            raise LyricsError(f"seed {seed}: its state file is damaged; --force to restart it")
        if state["profile"] != self.profile_name:
            raise LyricsError(f"seed {seed} is written from profile {state['profile']!r}")
        other = _other_ask(state, topic, bpm, delivery)
        if state["status"] == "saved":
            if force:
                raise LyricsError(f"seed {seed} is saved; it is final")
            if other:
                raise LyricsError(f"seed {seed} is written {other}")
            path = self.folder / "text.txt"
            return Saved(path, path.read_text(encoding="utf-8"))
        if force:
            return None
        if other:
            raise LyricsError(f"seed {seed} was started {other}; --force to change it")
        if state["status"] in ("failed", "refused"):
            return Failed(state["status"], tuple(state["reasons"]))
        if state["profile_digest"] != self.profile["meta"]["digest"]:
            raise LyricsError(
                f"the profile changed since seed {seed} started; --force to restart it"
            )
        return None

    def run(
        self,
        seed: int,
        topic: str | None = None,
        force: bool = False,
        bpm: float | None = None,
        bars: int | None = None,
        delivery: str | None = None,
    ) -> Saved | Pending | Failed:
        """Take the seed one step on: start it, wait, check a draft, ask again or save.

        Args:
            seed: The text's seed.
            topic: The vibe words; None keeps the started seed's.
            force: Start a pending or failed seed over.
            bpm: The beat's half-time tempo; None keeps the started seed's.
            bars: The beat's whole bars, or None when only its tempo is known.
            delivery: "hard" or "calm" for the beat; another one for a started seed is refused.
        """
        state = self._state()
        if state:
            settled = self._settled(state, seed, topic, bpm, force, delivery)
            if settled:
                return settled
        if state is None or force:
            before = state or {}
            state = self._start(
                seed,
                topic or before.get("asked_topic"),
                bpm or before.get("bpm"),
                bars if bpm else before.get("bars"),
                delivery if bpm else before.get("delivery"),
            )
        recipe = recipe_from_json((self.folder / "recipe.json").read_text(encoding="utf-8"))
        cringe, good = marked_lines(self.folder.parent)
        request = WriterRequest(
            prompt_text(),
            recipe,
            excerpts(self.mine, self.index, seed),
            tuple(state["reasons"]),
            state["round"],
            style_for(self.style, seed),
            cringe=cringe,
            good=good,
        )
        answer = self.writer.ask(request)
        if isinstance(answer, Waiting):
            return Pending(answer.request, answer.draft)
        if isinstance(answer, Refusal):
            state.update(status="refused", reasons=[answer.reason])
            self._save_state(state)
            return Failed("refused", (answer.reason,))
        return self._judge(state, recipe, answer)

    def _judge(self, state: dict[str, Any], recipe: Any, draft: Draft) -> Saved | Pending | Failed:
        title, text = split_title(draft.text)
        problems, stats = check_draft(text, recipe, self.mark)
        if not any(p.kind == "not lyrics" for p in problems):
            problems += self._copies(parse_text(text))
            problems += worn_problems(text, recipe.worn)
            if recipe.title is not None:
                problems += check_title(title, recipe.title, text)
        reasons = [f"{p.kind}, {p.where}: {p.detail}" for p in problems]
        checks = {
            "round": state["round"],
            "prompt": prompt_hash(),
            "style": hashlib.sha256(style_for(self.style, state["seed"]).encode()).hexdigest()[:12],
            "min_words": self.min_words,
            "problems": reasons,
            "copies": sum(p.kind == "copy" for p in problems),
            **stats,
        }
        _write(
            self.folder / f"checks-{state['round']}.json",
            json.dumps(checks, ensure_ascii=False, indent=1),
        )
        if not problems:
            _write(self.folder / "checks.json", json.dumps(checks, ensure_ascii=False, indent=1))
            _write(self.folder / "text.txt", text)
            if title:
                _write(self.folder / "title.txt", title + "\n")
            state.update(status="saved", reasons=[])
            self._save_state(state)
            return Saved(self.folder / "text.txt", text)
        if state["round"] >= ROUNDS:
            state.update(status="failed", reasons=reasons)
            self._save_state(state)
            return Failed("failed", tuple(reasons))
        state.update(round=state["round"] + 1, reasons=reasons)
        self._save_state(state)
        return self.run(state["seed"])


def versions(folder: Path) -> dict[int, Path]:
    """A seed's saved texts by version: text.txt is 1, each edit adds text-<n>.txt."""
    found = {1: folder / "text.txt"} if (folder / "text.txt").exists() else {}
    for path in folder.glob("text-*.txt"):
        number = path.stem.removeprefix("text-")
        if number.isdigit() and int(number) > 1:
            found[int(number)] = path
    return dict(sorted(found.items()))


def _sound(pending: Any, have: dict[int, Path]) -> TypeGuard[dict[str, Any]]:
    return (
        isinstance(pending, dict)
        and isinstance(pending.get("round"), int)
        and 1 <= pending["round"] <= ROUNDS
        and pending.get("source") in have
        and isinstance(pending.get("version"), int)
        and isinstance(pending.get("change"), str)
        and isinstance(pending.get("reasons"), list)
    )


class Editor:
    """Changes a saved text by an instruction: the writer rewrites, the checks stay the same."""

    def __init__(self, session: Session) -> None:
        """Edit the seed the session holds."""
        self.session = session
        self.folder = session.folder

    def _state(self) -> dict[str, Any]:
        path = self.folder / "edits.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"done": []}

    def run(self, seed: int, change: str | None, force: bool = False) -> Saved | Pending | Failed:
        """Take the edit one step on: ask, wait, check, ask again or save the next version."""
        have = versions(self.folder)
        state = self.session._state()
        if not have or not state or state.get("status") != "saved":
            raise LyricsError(f"seed {seed} has no saved text to edit")
        if state.get("profile") != self.session.profile_name:
            raise LyricsError(f"seed {seed} is written from profile {state.get('profile')!r}")
        edits = self._state()
        pending = self._pending(seed, edits.get("pending"), have, change, force)
        recipe = recipe_from_json((self.folder / "recipe.json").read_text(encoding="utf-8"))
        current = have[pending["source"]].read_text(encoding="utf-8")
        target = self.folder / f"edit-{pending['version']}"
        target.mkdir(mode=0o700, exist_ok=True)
        cringe, good = marked_lines(self.folder.parent)
        request = WriterRequest(
            prompt_text(),
            recipe,
            (),
            tuple(pending["reasons"]),
            pending["round"],
            style_for(self.session.style, seed),
            current,
            pending["change"],
            pending.get("draft", ""),
            cringe=cringe,
            good=good,
        )
        answer = HandoffWriter(target).ask(request)
        if isinstance(answer, Waiting):
            edits["pending"] = pending
            self._save(edits)
            return Pending(answer.request, answer.draft)
        if isinstance(answer, Refusal):
            edits.pop("pending", None)
            self._save(edits)
            return Failed("refused", (answer.reason,))
        return self._judge(seed, edits, pending, recipe, answer)

    def _pending(
        self,
        seed: int,
        pending: Any,
        have: dict[int, Path],
        change: str | None,
        force: bool,
    ) -> dict[str, Any]:
        """The edit to work on: the waiting one, or a new one from the newest version."""
        change = (change or "").strip() or None
        sound = _sound(pending, have)
        if pending is not None and not sound and not (force and change):
            raise LyricsError(f"seed {seed}: its edits file is damaged; --force with a change")
        if sound and change and change != pending["change"] and not force:
            raise LyricsError(
                f"seed {seed} has an edit waiting: {pending['change']!r}; --force to replace it"
            )
        if sound and not (force and change):
            return pending
        if not change:
            raise LyricsError("say what to change, e.g. 'усиль хук'")
        source = max(have)
        target = self.folder / f"edit-{source + 1}"
        if target.exists():
            for old in target.glob("*"):
                old.unlink()
        return {
            "version": source + 1,
            "source": source,
            "change": change,
            "round": 1,
            "reasons": [],
        }

    def _judge(
        self, seed: int, edits: dict[str, Any], pending: dict[str, Any], recipe: Any, draft: Draft
    ) -> Saved | Pending | Failed:
        title, text = split_title(draft.text)
        problems, _ = check_draft(text, recipe, self.session.mark)
        # an edit may change the form on purpose: only what makes a text unusable sends it back
        problems = [p for p in problems if p.kind in ("not lyrics", "length", "places")]
        if not any(p.kind == "not lyrics" for p in problems):
            problems += self.session._copies(parse_text(text))
        reasons = [f"{p.kind}, {p.where}: {p.detail}" for p in problems]
        if not problems:
            path = self.folder / f"text-{pending['version']}.txt"
            _write(path, text)
            if title:
                _write(self.folder / f"title-{pending['version']}.txt", title + "\n")
            edits.pop("pending", None)
            edits["done"].append({k: pending[k] for k in ("version", "source", "change")})
            self._save(edits)
            return Saved(path, text)
        if pending["round"] >= ROUNDS:
            edits.pop("pending", None)
            self._save(edits)
            return Failed("failed", tuple(reasons))
        pending.update(round=pending["round"] + 1, reasons=reasons, draft=draft.text)
        edits["pending"] = pending
        self._save(edits)
        return self.run(seed, None)

    def _save(self, edits: dict[str, Any]) -> None:
        _write(self.folder / "edits.json", json.dumps(edits, ensure_ascii=False, indent=1))
