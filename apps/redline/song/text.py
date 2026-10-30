"""Saving a hand-edited text as the next version of a seed, after the Editor's own checks."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redline.lyrics.base.fingerprint import DEFAULT_MIN_WORDS, RUN, FingerprintIndex
from redline.lyrics.text.parse import LyricsError, parse_text
from redline.lyrics.write.form import StressMark, check_draft
from redline.lyrics.write.generate import Session, create_new, versions
from redline.lyrics.write.recipe import recipe_from_json
from redline.lyrics.write.title import split_title
from redline.lyrics.write.writer import HandoffWriter

CHECKED = ("not lyrics", "length", "places")


@dataclass(frozen=True)
class TextError:
    """Why a hand edit was not saved: a kind, the place in the text, and a message."""

    kind: str
    place: str
    message: str


@dataclass(frozen=True)
class HandEdit:
    """The outcome of a save: the new version and its file, or the errors."""

    version: int | None = None
    path: Path | None = None
    errors: tuple[TextError, ...] = ()

    @property
    def saved(self) -> bool:
        """The text was written."""
        return self.path is not None


def _refuse(kind: str, message: str, place: str = "the seed") -> HandEdit:
    return HandEdit(errors=(TextError(kind, place, message),))


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _unfit(state: Any, edits: Any, have: dict[int, Path], base: int) -> HandEdit | None:
    if not isinstance(state, dict) or state.get("status") != "saved" or not have:
        return _refuse("no text", "the seed has no saved text to edit")
    if not isinstance(edits, dict) or edits.get("pending") is not None:
        return _refuse("pending", "an edit by the writer is waiting; finish or drop it first")
    if base not in have:
        return _refuse("base", f"there is no version {base} of this seed")
    min_words = state.get("min_words")
    if not (isinstance(min_words, int) and RUN <= min_words <= DEFAULT_MIN_WORDS):
        return _refuse("damaged", "the seed's state holds no usable copy threshold")
    return None


def save_hand_edit(
    seed_folder: Path,
    base_version: int,
    text: str,
    *,
    index: FingerprintIndex | None,
    profile: dict[str, Any] | None,
    mark: StressMark = str,
    lyrics: Path | None = None,
    build: Path | None = None,
) -> HandEdit:
    """Save a text written by hand as the seed's next text-<n>.txt, if it passes the checks.

    The checks are the Editor's: "not lyrics", "length" and "places" against the seed's recipe,
    then no run of the seed's stored min_words words shared with an existing text.

    Args:
        seed_folder: The seed's folder, with its state, recipe and saved texts.
        base_version: The version the edit started from; it must exist.
        text: The new text.
        index: The loaded fingerprint index; None when it could not be loaded.
        profile: The loaded profile; None when it could not be loaded.
        mark: The stress marker the rhyme checks use.
        lyrics: The lyrics base, named in the command a refusal gives.
        build: The index folder, named in the same command.

    Returns:
        The saved version, or the errors that refused it. Nothing is raised for a refusal.
    """
    if index is None or profile is None:
        missing = "index" if index is None else "profile"
        root, out = lyrics or "<lyrics folder>", build or "<build folder>"
        return _refuse(
            f"no {missing}",
            f"the lyrics {missing} is missing; build it: redline lyrics index "
            f"--lyrics {root} --build {out}",
        )
    try:
        state = _json(seed_folder / "state.json")
        edits = _json(seed_folder / "edits.json") or {"done": []}
        recipe = recipe_from_json((seed_folder / "recipe.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return _refuse("damaged", "the seed's files cannot be read")
    have = versions(seed_folder)
    refusal = _unfit(state, edits, have, base_version)
    if refusal:
        return refusal
    min_words = state["min_words"]
    title, body = split_title(text)
    problems, _ = check_draft(body, recipe, mark)
    problems = [p for p in problems if p.kind in CHECKED]
    if not any(p.kind == "not lyrics" for p in problems):
        try:
            session = Session(
                seed_folder,
                profile,
                str(state.get("profile")),
                index,
                [],
                HandoffWriter(seed_folder),
                mark,
                min_words,
            )
        except LyricsError as error:
            return _refuse("no index", f"{error}; rebuild it: redline lyrics index")
        problems += session.copies(parse_text(body))
    if problems:
        return HandEdit(errors=tuple(TextError(p.kind, p.where, p.detail) for p in problems))
    version = max(have) + 1
    path = seed_folder / f"text-{version}.txt"
    try:
        create_new(path, body)
    except (LyricsError, OSError) as error:
        return _refuse("exists", str(error), path.name)
    if title:
        try:
            create_new(seed_folder / f"title-{version}.txt", title + "\n")
        except (LyricsError, OSError) as error:
            path.unlink(missing_ok=True)
            return _refuse("exists", str(error), f"title-{version}.txt")
    return HandEdit(version, path)
