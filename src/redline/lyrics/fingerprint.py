"""The copy check: keyed digests of 5-word runs of the base, chained into longer copies."""

import hashlib
import json
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from redline.lyrics.normalize import words
from redline.lyrics.parse import LyricsError, Track

MAGIC = "redline-lyrics-index"
VERSION = 1
RUN = 5
MIN_DISTINCT = 3
DEFAULT_MIN_WORDS = 8
KEY_BYTES = 32
INDEX_FILE = "fingerprints.npz"
KEY_FILE = "key"
CANARY = b"redline canary"


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
    return [kept, dropped] if kept != dropped else [kept]


def _runs(key: bytes, stream: list[tuple[str, int]]) -> list[int | None]:
    """The digest of the run starting at each word; None where the run is an interjection chain."""
    out: list[int | None] = []
    for i in range(len(stream) - RUN + 1):
        run = [w for w, _ in stream[i : i + RUN]]
        out.append(_digest(key, " ".join(run).encode()) if len(set(run)) >= MIN_DISTINCT else None)
    return out


class FingerprintIndex:
    """Run digests of the base with the text each came from."""

    def __init__(
        self,
        key: bytes,
        digests: npt.NDArray[np.uint64],
        texts: npt.NDArray[np.uint32],
        artists: list[str],
    ) -> None:
        """Hold sorted digests, the text of each and the artist of each text."""
        if not artists:
            raise LyricsError("the lyrics index holds no texts")
        self.key = key
        self.digests = digests
        self.texts = texts
        self.artists = artists

    @property
    def n_texts(self) -> int:
        """How many texts the index was built from."""
        return len(self.artists)

    @classmethod
    def build(cls, tracks: Iterable[tuple[str, Track]], key: bytes) -> "FingerprintIndex":
        """Index (artist, track) pairs; the artist is kept per text for later counts."""
        pairs: set[tuple[int, int]] = set()
        artists: list[str] = []
        for text_id, (artist, track) in enumerate(tracks):
            artists.append(artist)
            for stream in _streams(track):
                pairs.update((d, text_id) for d in _runs(key, stream) if d is not None)
        ordered = sorted(pairs)
        digests = np.array([d for d, _ in ordered], dtype=np.uint64)
        texts = np.array([t for _, t in ordered], dtype=np.uint32)
        return cls(key, digests, texts, artists)

    def _texts_of(self, digest: int) -> set[int]:
        value = np.uint64(digest)
        lo = int(np.searchsorted(self.digests, value, side="left"))
        hi = int(np.searchsorted(self.digests, value, side="right"))
        return set(self.texts[lo:hi].tolist())

    def _spans(self, stream: list[tuple[str, int]], min_words: int) -> list[tuple[int, int]]:
        # consecutive runs found in the same base text join into one stretch
        hits = [self._texts_of(d) if d is not None else set() for d in _runs(self.key, stream)]
        active: dict[int, int] = {}
        spans = []
        for i, found in enumerate([*hits, set()]):
            for text_id in [t for t in active if t not in found]:
                start = active.pop(text_id)
                end = i + RUN - 2
                if end - start + 1 >= min_words:
                    spans.append((start, end))
            for text_id in found:
                active.setdefault(text_id, i)
        return spans

    def check(self, track: Track, min_words: int = DEFAULT_MIN_WORDS) -> list[Match]:
        """Stretches of min_words or more words in a row that one text of the base also has.

        Runs of different base texts never add up, so pieces common to several texts
        stay short.
        """
        if min_words < RUN:
            raise ValueError(f"a copy is at least {RUN} words long, got {min_words}")
        found = []
        for stream in _streams(track):
            for a, b in self._spans(stream, min_words):
                found.append((stream[a][1], stream[b][1], b - a + 1))
        lines = [line.text for section in track.sections for line in section.lines]
        return _matches(found, lines)

    def save(self, folder: Path, force: bool = False) -> None:
        """Write the index and its key; a smaller index replaces a bigger one only when forced."""
        if not force and (folder / INDEX_FILE).exists():
            try:
                existing = FingerprintIndex.load(folder).n_texts
            except LyricsError:
                existing = 0
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
        (folder / KEY_FILE).write_bytes(self.key)
        np.savez(
            folder / INDEX_FILE,
            header=np.frombuffer(json.dumps(header).encode(), dtype=np.uint8),
            artists=np.frombuffer(json.dumps(self.artists).encode(), dtype=np.uint8),
            digests=self.digests,
            texts=self.texts,
        )

    @classmethod
    def load(cls, folder: Path) -> "FingerprintIndex":
        """Read an index and refuse one that is missing, empty, of another version or key."""
        try:
            key = (folder / KEY_FILE).read_bytes()
            with np.load(folder / INDEX_FILE, allow_pickle=False) as data:
                header = json.loads(data["header"].tobytes())
                artists = json.loads(data["artists"].tobytes())
                digests = data["digests"].astype(np.uint64)
                texts = data["texts"].astype(np.uint32)
        except (OSError, KeyError, ValueError) as error:
            raise LyricsError(f"no usable lyrics index in {folder}: {error}") from error
        if header.get("magic") != MAGIC or header.get("version") != VERSION:
            raise LyricsError(f"the lyrics index in {folder} is of another version; rebuild it")
        if header.get("run") != RUN or header.get("min_distinct") != MIN_DISTINCT:
            raise LyricsError(
                f"the lyrics index in {folder} was built with other rules; rebuild it"
            )
        if header.get("canary") != _digest(key, CANARY):
            raise LyricsError(f"the key in {folder} is not the one the lyrics index was built with")
        return cls(key, digests, texts, artists)


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
