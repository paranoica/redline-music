"""The sample library: a pinned manifest of instruments, their fetch and their zones.

Fetched sources are CC0 repositories pinned at a commit; each file is listed with its git blob
SHA-1 and size, checked before anything is written. Local-only sources are placed by hand and
never downloaded or redistributed.
"""

import hashlib
import http.client
import json
import re
import tempfile
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

MANIFEST = Path(__file__).with_name("library.json")
SOUNDS = Path("work/sounds")
TIMEOUT_S = 60
CHUNK = 1 << 16
KINDS = ("fetch", "local_only")
COMMERCIAL_LICENCES = ("CC0-1.0", "own")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
SHA1 = re.compile(r"[0-9a-f]{40}")

Opener = Callable[[str, int], bytes]


class LibraryError(Exception):
    """A manifest, a fetch or a zone the library refuses."""


@dataclass(frozen=True)
class SampleFile:
    """One recorded sample of an instrument.

    Attributes:
        path: Its path in the source repository, and under the instrument's folder.
        git_sha1: The git blob SHA-1 of its bytes.
        size: Its size in bytes.
        note: The MIDI root note it was recorded at.
        vel: The velocity range it plays, both ends included.
    """

    path: str
    git_sha1: str
    size: int
    note: int
    vel: tuple[int, int]


@dataclass(frozen=True)
class Source:
    """One instrument of the manifest.

    Attributes:
        name: Our slug, also its folder under the sounds root.
        source: Where it comes from.
        commit: The pinned commit of a fetched source, empty for a local one.
        licence: The licence identifier.
        licence_quote: One line quoted from the licence.
        kind: ``fetch`` or ``local_only``.
        commercial: Whether a beat using it may be sold.
        files: The samples of a fetched source.
        note: Free text, e.g. how a local source is placed.
    """

    name: str
    source: str
    commit: str
    licence: str
    licence_quote: str
    kind: str
    commercial: bool
    files: tuple[SampleFile, ...]
    note: str = ""


@dataclass(frozen=True)
class Zone:
    """A sample ready for the sampler.

    Attributes:
        path: The verified file on disk.
        note: Its MIDI root note.
        vel: The velocity range it plays.
    """

    path: Path
    note: int
    vel: tuple[int, int]


@dataclass(frozen=True)
class Fetched:
    """What a fetch did.

    Attributes:
        downloaded: Paths downloaded now.
        present: Paths already on disk with the right hash.
    """

    downloaded: tuple[str, ...]
    present: tuple[str, ...]


def git_sha1(data: bytes) -> str:
    """The git blob SHA-1 of some bytes, as GitHub trees list it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def raw_url(source: str, commit: str, path: str) -> str:
    """The raw.githubusercontent.com address of a file at a commit."""
    match = GITHUB.fullmatch(source)
    if match is None:
        raise LibraryError(f"source {source!r} is not a GitHub repository address")
    owner, repo = match.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{quote(path)}"


def _field(raw: dict[str, Any], key: str, kind: type, where: str) -> Any:
    value = raw.get(key)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise LibraryError(f"{where}: {key} must be a {kind.__name__}, not {value!r}")
    return value


def _check_path(path: str, where: str) -> None:
    parts = PurePosixPath(path).parts
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\0" in path
        or ".." in parts
        or {".", ""} & set(path.split("/"))
    ):
        raise LibraryError(f"{where}: path {path!r} must stay inside the instrument folder")


def _sample(raw: Any, where: str) -> SampleFile:
    if not isinstance(raw, dict):
        raise LibraryError(f"{where}: a file entry must be an object")
    path = _field(raw, "path", str, where)
    where = f"{where} {path!r}"
    _check_path(path, where)
    sha = _field(raw, "git_sha1", str, where)
    if not SHA1.fullmatch(sha):
        raise LibraryError(f"{where}: git_sha1 must be 40 lower-case hex digits, not {sha!r}")
    size = _field(raw, "size", int, where)
    if size <= 0:
        raise LibraryError(f"{where}: size must be positive, not {size}")
    note = _field(raw, "note", int, where)
    if not 0 <= note <= 127:
        raise LibraryError(f"{where}: note must be 0..127, not {note}")
    vel = _field(raw, "vel", list, where)
    if (
        len(vel) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in vel)
        or not 0 <= vel[0] <= vel[1] <= 127
    ):
        raise LibraryError(f"{where}: vel must be [lo, hi] with 0 <= lo <= hi <= 127, not {vel}")
    return SampleFile(path, sha, size, note, (vel[0], vel[1]))


def _source(raw: Any) -> Source:
    if not isinstance(raw, dict):
        raise LibraryError("a source entry must be an object")
    name = _field(raw, "name", str, "source")
    if not NAME.fullmatch(name):
        raise LibraryError(f"source name {name!r} must be lower-case letters, digits and -")
    kind = _field(raw, "kind", str, name)
    if kind not in KINDS:
        raise LibraryError(f"{name}: kind must be one of {KINDS}, not {kind!r}")
    licence = _field(raw, "licence", str, name)
    commercial = _field(raw, "commercial", bool, name)
    if commercial and licence not in COMMERCIAL_LICENCES:
        raise LibraryError(f"{name}: commercial is only for {COMMERCIAL_LICENCES} sources")
    files = tuple(_sample(item, name) for item in _field(raw, "files", list, name))
    source = _field(raw, "source", str, name)
    commit = _field(raw, "commit", str, name) if "commit" in raw else ""
    note = _field(raw, "note", str, name) if "note" in raw else ""
    if kind == "fetch":
        raw_url(source, "", "")
        if not SHA1.fullmatch(commit):
            raise LibraryError(f"{name}: commit must be a full 40-digit hash, not {commit!r}")
        if not files:
            raise LibraryError(f"{name}: a fetched source lists its files")
        if len({f.path for f in files}) != len(files):
            raise LibraryError(f"{name}: a file is listed twice")
    elif files:
        raise LibraryError(f"{name}: a local_only source lists no files")
    return Source(
        name=name,
        source=source,
        commit=commit,
        licence=licence,
        licence_quote=_field(raw, "licence_quote", str, name),
        kind=kind,
        commercial=commercial,
        files=files,
        note=note,
    )


def load(manifest: Path | None = None) -> dict[str, Source]:
    """Load and validate the manifest.

    Args:
        manifest: The manifest file (default: the library.json next to this module).

    Returns:
        The sources by name.

    Raises:
        LibraryError: The manifest is unreadable or an entry is invalid.
    """
    path = MANIFEST if manifest is None else manifest
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LibraryError(f"cannot read the sound manifest {path}: {error}") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise LibraryError(f"{path}: the manifest is an object with a sources list")
    sources: dict[str, Source] = {}
    for item in raw["sources"]:
        source = _source(item)
        if source.name in sources:
            raise LibraryError(f"{path}: source {source.name!r} is listed twice")
        sources[source.name] = source
    return sources


PACK_PREFIX = "pack-"


def is_pack(name: str) -> bool:
    """Whether a library name is one sound of the owner's pack, ``pack-<id>``."""
    return name.startswith(PACK_PREFIX)


