import ast
from pathlib import Path

import pytest

from redline.lyrics.fingerprint import FingerprintIndex
from redline.lyrics.parse import parse_text

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "private" / "lyrics" / "build"


def public_strings():
    for path in sorted((ROOT / "tests").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path, node.value
    for path in sorted((ROOT / "docs").glob("*")):
        if path.suffix in (".md", ".json"):
            yield path, path.read_text(encoding="utf-8")


@pytest.mark.skipif(not (BUILD / "fingerprints.npz").exists(), reason="no local lyrics index")
def test_no_public_text_copies_the_lyrics_base():
    index = FingerprintIndex.load(BUILD)
    copies = [
        (path.name, match.lines)
        for path, text in public_strings()
        # a leading newline keeps any front matter in the text as plain lines
        for match in index.check(parse_text("\n" + text))
    ]
    assert copies == []
