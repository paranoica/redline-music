"""The copy check: keyed digests of 5-word runs of the base, chained into longer copies."""

import hashlib
import json
import secrets
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from redline.lyrics.text.normalize import words
from redline.lyrics.text.parse import LyricsError, Track

MAGIC = "redline-lyrics-index"
VERSION = 3
RUN = 5
MIN_DISTINCT = 3
DEFAULT_MIN_WORDS = 8
KEY_BYTES = 32
INDEX_FILE = "fingerprints.npz"
KEY_FILE = "key"
CANARY = b"redline canary"
# a place is the run's position in its text's word stream; the stream with adlibs dropped
# is told apart by the top bit
DROPPED = 1 << 31


@dataclass(frozen=True)
class Match:
    """A copied stretch of the checked text: its length in words and its lines."""

    words: int
    first_line: int
    last_line: int
    lines: tuple[str, ...]


def new_key() -> bytes:
    """A fresh random key for the digests."""
    return secrets.token_bytes(KEY_BYTES)


def _digest(key: bytes, data: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(data, digest_size=8, key=key).digest(), "little")


def _streams(track: Track) -> list[list[tuple[str, int]]]:
    """The text's words with their line numbers, adlibs kept as words and adlibs dropped.

    Headers are not words, so a run goes on across line and section breaks.
    """
    kept: list[tuple[str, int]] = []
    dropped: list[tuple[str, int]] = []
    number = 0
    for section in track.sections:
        for line in section.lines:
            number += 1
            kept += [(w, number) for w in words(line.text)]
            dropped += [(w, number) for w in words(line.plain)]
    return [kept, dropped]


def _runs(key: bytes, stream: list[tuple[str, int]]) -> list[int | None]:
    """The digest of the run starting at each word; None where the run is an interjection chain."""
    out: list[int | None] = []
    for i in range(len(stream) - RUN + 1):
        run = [w for w, _ in stream[i : i + RUN]]
        out.append(_digest(key, " ".join(run).encode()) if len(set(run)) >= MIN_DISTINCT else None)
    return out


def _write(path: Path, data: bytes) -> None:
    # written aside and moved in, so a crash never leaves half a file
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
    tmp.chmod(0o600)
    tmp.replace(path)


