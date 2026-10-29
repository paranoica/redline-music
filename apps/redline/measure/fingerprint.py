"""Fingerprints of code and library versions, so caches never serve numbers from old code."""

import hashlib
from collections.abc import Iterable, Mapping
from importlib import metadata
from pathlib import Path


def fingerprint(sources: Iterable[bytes], versions: Mapping[str, str]) -> str:
    """Hash of source texts and library versions."""
    digest = hashlib.sha256()
    for source in sources:
        digest.update(hashlib.sha256(source).digest())
    for name in sorted(versions):
        digest.update(f"{name}={versions[name]}\n".encode())
    return digest.hexdigest()


def library_versions(names: Iterable[str]) -> dict[str, str]:
    """Installed versions; "missing" for a library that is not installed."""
    found = {}
    for name in names:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = "missing"
    return found


def module_sources(names: Iterable[str]) -> list[bytes]:
    """Source bytes of redline modules by dotted name under the package, e.g. "core.audio"."""
    root = Path(__file__).parents[1]
    return [(root / f"{name.replace('.', '/')}.py").read_bytes() for name in names]
