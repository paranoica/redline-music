"""Writing a text by seed: recipe, requests to the writer, checks, rounds and state."""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from redline.lyrics.fingerprint import RUN, FingerprintIndex
from redline.lyrics.form import Problem, check_draft
from redline.lyrics.parse import LyricsError, Track, parse_text
from redline.lyrics.recipe import make_recipe, recipe_from_json, recipe_json
from redline.lyrics.writer import (
    Draft,
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


def style_for(notes: str, seed: int) -> str:
    """The style notes with each long list cut to a part chosen by seed.

    Every writer seeing the same examples writes the same jokes; a different part each seed
    keeps the texts apart.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    out = []
    for line in notes.splitlines():
        found = LISTED.match(line)
        items = found.group(2).rstrip(".").split(", ") if found else []
        if len(items) <= 2 * MIN_KEPT:
            out.append(line)
            continue
        keep = max(MIN_KEPT, len(items) // 2)
        picked = sorted(rng.choice(len(items), size=keep, replace=False).tolist())
        out.append(found.group(1) + ", ".join(items[i] for i in picked))
    return "\n".join(out)


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

    def _state(self) -> dict[str, Any] | None:
        path = self.folder / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _save_state(self, state: dict[str, Any]) -> None:
        _write(self.folder / "state.json", json.dumps(state, ensure_ascii=False, indent=1))

    def _start(self, seed: int, topic: str | None) -> dict[str, Any]:
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        for old in self.folder.glob("*"):
            old.unlink()
        recipe = make_recipe(self.profile, seed, topic)
        _write(self.folder / "recipe.json", recipe_json(recipe))
        state = {
            "seed": seed,
            "topic": recipe.topic,
            "asked_topic": topic,
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
        made = self.folder.parent.glob("*/text.txt")
        return [
            (MADE, parse_text(p.read_text(encoding="utf-8")))
            for p in made
            if p.parent != self.folder
        ]

    def _copies(self, track: Track) -> list[Problem]:
        matches = self.index.check(track, self.min_words)
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
        self, state: dict[str, Any], seed: int, topic: str | None, force: bool
    ) -> Saved | Failed | None:
        """A saved or given-up seed's answer; None when the seed goes on or starts over."""
        if not (isinstance(state.get("round"), int) and 1 <= state["round"] <= ROUNDS):
            raise LyricsError(f"seed {seed}: its state file is damaged; --force to restart it")
        if state["profile"] != self.profile_name:
            raise LyricsError(f"seed {seed} is written from profile {state['profile']!r}")
        other_topic = bool(topic) and topic not in (state["asked_topic"], state["topic"])
        if state["status"] == "saved":
            if force:
                raise LyricsError(f"seed {seed} is saved; it is final")
            if other_topic:
                raise LyricsError(f"seed {seed} is about {state['topic']!r}")
            path = self.folder / "text.txt"
            return Saved(path, path.read_text(encoding="utf-8"))
        if force:
            return None
        if other_topic:
            raise LyricsError(
                f"seed {seed} was started about {state['topic']!r}; --force to change it"
            )
        if state["status"] in ("failed", "refused"):
            return Failed(state["status"], tuple(state["reasons"]))
        if state["profile_digest"] != self.profile["meta"]["digest"]:
            raise LyricsError(
                f"the profile changed since seed {seed} started; --force to restart it"
            )
        return None

    def run(
        self, seed: int, topic: str | None = None, force: bool = False
    ) -> Saved | Pending | Failed:
        """Take the seed one step on: start it, wait, check a draft, ask again or save."""
        state = self._state()
        if state:
            settled = self._settled(state, seed, topic, force)
            if settled:
                return settled
        if state is None or force:
            state = self._start(seed, topic or (state or {}).get("asked_topic"))
        recipe = recipe_from_json((self.folder / "recipe.json").read_text(encoding="utf-8"))
        request = WriterRequest(
            prompt_text(),
            recipe,
            excerpts(self.mine, self.index, seed),
            tuple(state["reasons"]),
            state["round"],
            style_for(self.style, seed),
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
        problems, stats = check_draft(draft.text, recipe, self.mark)
        if not any(p.kind == "not lyrics" for p in problems):
            problems += self._copies(parse_text(draft.text))
        reasons = [f"{p.kind}, {p.where}: {p.detail}" for p in problems]
        checks = {
            "round": state["round"],
            "prompt": prompt_hash(),
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
            _write(self.folder / "text.txt", draft.text)
            state.update(status="saved", reasons=[])
            self._save_state(state)
            return Saved(self.folder / "text.txt", draft.text)
        if state["round"] >= ROUNDS:
            state.update(status="failed", reasons=reasons)
            self._save_state(state)
            return Failed("failed", tuple(reasons))
        state.update(round=state["round"] + 1, reasons=reasons)
        self._save_state(state)
        return self.run(state["seed"])
