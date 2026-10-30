"""Build library.json from the git trees of the sample repositories.

Run with the trees listed by
``gh api 'repos/<owner>/<repo>/git/trees/<commit>?recursive=1'``::

    python -m redline.beat.library_build VSCO-2-CE=vsco.json VCSL=vcsl.json > library.json
"""

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from redline.beat.library import LibraryError

MAX_BYTES = 40_000_000
MAX_LAYERS = 2
NOTE = re.compile(r"(?<![A-Za-z0-9#])([A-G])([#b]?)(-?\d)(?![A-Za-z0-9#])")
PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass(frozen=True)
class Repo:
    """A pinned sample repository.

    Attributes:
        url: The GitHub page of the repository.
        commit: The commit every file is fetched from.
        licence_quote: One line quoted from the LICENSE file at that commit.
    """

    url: str
    commit: str
    licence_quote: str


@dataclass(frozen=True)
class Pick:
    """Which files of one folder make an instrument.

    Attributes:
        name: Our slug of the instrument.
        repo: The key of its repository in REPOS.
        folder: The folder holding the files; subfolders are not taken.
        layers: Velocity layers, soft to loud, each a set of name tokens a file must carry;
            an empty set takes every file.
        low: The lowest root note kept.
        high: The highest root note kept.
    """

    name: str
    repo: str
    folder: str
    layers: tuple[tuple[str, ...], ...]
    low: int = 0
    high: int = 127

    def __post_init__(self) -> None:
        """Refuse more velocity layers than the budget allows."""
        if not 1 <= len(self.layers) <= MAX_LAYERS:
            raise LibraryError(f"{self.name}: 1 to {MAX_LAYERS} velocity layers, not {self.layers}")


REPOS = {
    "VSCO-2-CE": Repo(
        "https://github.com/sgossner/VSCO-2-CE",
        "440300901dfe9275fd84e0b7763af1f8443ae62e",
        "purposes, including without limitation commercial purposes. These owners may",
    ),
    "VCSL": Repo(
        "https://github.com/sgossner/VCSL",
        "c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e",
        "and for any purposes, including without limitation commercial purposes.",
    ),
}

# each kept under MAX_BYTES: one round robin, at most two layers, sub-bass octaves cut where needed
PICKS = (
    Pick(
        "vcsl-glock",
        "VCSL",
        "Idiophones/Struck Idiophones/Glockenspiel",
        (("medium",), ("loud",)),
    ),
    Pick("vsco-harp", "VSCO-2-CE", "Strings/Harp", ((),)),
    Pick("vsco-violin-pizz", "VSCO-2-CE", "Strings/Violin Section/Pizz", (("v1",), ("v2",))),
    Pick("vsco-cello-pizz", "VSCO-2-CE", "Strings/Cello Section/pizzT", (("v1",), ("v2",))),
    Pick("vcsl-kalimba", "VCSL", "Idiophones/Plucked Idiophones/Kalimba, Tanzania", ((),)),
    Pick("vcsl-fm-piano", "VCSL", "Electrophones/TX81Z/FM Piano", (("vl1",), ("vl3",)), low=24),
    Pick(
        "vcsl-tubular-bells",
        "VCSL",
        "Idiophones/Struck Idiophones/Tubular Bells 1",
        (("f", "ff", "fff"),),
    ),
    Pick(
        "vcsl-vibes-hard",
        "VCSL",
        "Idiophones/Struck Idiophones/Vibraphone/Hard Mallets",
        (("v3",),),
    ),
    Pick("vcsl-marimba", "VCSL", "Idiophones/Struck Idiophones/Marimba", (("med",), ("loud",))),
    Pick("vsco-upright-piano", "VSCO-2-CE", "Keys/Upright Nr1", (("mf",),)),
)

LOCAL_ONLY = (
    {
        "name": "philharmonia",
        "source": "https://philharmonia.co.uk/resources/sound-samples/",
        "licence": "Philharmonia sound samples terms",
        "licence_quote": "must not be sold or made available 'as is'",
        "kind": "local_only",
        "commercial": False,
        "note": "placed by hand into work/sounds/philharmonia/, never redistributed",
        "files": [],
    },
    {
        "name": "iowa-mis",
        "source": "https://theremin.music.uiowa.edu/MIS.html",
        "licence": "University of Iowa MIS terms",
        "licence_quote": "may be downloaded and used for any projects, without restrictions.",
        "kind": "local_only",
        "commercial": False,
        "note": "placed by hand into work/sounds/iowa-mis/, never redistributed",
        "files": [],
    },
)


