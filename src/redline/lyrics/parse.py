"""Reading lyrics files: front matter, sections, lines and adlibs."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from redline.lyrics.normalize import clean

KINDS = {
    "intro": "intro",
    "интро": "intro",
    "інтро": "intro",
    "вступление": "intro",
    "hook": "hook",
    "chorus": "hook",
    "refrain": "hook",
    "припев": "hook",
    "приспів": "hook",
    "хук": "hook",
    "рефрен": "hook",
    "pre-hook": "prehook",
    "pre-chorus": "prehook",
    "prehook": "prehook",
    "предприпев": "prehook",
    "пред-припев": "prehook",
    "пре-хук": "prehook",
    "verse": "verse",
    "куплет": "verse",
    "часть": "verse",
    "bridge": "bridge",
    "бридж": "bridge",
    "переход": "bridge",
    "outro": "outro",
    "аутро": "outro",
    "концовка": "outro",
}
HEADER = re.compile(r"^\[([^\]]*)\]\s*(.*)$")
ADLIB = re.compile(r"\(([^()]*)\)")


class LyricsError(ValueError):
    """A lyrics file that cannot be read."""


@dataclass(frozen=True)
class Line:
    """One sung line: as written, without the parentheses, and what was in them."""

    text: str
    plain: str
    adlibs: tuple[str, ...]


@dataclass
class Section:
    """A run of lines under one header."""

    kind: str
    name: str
    who: str
    lines: list[Line] = field(default_factory=list)


@dataclass(frozen=True)
class Track:
    """A parsed lyrics file."""

    meta: dict[str, Any]
    sections: list[Section]
    headerless: bool
    mine: bool


@dataclass(frozen=True)
class Artist:
    """An artist of the base: names to match performers by, and the style group."""

    names: tuple[str, ...]
    profile: str


def _line(text: str) -> Line:
    adlibs = tuple(a.strip() for a in ADLIB.findall(text) if a.strip())
    plain = " ".join(ADLIB.sub(" ", text).split())
    return Line(text, plain, adlibs)


def _header(inside: str) -> tuple[str, str, str]:
    name, _, who = inside.partition(":")
    name = re.sub(r"\s*\d+\s*$", "", name.strip())
    first = name.split()[0].lower() if name.split() else ""
    return KINDS.get(first, "other"), name, who.strip()


def _sections(body: str, mine: bool) -> tuple[list[Section], bool]:
    sections: list[Section] = []
    headerless = True
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = HEADER.match(line)
        if match:
            inside, rest = match.groups()
            if inside.strip().lower().startswith("текст"):
                continue
            kind, name, who = _header(inside)
            # the owner's bracket lines that name no section are performance notes
            if not (mine and kind == "other"):
                headerless = False
                sections.append(Section(kind, name, "" if mine else who))
            line = rest.strip()
            if not line:
                continue
        if not sections:
            sections.append(Section("other", "", ""))
        sections[-1].lines.append(_line(line))
    if mine:
        _repeat_empty(sections)
    return sections, headerless


def _repeat_empty(sections: list[Section]) -> None:
    # an empty header in the owner's texts means "this section again"
    for i, section in enumerate(sections):
        if not section.lines:
            earlier = [s for s in sections[:i] if s.name.lower() == section.name.lower()]
            if earlier and earlier[-1].lines:
                section.lines = list(earlier[-1].lines)


def _unify(text: str) -> str:
    # one Unicode form, no BOM or invisible characters, \n line ends
    return clean(text).replace("\r\n", "\n").replace("\r", "\n")


def _split(text: str) -> tuple[str | None, str]:
    text = _unify(text)
    if not text.startswith("---\n"):
        return None, text
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        return None, text
    return head, body


def _front_matter(head: str) -> dict[str, Any] | None:
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError:
        return None
    if isinstance(meta, dict) and meta.get("artist") and meta.get("track"):
        return meta
    return None


def parse_text(text: str, mine: bool = False) -> Track:
    """Parse a text with or without front matter, as for checking a new text.

    A leading block between --- lines that is not front matter with artist and track is
    read as text, so nothing in it escapes a check.
    """
    head, body = _split(text)
    meta = _front_matter(head) if head is not None else None
    if meta is None:
        meta, body = {}, _unify(text)
    sections, headerless = _sections(body, mine)
    return Track(meta, sections, headerless, mine)


def read_track(path: Path, mine: bool = False) -> Track:
    """Read a file of the base; it must have front matter with artist and track."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise LyricsError(f"{path}: {error}") from error
    head, body = _split(text)
    if head is None:
        raise LyricsError(f"{path}: no front matter between --- lines")
    try:
        meta = yaml.safe_load(head)
    except yaml.YAMLError as error:
        # the parser's message quotes the line, which can hold names
        mark = getattr(error, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        raise LyricsError(f"{path}: front matter is not valid YAML{where}") from None
    if not isinstance(meta, dict) or not meta.get("artist") or not meta.get("track"):
        raise LyricsError(f"{path}: front matter needs artist and track")
    sections, headerless = _sections(body, mine)
    return Track(meta, sections, headerless, mine)


def read_artists(path: Path) -> dict[str, Artist]:
    """Read the artist map: folder name to names and style group."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise LyricsError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise LyricsError(f"{path}: expected a map of artists")
    artists = {}
    for slug, entry in data.items():
        if not isinstance(entry, dict) or not entry.get("name"):
            raise LyricsError(f"{path}: {slug}: needs a name")
        names = (str(entry["name"]), *(str(a) for a in entry.get("aka") or ()))
        artists[str(slug)] = Artist(names, str(entry.get("profile") or ""))
    return artists
