import ast
import subprocess
import sys
from pathlib import Path

import pytest
import redline

PACKAGE = Path(redline.__file__).resolve().parent
# each module is entered first, with every redline module dropped before it, so a cycle
# shows up whichever of its modules is imported first
IMPORT_EACH = """
import importlib
import sys

for name in sys.argv[1:]:
    for loaded in [m for m in sys.modules if m == "redline" or m.startswith("redline.")]:
        del sys.modules[loaded]
    importlib.import_module(name)
"""


def module_name(path: Path) -> str:
    parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def modules(package: str = "") -> dict[str, Path]:
    root = PACKAGE / package
    return {module_name(path): path for path in sorted(root.rglob("*.py"))}


def imports(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Every redline import in a file, lazy and type-only ones included, with its names."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
    return [(module, names) for module, names in found if module.split(".")[0] == "redline"]


def targets(module: str, names: tuple[str, ...]) -> list[str]:
    """The modules an import reaches: `from redline.a import b` may name the module a.b."""
    return [module, *(f"{module}.{name}" for name in names)]


def under(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == p or module.startswith(f"{p}.") for p in prefixes)


def violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{name} imports {reached}"
        for name, path in modules(package).items()
        for module, names in imports(path)
        for reached in targets(module, names)
        if under(reached, forbidden)
    ]


def test_every_module_imports_first_in_a_fresh_interpreter():
    names = sorted(modules())
    assert "redline.engine.track" in names and len(names) > 50
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_EACH, *names], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_core_imports_nothing_above_it():
    above = tuple(
        f"redline.{p}"
        for p in ("vocal", "master", "engine", "studio", "cli", "lyrics", "measure", "beat", "song")
    )
    assert violations("core", above) == []


def test_the_engine_imports_no_app_module():
    apps = tuple(
        f"redline.{p}" for p in ("studio", "cli", "vocal.rounds", "master.run", "output", "beat")
    )
    assert violations("engine", apps) == []


@pytest.mark.parametrize(
    ("names", "home"),
    [
        ({"track_engine", "reference_target", "reference_loudness"}, "redline.engine.track"),
        ({"check_folders", "VocalRun", "ORIGINAL"}, "redline.output"),
    ],
)
def test_shared_helpers_are_imported_from_their_home(names, home):
    elsewhere = [
        f"{name} takes {', '.join(sorted(names & set(imported)))} from {module}"
        for name, path in modules().items()
        for module, imported in imports(path)
        if names & set(imported) and module != home
    ]
    assert elsewhere == []


def test_the_violation_check_sees_a_lazy_import(tmp_path, monkeypatch):
    package = tmp_path / "redline"
    (package / "core").mkdir(parents=True)
    (package / "core" / "bad.py").write_text(
        "def f():\n    from redline import cli\n", encoding="utf-8"
    )
    (package / "core" / "good.py").write_text(
        "from redline.core.audio import decode\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys.modules[__name__], "PACKAGE", package)
    assert violations("core", ("redline.cli",)) == ["redline.core.bad imports redline.cli"]
