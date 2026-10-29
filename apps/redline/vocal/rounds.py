"""Blind listening rounds: variants rendered by the engine, written under letters, the key kept.

Stage 2's vocal rounds (dirt, balance) live here; the stage 3 rounds use the same blind_round.
"""

import json
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from redline.core.listen import write_listen_set
from redline.engine.render import Engine, Rendered
from redline.engine.settings import Settings
from redline.engine.track import track_engine
from redline.measure.run import keep_out_of_git, measure_inputs, new_run_dir
from redline.measure.separate import Separator
from redline.output import ORIGINAL, VocalRun, check_folders
from redline.vocal.chain import ALL_LEVELS, DIRT_LEVELS, EXTRA, DirtLevel
from redline.vocal.report import write_vocal_report

Samples = npt.NDArray[np.floating]

AXES = ("dirt", "balance")
BALANCE_DB = (0, 3, 6, 9)
DEFAULT_LEVELS = tuple(level.name for level in DIRT_LEVELS)
# the processed vocal over the beat as it came: no timbre, no 808 drive, no master
PLAIN = replace(
    Settings(), octave_down_on=False, doubles_on=False, bass_drive_db=0.0, master_on=False
)
log = logging.getLogger(__name__)


def _check_levels(levels: tuple[str, ...]) -> None:
    if not levels:
        raise ValueError("no dirt level given")
    unknown = [name for name in levels if name not in ALL_LEVELS]
    if unknown:
        raise ValueError(
            f"unknown dirt level {', '.join(unknown)}, expected some of {', '.join(ALL_LEVELS)}"
        )


def level_settings(level: DirtLevel, vocal_db: float) -> Settings:
    """A dirt level as settings: its chain on the vocal, moved by vocal_db, over the plain beat.

    A level without a compressor is the EQ alone, the chain switched off.
    """
    comp = level.compressor
    if comp is None:
        return replace(PLAIN, chain_on=False, vocal_db=vocal_db)
    clip = level.clip_below_peak_db
    return replace(
        PLAIN,
        comp_threshold_db=comp.threshold_db,
        comp_ratio=comp.ratio,
        comp_attack_ms=comp.attack_ms,
        comp_release_ms=comp.release_ms,
        drive_db=level.drive_db,
        clip_on=clip is not None,
        clip_below_peak_db=PLAIN.clip_below_peak_db if clip is None else clip,
        vocal_db=vocal_db,
    )


def blind_round(
    engine: Engine,
    variants: Mapping[str, Settings | Samples],
    work_dir: Path,
    out_dir: Path,
    axis: str,
    seed: int | None = None,
    record: Callable[[dict[str, Rendered]], dict[str, Any]] = lambda rendered: {},
) -> tuple[VocalRun, dict[str, Rendered]]:
    """Render each variant and write the listening set, its key and the round's record.

    Args:
        engine: The track's engine.
        variants: Settings to render, or audio played as it is, such as the original.
        work_dir: Where runs/<run>/ gets key.json and chain.json.
        out_dir: Where <run>/listen/ gets one letter per variant, all at one loudness.
        axis: The round's axis, kept in the key.
        seed: The shuffle's seed; a random one when None.
        record: Builds chain.json from the renders; the settings of each are added to it.

    Returns:
        Where the files went, and the render of each settings variant.
    """
    renders: dict[str, Rendered] = {}
    mixes: dict[str, Samples] = {}
    for name, variant in variants.items():
        if isinstance(variant, Settings):
            log.info("%s: %s", axis, name)
            renders[name] = engine.render(variant)
            mixes[name] = renders[name].final
        else:
            mixes[name] = variant
    run_dir = new_run_dir(work_dir / "runs")
    out_run = new_run_dir(out_dir)
    keep_out_of_git(out_run)
    listen_dir = out_run / "listen"
    seed = secrets.randbits(32) if seed is None else seed
    key = write_listen_set(mixes, engine.context.rate, listen_dir, seed)
    (run_dir / "key.json").write_text(
        json.dumps(
            {"axis": axis, "seed": seed, "key": key, "listen_dir": str(listen_dir)}, indent=1
        ),
        encoding="utf-8",
    )
    settings = {n: asdict(v) for n, v in variants.items() if isinstance(v, Settings)}
    (run_dir / "chain.json").write_text(
        json.dumps({**record(renders), "variants": settings}, indent=1), encoding="utf-8"
    )
    return VocalRun(run_dir, listen_dir), renders


def vocal_run(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
    axis: str = "dirt",
    levels: tuple[str, ...] = DEFAULT_LEVELS,
    vocal_db: float = 0.0,
) -> VocalRun:
    """Measure, process the vocal, write the listening set for one axis, the key and the chain.

    The dirt axis holds the vocal at vocal_db against the beat and steps through the named
    levels; the balance axis holds the dirt at EXTRA and lowers the vocal against the beat.
    """
    if axis not in AXES:
        raise ValueError(f"unknown axis {axis!r}, expected one of {', '.join(AXES)}")
    _check_levels(levels)
    if axis == "balance" and (levels != DEFAULT_LEVELS or vocal_db != 0.0):
        raise ValueError("--levels and --vocal-db belong to the dirt axis")
    check_folders(work_dir, out_dir)
    inputs = measure_inputs(track, refs, work_dir, separator)
    engine = track_engine(inputs)
    ctx = engine.context
    if axis == "dirt":
        variants = {name: level_settings(ALL_LEVELS[name], vocal_db) for name in levels}
    else:
        variants = {f"vocal {-offset}": level_settings(EXTRA, -offset) for offset in BALANCE_DB}

    def record(renders: dict[str, Rendered]) -> dict[str, Any]:
        chains = list(renders.values()) if axis == "dirt" else [renders["vocal 0"]]
        return {
            "rate": ctx.rate,
            "compared_up_to_hz": ctx.upper,
            "target": asdict(ctx.target),
            "axis": axis,
            "chains": [asdict(r.chain) for r in chains],
            "levels": [asdict(level) for level in ALL_LEVELS.values()],
            "balance_db": list(BALANCE_DB),
            "vocal_db": vocal_db,
        }

    result, renders = blind_round(
        engine,
        {ORIGINAL: engine.vocal + engine.beat, **variants},
        work_dir,
        out_dir,
        axis,
        seed,
        record,
    )
    # the report reads each processed vocal at its own level, before the balance moved it
    vocals = {ORIGINAL: engine.vocal}
    if axis == "dirt":
        undo = np.float32(10 ** (-vocal_db / 20))
        vocals.update({name: renders[name].vocal * undo for name in levels})
    else:
        vocals[EXTRA.name] = renders["vocal 0"].vocal
    write_vocal_report(
        result.run_dir / "report.md",
        vocals,
        ctx.rate,
        ctx.upper,
        ctx.target,
        [r.values for r in inputs.refs],
        inputs.track.native_rate,
    )
    return result
