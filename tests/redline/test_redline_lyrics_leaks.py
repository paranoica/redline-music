import ast
import json
import subprocess
from pathlib import Path

import pytest

from redline.lyrics.fingerprint import RUN, FingerprintIndex
from redline.lyrics.normalize import words
from redline.lyrics.parse import parse_text
from redline.lyrics.syllables import HUNDREDS, TEENS, TENS, UNITS

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "private" / "lyrics" / "build"
TEXT_SUFFIXES = {".py", ".md", ".json", ".txt", ".toml", ".yaml", ".yml", ".cfg", ".ini"}
SKIP = {"uv.lock"}
# counting in a row is in the numeral tables of the code and in some songs
NUMBERS = set(words(" ".join(UNITS + TEENS + TENS + HUNDREDS)))
no_index = pytest.mark.skipif(
    not (BUILD / "fingerprints.npz").exists(), reason="no local lyrics index"
)


def public_texts():
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for name in tracked:
        path = ROOT / name
        if path.suffix not in TEXT_SUFFIXES or path.name in SKIP or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                yield name, text
                continue
            # the file's strings joined, so a text cut into short pieces is still whole
            strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)]
            text = "\n".join(v for v in strings if isinstance(v, str))
        yield name, text


@no_index
def test_no_tracked_text_holds_five_words_of_the_lyrics_base():
    index = FingerprintIndex.load(BUILD)
    copies = [
        (name, match.lines)
        for name, text in public_texts()
        # a leading newline keeps any front matter in the text as plain lines
        for match in index.check(parse_text("\n" + text), min_words=RUN)
        if not set(words(" ".join(match.lines))) <= NUMBERS
    ]
    assert copies == []


def strings_of(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_of(item)
    elif isinstance(value, str):
        yield value


@no_index
def test_real_profiles_hold_single_words_only():
    for path in sorted((BUILD / "profiles").glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert not [s for s in strings_of(profile) if " " in s], path.name
