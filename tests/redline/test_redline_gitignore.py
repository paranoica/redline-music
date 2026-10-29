import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", "--no-index", path], check=False
    )
    return result.returncode == 0


# private folders as folders and as symlinks (a symlink is matched without the slash)
@pytest.mark.parametrize(
    "path",
    [
        "private",
        "private/docs/TZ.md",
        "input",
        "input/track.wav",
        "work",
        "output",
        "lyrics",
        "profiles",
        "docs/private",
        "models",
        "a/b/Song.MP3",
        "x/stems/vocal.wav",
        "chain.json",
        ".env",
        ".env.local",
        "weights.ckpt",
    ],
)
def test_private_material_is_ignored(path):
    assert ignored(path)


@pytest.mark.parametrize(
    "path",
    [
        "apps/redline/models/__init__.py",
        "apps/redline/stems/__init__.py",
        "apps/redline/lyrics/__init__.py",
        "apps/redline/profiles/__init__.py",
        ".env.example",
        "tests/redline/core/test_redline_audio.py",
        "apps/studio-web/index.html",
    ],
)
def test_code_is_not_ignored(path):
    assert not ignored(path)