def _pack_source(name: str) -> Source:
    # the pack's licence is whatever the owner downloaded it under: never marked for sale
    return Source(
        name=name,
        source="the owner's pack in input/sounds",
        commit="",
        licence="as downloaded",
        licence_quote="",
        kind="pack",
        commercial=False,
        files=(),
    )


def source(name: str, manifest: Path | None = None) -> Source:
    """The manifest entry of one instrument, or the entry of a ``pack-<id>`` sound.

    Raises:
        LibraryError: No source has that name.
    """
    if is_pack(name):
        return _pack_source(name)
    sources = load(manifest)
    if name not in sources:
        raise LibraryError(f"no sound named {name!r}; known: {', '.join(sorted(sources))}")
    return sources[name]


def commercial(name: str, manifest: Path | None = None) -> bool:
    """Whether a beat using this instrument may be sold, per the manifest."""
    return source(name, manifest).commercial


def _target(folder: Path, item: SampleFile) -> Path:
    """The file's place on disk, refused when it resolves outside the instrument folder."""
    _check_path(item.path, folder.name)
    base = folder.resolve()
    target = folder / item.path
    if not target.resolve().is_relative_to(base):
        raise LibraryError(f"{item.path!r} resolves outside {folder}")
    return target


def _matches(path: Path, item: SampleFile) -> bool:
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    return git_sha1(path.read_bytes()) == item.git_sha1


def download(url: str, limit: int) -> bytes:
    """Read at most `limit` bytes from a URL, with a timeout."""
    chunks: list[bytes] = []
    got = 0
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
        while got < limit:
            chunk = response.read(min(CHUNK, limit - got))
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
    return b"".join(chunks)


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False
    ) as temp:
        part = Path(temp.name)
        try:
            temp.write(data)
        except OSError:
            part.unlink(missing_ok=True)
            raise
    try:
        part.replace(target)
    except OSError:
        part.unlink(missing_ok=True)
        raise