class FingerprintIndex:
    """Run digests of the base with the text and the place each came from."""

    def __init__(
        self,
        key: bytes,
        digests: npt.NDArray[np.uint64],
        texts: npt.NDArray[np.uint32],
        places: npt.NDArray[np.uint32],
        artists: list[str],
    ) -> None:
        """Hold digests sorted with their texts and places, and the artist of each text."""
        if len(key) != KEY_BYTES:
            # a short key, empty above all, leaves the digests open to a word-list attack
            raise LyricsError(f"the lyrics index key must be {KEY_BYTES} bytes, got {len(key)}")
        if not artists:
            raise LyricsError("the lyrics index holds no texts")
        self.key = key
        self.digests = digests
        self.texts = texts
        self.places = places
        self.artists = artists

    @property
    def n_texts(self) -> int:
        """How many texts the index was built from."""
        return len(self.artists)

    @classmethod
    def build(cls, tracks: Iterable[tuple[str, Track]], key: bytes) -> "FingerprintIndex":
        """Index (artist, track) pairs; the artist is kept per text for later counts."""
        rows: set[tuple[int, int, int]] = set()
        artists: list[str] = []
        for text_id, (artist, track) in enumerate(tracks):
            artists.append(artist)
            for variant, stream in zip((0, DROPPED), _streams(track), strict=True):
                for place, digest in enumerate(_runs(key, stream)):
                    if digest is not None:
                        rows.add((digest, text_id, variant | place))
        ordered = sorted(rows)
        return cls(
            key,
            np.array([d for d, _, _ in ordered], dtype=np.uint64),
            np.array([t for _, t, _ in ordered], dtype=np.uint32),
            np.array([p for _, _, p in ordered], dtype=np.uint32),
            artists,
        )

    def _places_of(self, digest: int, ignore: frozenset[int]) -> set[tuple[int, int]]:
        value = np.uint64(digest)
        lo = int(np.searchsorted(self.digests, value, side="left"))
        hi = int(np.searchsorted(self.digests, value, side="right"))
        found = zip(self.texts[lo:hi].tolist(), self.places[lo:hi].tolist(), strict=True)
        return {(t, p) for t, p in found if t not in ignore}

    def _spans(
        self, stream: list[tuple[str, int]], min_words: int, ignore: frozenset[int]
    ) -> list[tuple[int, int]]:
        # a stretch grows while each next run sits right after the previous one in the same
        # base text; an interjection run proves nothing and only carries the stretch on
        active: dict[tuple[int, int], tuple[int, int]] = {}
        spans = []

        def close(start: int, last: int) -> None:
            if last + RUN - start >= min_words:
                spans.append((start, last + RUN - 1))

        for i, digest in enumerate(_runs(self.key, stream)):
            if digest is None:
                active = {(t, p + 1): v for (t, p), v in active.items()}
                continue
            hits = self._places_of(digest, ignore)
            grown: dict[tuple[int, int], tuple[int, int]] = {}
            for (text_id, place), (start, last) in active.items():
                if (text_id, place) in hits:
                    grown[(text_id, place + 1)] = (start, i)
                else:
                    close(start, last)
            for text_id, place in hits:
                grown.setdefault((text_id, place + 1), (i, i))
            active = grown
        for start, last in active.values():
            close(start, last)
        return spans

    def check(
        self, track: Track, min_words: int = DEFAULT_MIN_WORDS, ignore: Iterable[str] = ()
    ) -> list[Match]:
        """Stretches of min_words or more words in a row that one text of the base also has.

        Pieces of different base texts, or of different places in one, never add up. Texts
        of the artists in `ignore` are left out.
        """
        if min_words < RUN:
            raise ValueError(f"a copy is at least {RUN} words long, got {min_words}")
        skip = set(ignore)
        left_out = frozenset(i for i, artist in enumerate(self.artists) if artist in skip)
        found = []
        for stream in _streams(track):
            for a, b in self._spans(stream, min_words, left_out):
                found.append((stream[a][1], stream[b][1], b - a + 1))
        lines = [line.text for section in track.sections for line in section.lines]
        return _matches(found, lines)

    def save(self, folder: Path, force: bool = False) -> None:
        """Write the index and its key; a smaller or unreadable index is replaced only by force."""
        if not force and (folder / INDEX_FILE).exists():
            try:
                existing = FingerprintIndex.load(folder).n_texts
            except LyricsError as error:
                raise LyricsError(f"{error}; force a rebuild to replace it") from error
            if existing > self.n_texts:
                raise LyricsError(
                    f"the index on disk holds {existing} texts, this one {self.n_texts}; "
                    "rebuild from all texts or force it"
                )
        folder.mkdir(parents=True, exist_ok=True)
        header = {
            "magic": MAGIC,
            "version": VERSION,
            "run": RUN,
            "min_distinct": MIN_DISTINCT,
            "texts": self.n_texts,
            "canary": _digest(self.key, CANARY),
        }
        tmp = folder / (INDEX_FILE + ".tmp")
        with tmp.open("wb") as f:
            np.savez(
                f,
                header=np.frombuffer(json.dumps(header).encode(), dtype=np.uint8),
                artists=np.frombuffer(json.dumps(self.artists).encode(), dtype=np.uint8),
                digests=self.digests,
                texts=self.texts,
                places=self.places,
            )
        tmp.chmod(0o600)
        _write(folder / KEY_FILE, self.key)
        tmp.replace(folder / INDEX_FILE)

    @classmethod
    def load(cls, folder: Path) -> "FingerprintIndex":
        """Read an index and refuse one that is missing, broken, of another version or key."""
        try:
            key = (folder / KEY_FILE).read_bytes()
            with np.load(folder / INDEX_FILE, allow_pickle=False) as data:
                header = json.loads(data["header"].tobytes())
                artists = json.loads(data["artists"].tobytes())
                digests = data["digests"].astype(np.uint64)
                texts = data["texts"].astype(np.uint32)
                places = data["places"].astype(np.uint32)
        except (OSError, KeyError, ValueError, EOFError, zipfile.BadZipFile) as error:
            raise LyricsError(f"no usable lyrics index in {folder}: {error}") from error
        if header.get("magic") != MAGIC or header.get("version") != VERSION:
            raise LyricsError(f"the lyrics index in {folder} is of another version; rebuild it")
        if header.get("run") != RUN or header.get("min_distinct") != MIN_DISTINCT:
            raise LyricsError(
                f"the lyrics index in {folder} was built with other rules; rebuild it"
            )
        if header.get("canary") != _digest(key, CANARY):
            raise LyricsError(f"the key in {folder} is not the one the lyrics index was built with")
        sizes = {len(digests), len(texts), len(places)}
        unsorted = len(digests) > 1 and bool(np.any(digests[1:] < digests[:-1]))
        if header.get("texts") != len(artists) or len(sizes) != 1 or not len(digests) or unsorted:
            raise LyricsError(f"the lyrics index in {folder} is damaged; rebuild it")
        return cls(key, digests, texts, places, artists)


def _matches(found: list[tuple[int, int, int]], lines: list[str]) -> list[Match]:
    # both word streams can find the same copy: overlapping line ranges are one match
    merged: list[list[int]] = []
    for first, last, count in sorted(found):
        if merged and first <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], last)
            merged[-1][2] = max(merged[-1][2], count)
        else:
            merged.append([first, last, count])
    return [Match(c, f, la, tuple(lines[f - 1 : la])) for f, la, c in merged]
