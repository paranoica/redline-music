"""Stage 3 runs: the chosen vocal over the beat, clipped to the references' loudness."""

import json
import logging
import secrets
import statistics
from dataclasses import asdict
from pathlib import Path

import numpy as np

from redline.chain import ALL_LEVELS, check_vocal, process_vocal
from redline.listen import write_listen_set
from redline.loudness import integrated_lufs
from redline.master import master_clip
from redline.run import FileResult, keep_out_of_git, measure_inputs, new_run_dir
from redline.separate import Separator
from redline.vocal_run import ORIGINAL, VocalRun, check_folders, reference_target

VOCAL_LEVEL = "loudest"
VOCAL_DB = -6.0
QUIET_MASTER_LUFS = -11.0
NO_MASTER = "no master"
log = logging.getLogger(__name__)


def master_targets(refs: list[FileResult]) -> dict[str, float]:
    """Loudness targets for a round: a quiet master, the references' median, their loudest."""
    levels = [r.values.get("mix.lufs") for r in refs]
    if any(level is None for level in levels):
        raise ValueError("a reference has no measured loudness")
    known = [float(level) for level in levels if level is not None]
    targets = (QUIET_MASTER_LUFS, statistics.median(known), max(known))
    return {f"master {t:.1f}": round(t, 1) for t in targets}


def master_round(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
) -> VocalRun:
    """Blind round on the master: the original, the mix unmastered, and one file per target."""
    check_folders(work_dir, out_dir)
    inputs = measure_inputs(track, refs, work_dir, separator)
    stems, upper = inputs.track_stems, inputs.upper
    rate = stems.rate
    original = stems.vocal + stems.beat
    check_vocal(integrated_lufs(stems.vocal, rate), integrated_lufs(original, rate))
    vocal, chain = process_vocal(
        stems.vocal, rate, ALL_LEVELS[VOCAL_LEVEL], reference_target(inputs.refs), upper
    )
    mix = vocal * np.float32(10 ** (VOCAL_DB / 20)) + stems.beat
    variants = {ORIGINAL: original, NO_MASTER: mix}
    masters = []
    for name, target in master_targets(inputs.refs).items():
        log.info("mastering: %s", name)
        mastered, result = master_clip(mix, rate, target)
        variants[name] = mastered
        masters.append(
            {"name": name, **asdict(result), "final_lufs": integrated_lufs(mastered, rate)}
        )
    run_dir = new_run_dir(work_dir / "runs")
    out_run = new_run_dir(out_dir)
    keep_out_of_git(out_run)
    listen_dir = out_run / "listen"
    seed = secrets.randbits(32) if seed is None else seed
    key = write_listen_set(variants, rate, listen_dir, seed)
    (run_dir / "key.json").write_text(
        json.dumps(
            {"axis": "master", "seed": seed, "key": key, "listen_dir": str(listen_dir)}, indent=1
        ),
        encoding="utf-8",
    )
    (run_dir / "chain.json").write_text(
        json.dumps({"vocal": asdict(chain), "vocal_db": VOCAL_DB, "masters": masters}, indent=1),
        encoding="utf-8",
    )
    return VocalRun(run_dir, listen_dir)
