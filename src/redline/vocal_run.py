"""Stage 2 run: process the track's vocal at every dirt level and write the blind listening set."""

import json
import logging
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from redline.chain import (
    DIRT_LEVELS,
    EXTRA,
    FIT_FROM_HZ,
    ChainResult,
    Target,
    check_vocal,
    curve_distance,
    process_vocal,
)
from redline.listen import write_listen_set
from redline.loudness import integrated_lufs
from redline.run import FileResult, keep_out_of_git, measure_inputs, new_run_dir
from redline.separate import Separator
from redline.spectrum import tonal_curve

Samples = npt.NDArray[np.floating]

ORIGINAL = "original"
AXES = ("dirt", "balance")
BALANCE_DB = (0, 3, 6, 9)
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VocalRun:
    """Where a stage 2 run put its files."""

    run_dir: Path
    listen_dir: Path


def reference_target(refs: list[FileResult]) -> Target:
    """Per-band median of the references' vocal tonal curves."""
    curves = [r.info["vocal.tonal_curve"] for r in refs]
    centers = tuple(curves[0]["centers_hz"])
    if any(tuple(c["centers_hz"]) != centers for c in curves):
        raise ValueError("the references' vocal curves use different bands")
    median = np.median(np.array([c["db"] for c in curves], dtype=float), axis=0)
    return Target(centers, tuple(float(v) for v in median))


def balance_variants(vocal: Samples, beat: Samples) -> dict[str, Samples]:
    """The processed vocal over the untouched beat, lowered by each BALANCE_DB step."""
    return {
        f"vocal {-offset}": vocal * np.float32(10 ** (-offset / 20)) + beat for offset in BALANCE_DB
    }


def vocal_run(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
    axis: str = "dirt",
) -> VocalRun:
    """Measure, process the vocal, write the listening set for one axis, the key and the chain.

    The dirt axis holds the balance and steps through the dirt levels; the balance axis holds
    the dirt at EXTRA and lowers the vocal against the beat.
    """
    if axis not in AXES:
        raise ValueError(f"unknown axis {axis!r}, expected one of {', '.join(AXES)}")
    inputs = measure_inputs(track, refs, work_dir, separator)
    stems, upper = inputs.track_stems, inputs.upper
    rate = stems.rate
    original = stems.vocal + stems.beat
    check_vocal(integrated_lufs(stems.vocal, rate), integrated_lufs(original, rate))
    target = reference_target(inputs.refs)
    variants = {ORIGINAL: original}
    chains: list[ChainResult] = []
    distances = {ORIGINAL: curve_distance(tonal_curve(stems.vocal, rate, upper), target)}
    for level in DIRT_LEVELS if axis == "dirt" else (EXTRA,):
        log.info("processing the vocal: %s", level.name)
        vocal, chain = process_vocal(stems.vocal, rate, level, target, upper)
        chains.append(chain)
        distances[level.name] = curve_distance(tonal_curve(vocal, rate, upper), target)
        if axis == "dirt":
            variants[level.name] = vocal + stems.beat
        else:
            variants.update(balance_variants(vocal, stems.beat))
    run_dir = new_run_dir(work_dir / "runs")
    listen_dir = out_dir / run_dir.name / "listen"
    keep_out_of_git(out_dir / run_dir.name)
    seed = secrets.randbits(32) if seed is None else seed
    key = write_listen_set(variants, rate, listen_dir, seed)
    (run_dir / "key.json").write_text(
        json.dumps(
            {"axis": axis, "seed": seed, "key": key, "listen_dir": str(listen_dir)}, indent=1
        ),
        encoding="utf-8",
    )
    (run_dir / "chain.json").write_text(
        json.dumps(
            {
                "rate": rate,
                "compared_up_to_hz": upper,
                "target": asdict(target),
                "axis": axis,
                "chains": [asdict(c) for c in chains],
                "levels": [asdict(level) for level in (*DIRT_LEVELS, EXTRA)],
                "balance_db": list(BALANCE_DB),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    _write_report(run_dir / "report.md", distances)
    return VocalRun(run_dir, listen_dir)


def _write_report(path: Path, distances: dict[str, float]) -> None:
    lines = [
        "# Вокал: варианты для прослушивания",
        "",
        "Расстояние тональной кривой вокала до медианы вокала референсов, дБ "
        f"(среднее по полосам от {FIT_FROM_HZ:.0f} Гц, меньше = ближе к референсам; "
        "это не «лучше»).",
        "",
        "| вариант | расстояние, дБ |",
        "|---|---|",
        *(f"| {name} | {value:.2f} |" for name, value in distances.items()),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
