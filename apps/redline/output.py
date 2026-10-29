"""What the commands that write listening sets and results share: folders and names."""

from dataclasses import dataclass
from pathlib import Path

ORIGINAL = "original"


@dataclass(frozen=True)
class VocalRun:
    """Where a round put its files."""

    run_dir: Path
    listen_dir: Path


def check_folders(work_dir: Path, out_dir: Path) -> None:
    """Refuse an output folder inside the work folder or the reverse: the key lives in work."""
    work, out = work_dir.resolve(), out_dir.resolve()
    if work.is_relative_to(out) or out.is_relative_to(work):
        raise ValueError(
            f"keep --out and --work separate: the key in {work_dir} would sit with the files"
        )
