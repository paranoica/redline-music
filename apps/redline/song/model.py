"""Song format v1: what a song holds, where it lives, and the one writer that saves it."""

import fcntl
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from redline.core.private import make_private_dir

FORMAT = 1
ALLOWED = ("input", "private", "work", "output")
ROOT = Path(__file__).resolve().parents[3]
TAKE_FILE = re.compile(r"take-\d+\.wav", re.IGNORECASE)


class SongError(ValueError):
    """A song file, a name or a path that is refused."""


class StaleRev(SongError):
    """The song on disk moved on since it was read."""


def repo_root() -> Path:
    """The folder holding `apps/redline`; tests point ROOT elsewhere."""
    return ROOT


def check_name(name: object) -> str:
    """Return a song name that is one folder name, or raise SongError."""
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or name in (".", "..")
        or "/" in name
        or "\\" in name
        or any(ord(ch) < 32 for ch in name)
        or name.startswith(".")
    ):
        raise SongError(f"name: {name!r} is not a folder name")
    return name


def _check_relative(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise SongError(f"{where}: must be a path string")
    if "\0" in value or "\\" in value:
        raise SongError(f"{where}: {value!r} is not a path")
    path = Path(value)
    if path.is_absolute():
        raise SongError(f"{where}: {value!r} is absolute; paths are relative to the repository")
    if ".." in path.parts:
        raise SongError(f"{where}: {value!r} leaves the repository")
    if not path.parts or path.parts[0] not in ALLOWED:
        raise SongError(f"{where}: {value!r} is not under {', '.join(ALLOWED)}")
    return value


def resolve_inside(relative: str, root: Path | None = None, where: str = "path") -> Path:
    """Resolve a stored path under the root, following symlinks; refuse anything outside."""
    base = (root or repo_root()).resolve()
    _check_relative(relative, where)
    top = base / Path(relative).parts[0]
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(top):
        raise SongError(f"{where}: {relative!r} resolves outside {Path(relative).parts[0]}/")
    return resolved


def relative_to_root(path: Path | str, root: Path | None = None) -> str:
    """The stored form of a path: relative to the root as given (no symlink is followed)."""
    base = (root or repo_root()).resolve()
    joined = Path(os.path.normpath(base / path))
    if not joined.is_relative_to(base):
        raise SongError(f"path: {str(path)!r} is outside the repository")
    return joined.relative_to(base).as_posix()


@dataclass(frozen=True)
class Song:
    """Song format v1; paths are stored relative to the repository root."""

    name: str
    beat: dict[str, Any]
    text: dict[str, int] | None
    voice: dict[str, Any]
    references: tuple[str, ...]
    mix: dict[str, Any]
    rev: int = 0

    def to_json(self) -> dict[str, Any]:
        """The file's content."""
        return {
            "format": FORMAT,
            "name": self.name,
            "beat": dict(self.beat),
            "text": None if self.text is None else dict(self.text),
            "voice": dict(self.voice),
            "references": list(self.references),
            "mix": dict(self.mix),
            "rev": self.rev,
        }


def _fields(value: object, where: str, required: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SongError(f"{where}: must be an object")
    for key in value:
        if key not in required:
            raise SongError(f"{where}.{key}: unknown field")
    for key in required:
        if key not in value:
            raise SongError(f"{where}.{key}: missing")
    return value


def _count(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SongError(f"{where}: must be a whole number, 0 or more")
    return value


def _optional(value: object, where: str, check: Any) -> Any:
    return None if value is None else check(value, where)


def _parse_beat(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SongError("beat: must be an object")
    if len(value) != 1:
        raise SongError("beat: exactly one of own, file")
    key = next(iter(value))
    if key == "own":
        return {"own": _count(value["own"], "beat.own")}
    if key == "file":
        return {"file": _check_relative(value["file"], "beat.file")}
    raise SongError(f"beat.{key}: unknown field")


def _parse_voice(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SongError("voice: must be an object")
    if "suno" in value:
        value = _fields(value, "voice", ("suno",))
        return {"suno": _check_relative(value["suno"], "voice.suno")}
    value = _fields(value, "voice", ("takes", "chosen"))

    def take(chosen: object, where: str) -> str:
        if not isinstance(chosen, str) or not TAKE_FILE.fullmatch(chosen):
            raise SongError(f"{where}: {chosen!r} is not a take file name")
        return chosen

    return {
        "takes": _check_relative(value["takes"], "voice.takes"),
        "chosen": _optional(value["chosen"], "voice.chosen", take),
    }


def parse(data: object) -> Song:
    """Build a Song from decoded JSON; the first problem raises SongError with its path."""
    top = _fields(
        data, "song", ("format", "name", "beat", "text", "voice", "references", "mix", "rev")
    )
    if top["format"] != FORMAT or isinstance(top["format"], bool):
        raise SongError(f"format: {top['format']!r} is not {FORMAT}")
    name = check_name(top["name"])
    beat = _parse_beat(top["beat"])
    text = None
    if top["text"] is not None:
        raw = _fields(top["text"], "text", ("seed", "version"))
        text = {
            "seed": _count(raw["seed"], "text.seed"),
            "version": _count(raw["version"], "text.version"),
        }
    voice = _parse_voice(top["voice"])
    refs = top["references"]
    if not isinstance(refs, list):
        raise SongError("references: must be a list")
    references = tuple(_check_relative(item, f"references[{i}]") for i, item in enumerate(refs))
    raw_mix = _fields(top["mix"], "mix", ("settings", "variant"))
    mix = {
        "settings": _optional(raw_mix["settings"], "mix.settings", _check_relative),
        "variant": _optional(raw_mix["variant"], "mix.variant", _count),
    }
    return Song(name, beat, text, voice, references, mix, _count(top["rev"], "rev"))


def loads(text: str) -> Song:
    """Parse the JSON text of a song file."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SongError(f"song: not JSON ({error})") from None
    return parse(data)


def new_song(
    name: str,
    beat: dict[str, Any],
    suno: str | None = None,
    references: tuple[str, ...] = (),
) -> Song:
    """A song at rev 0 with defaults: takes in input/takes/<name>, no text, no mix yet."""
    check_name(name)
    voice: dict[str, Any] = (
        {"suno": suno} if suno else {"takes": f"input/takes/{name}", "chosen": None}
    )
    return parse(
        {
            "format": FORMAT,
            "name": name,
            "beat": beat,
            "text": None,
            "voice": voice,
            "references": list(references),
            "mix": {"settings": None, "variant": None},
            "rev": 0,
        }
    )


def songs_dir(root: Path | None = None) -> Path:
    """The folder of all songs, checked to be inside the root."""
    return resolve_inside("private/songs", root, "songs")


def song_dir(name: str, root: Path | None = None) -> Path:
    """The folder of one song."""
    return resolve_inside(f"private/songs/{check_name(name)}", root, "name")


def _read_rev(path: Path) -> int:
    try:
        return loads(path.read_text(encoding="utf-8")).rev
    except FileNotFoundError:
        return 0


def save(song: Song, expected_rev: int, root: Path | None = None) -> Song:
    """Write the song as rev expected_rev + 1 if the file is still at expected_rev."""
    base = (root or repo_root()).resolve()
    folder = song_dir(song.name, base)
    make_private_dir(folder, base)
    parse(song.to_json())
    lock = os.open(folder / ".song.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / "song.json"
        current = _read_rev(path)
        if current != expected_rev:
            raise StaleRev(f"{song.name}: rev is {current}, not {expected_rev}")
        saved = replace(song, rev=expected_rev + 1)
        tmp = folder / f".song.json.{os.getpid()}.tmp"
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(json.dumps(saved.to_json(), indent=2, ensure_ascii=False) + "\n")
                out.flush()
                os.fsync(out.fileno())
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return saved
    finally:
        os.close(lock)


def load(name: str, root: Path | None = None) -> Song:
    """Read one song; SongError when it is missing or malformed."""
    path = song_dir(name, root) / "song.json"
    try:
        song = loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SongError(f"{name}: no such song") from None
    if song.name != name:
        raise SongError(f"{name}: the file holds the song {song.name!r}")
    return song


def list_songs(root: Path | None = None) -> list[str]:
    """Names of the songs that have a song.json, sorted."""
    folder = songs_dir(root)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if (p / "song.json").is_file())
