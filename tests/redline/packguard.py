"""The owner's scanned pack kept out of a test: import `no_owners_pack` into a test module."""

from pathlib import Path

import pytest
from redline.beat import pack


@pytest.fixture(autouse=True)
def no_owners_pack(tmp_path_factory, monkeypatch):
    """Keep the owner's scanned pack, if any, out of every test: a test that wants a pack
    makes its own."""
    monkeypatch.setattr(pack, "CATALOG", tmp_path_factory.mktemp("nopack") / "catalog.json")
    return Path(pack.CATALOG)