def fetch(
    name: str,
    root: Path = SOUNDS,
    opener: Opener | None = None,
    manifest: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Fetched:
    """Download a fetched instrument into ``root/<name>/``, checking every file first.

    Files already present with the right hash are kept. Each download is checked for size and
    git blob SHA-1 before it is written, then written through a temporary file and a rename.

    Args:
        name: The instrument's name in the manifest.
        root: The sounds folder.
        opener: Reads at most a number of bytes from a URL (default: urllib with a timeout).
        manifest: The manifest file (default: the committed one).
        progress: Told each path as it is downloaded.

    Returns:
        The paths downloaded now and those already present.

    Raises:
        LibraryError: The name is unknown or local only, a path leaves the folder, a download
            fails or does not match the manifest.
    """
    entry = source(name, manifest)
    if entry.kind != "fetch":
        raise LibraryError(
            f"{name} is local only: place it by hand into {root / name}/, it is never downloaded"
        )
    read = download if opener is None else opener
    folder = root / name
    targets = [(item, _target(folder, item)) for item in entry.files]
    downloaded: list[str] = []
    present: list[str] = []
    for item, target in targets:
        if _matches(target, item):
            present.append(item.path)
            continue
        if progress is not None:
            progress(item.path)
        url = raw_url(entry.source, entry.commit, item.path)
        try:
            data = read(url, item.size + 1)
        except (OSError, http.client.HTTPException) as error:
            raise LibraryError(f"{item.path}: download failed: {error!r}") from error
        if len(data) != item.size:
            raise LibraryError(f"{item.path}: got {len(data)} bytes, the manifest says {item.size}")
        if git_sha1(data) != item.git_sha1:
            raise LibraryError(f"{item.path}: the hash does not match the manifest")
        try:
            _write_atomic(target, data)
        except OSError as error:
            raise LibraryError(f"{item.path}: cannot write {target}: {error}") from error
        downloaded.append(item.path)
    return Fetched(tuple(downloaded), tuple(present))


def present(name: str, root: Path = SOUNDS, manifest: Path | None = None) -> int:
    """How many of a fetched instrument's files are on disk with the right hash."""
    entry = source(name, manifest)
    folder = root / name
    count = 0
    for item in entry.files:
        try:
            target = _target(folder, item)
        except LibraryError:
            continue
        count += _matches(target, item)
    return count


def _nearest(files: tuple[SampleFile, ...], notes: Iterable[int]) -> list[SampleFile]:
    """Per velocity layer, the files whose root is nearest to each note (the lower on a tie)."""
    layers: dict[tuple[int, int], list[SampleFile]] = {}
    for item in files:
        layers.setdefault(item.vel, []).append(item)
    chosen: dict[str, SampleFile] = {}
    for note in notes:
        if isinstance(note, bool) or not isinstance(note, int) or not 0 <= note <= 127:
            raise LibraryError(f"a used note must be a MIDI note 0..127, not {note!r}")
        for layer in layers.values():
            best = min(layer, key=lambda item: (abs(item.note - note), item.note))
            chosen[best.path] = best
    return [item for item in files if item.path in chosen]


def _pack_zone(name: str, root: Path, pack_root: Path | None) -> Zone:
    from redline.beat import pack

    try:
        path, entry = pack.file_of(
            name, pack.PACK if pack_root is None else pack_root, root / "pack" / "catalog.json"
        )
    except pack.PackError as error:
        raise LibraryError(str(error)) from error
    # an unpitched sound plays at middle C, so a drum's note means nothing to it
    return Zone(path, entry.note if entry.note is not None else 60, (0, 127))


def pack_names(catalog: Path | None = None) -> set[str]:
    """The ``pack-<id>`` names of every catalogued pack sound (default: `pack.CATALOG`)."""
    from redline.beat import pack

    try:
        return {f"{PACK_PREFIX}{i}" for i in pack.load(catalog or pack.CATALOG)}
    except pack.PackError:
        return set()


def zones(
    name: str,
    root: Path = SOUNDS,
    notes_used: Iterable[int] | None = None,
    manifest: Path | None = None,
    pack_root: Path | None = None,
) -> list[Zone]:
    """The verified zones of a fetched instrument, or the one zone of a pack sound.

    Args:
        name: The instrument's name in the manifest.
        root: The sounds folder.
        notes_used: The MIDI notes a score plays; only the nearest roots of each velocity
            layer are returned and read. None returns every zone.
        manifest: The manifest file (default: the committed one).
        pack_root: The owner's pack folder (default: `pack.PACK`), for a pack sound.

    Returns:
        The zones, in manifest order.

    Raises:
        LibraryError: The name is unknown or local only, or a returned file is missing or
            does not match its hash.
    """
    if is_pack(name):
        return [_pack_zone(name, root, pack_root)]
    entry = source(name, manifest)
    if entry.kind != "fetch":
        raise LibraryError(f"{name} is local only: its zones are placed by hand, not listed")
    files = list(entry.files) if notes_used is None else _nearest(entry.files, notes_used)
    folder = root / name
    found: list[Zone] = []
    for item in files:
        target = _target(folder, item)
        if not target.is_file():
            raise LibraryError(f"{item.path} is missing: run `redline beat sounds fetch {name}`")
        if not _matches(target, item):
            raise LibraryError(f"{item.path} does not match its hash: fetch {name} again")
        found.append(Zone(target, item.note, item.vel))
    return found