def parse_note(path: str) -> int:
    """Read the MIDI root note from a sample's file name, C-1 being 0 and C4 60.

    Args:
        path: The file's path; only its name is read.

    Returns:
        The MIDI note, 0 to 127.

    Raises:
        LibraryError: The name holds no note, more than one, or one outside MIDI.
    """
    found = NOTE.findall(PurePosixPath(path).stem)
    if len(found) != 1:
        raise LibraryError(f"no single note in the name {path!r}: {found or 'none'}")
    letter, accidental, octave = found[0]
    shift = {"#": 1, "b": -1, "": 0}[accidental]
    note = (int(octave) + 1) * 12 + PITCH_CLASS[letter] + shift
    if not 0 <= note <= 127:
        raise LibraryError(f"the note in {path!r} is outside MIDI 0..127")
    return note


def velocity_ranges(count: int) -> list[list[int]]:
    """Split 0..127 evenly into `count` velocity ranges, soft first."""
    edges = [round(i * 128 / count) for i in range(count + 1)]
    return [[edges[i], edges[i + 1] - 1] for i in range(count)]


def _layer(path: str, pick: Pick) -> int | None:
    tokens = set(re.split(r"[_ ]", PurePosixPath(path).stem.lower()))
    matched = [i for i, layer in enumerate(pick.layers) if not layer or tokens & set(layer)]
    if len(matched) > 1:
        raise LibraryError(f"{path!r} falls in more than one layer of {pick.name}")
    return matched[0] if matched else None


def pick_files(tree: dict[str, Any], pick: Pick) -> list[dict[str, Any]]:
    """Choose an instrument's files from a repository tree.

    One file per root note and layer, the first by name, which is the first round robin.

    Args:
        tree: The tree JSON listed by the GitHub API with ``recursive=1``.
        pick: Which folder, layers and notes to take.

    Returns:
        Manifest file entries, soft layer first, low note first.

    Raises:
        LibraryError: The tree is truncated, a layer has no file, or the files are over budget.
    """
    if tree.get("truncated"):
        raise LibraryError("the tree listing is truncated: list the folder on its own")
    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    blobs = (e for e in tree["tree"] if e["type"] == "blob")
    for item in sorted(blobs, key=lambda e: e["path"]):
        path = PurePosixPath(item["path"])
        if str(path.parent) != pick.folder or path.suffix.lower() != ".wav":
            continue
        layer = _layer(item["path"], pick)
        if layer is None:
            continue
        note = parse_note(item["path"])
        if not pick.low <= note <= pick.high:
            continue
        chosen.setdefault((layer, note), item)
    vel = velocity_ranges(len(pick.layers))
    for layer in range(len(pick.layers)):
        if not any(key[0] == layer for key in chosen):
            raise LibraryError(f"{pick.name}: no file in layer {pick.layers[layer]}")
    files = [
        {
            "path": item["path"],
            "git_sha1": item["sha"],
            "size": item["size"],
            "note": note,
            "vel": vel[layer],
        }
        for (layer, note), item in sorted(chosen.items())
    ]
    total = sum(f["size"] for f in files)
    if total > MAX_BYTES:
        raise LibraryError(f"{pick.name}: {total} bytes, over the budget of {MAX_BYTES}")
    return files


def build(trees: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build the whole manifest from the tree of each repository in REPOS."""
    sources: list[dict[str, Any]] = []
    for pick in PICKS:
        repo = REPOS[pick.repo]
        sources.append(
            {
                "name": pick.name,
                "source": repo.url,
                "commit": repo.commit,
                "licence": "CC0-1.0",
                "licence_quote": repo.licence_quote,
                "kind": "fetch",
                "commercial": True,
                "files": pick_files(trees[pick.repo], pick),
            }
        )
    sources.extend(LOCAL_ONLY)
    return {"sources": sources}


def main(argv: Sequence[str] | None = None) -> int:
    """Print the manifest built from `REPO=tree.json` arguments."""
    trees = {}
    for argument in sys.argv[1:] if argv is None else argv:
        repo, _, file = argument.partition("=")
        trees[repo] = json.loads(Path(file).read_text(encoding="utf-8"))
    missing = sorted(set(REPOS) - set(trees))
    if missing:
        print(f"trees missing for: {', '.join(missing)}", file=sys.stderr)
        return 2
    print(json.dumps(build(trees), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
