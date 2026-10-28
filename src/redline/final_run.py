"""Stage 3 runs: the chosen vocal over the beat, clipped to the references' loudness."""

import json
import logging
import secrets
import statistics
from dataclasses import asdict
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.audio import true_peak_db
from redline.chain import ALL_LEVELS, ChainResult, check_vocal, process_vocal
from redline.final_report import write_final_report
from redline.listen import common_lufs, write_listen_set
from redline.loudness import gated_crest_db, integrated_lufs
from redline.master import master_clip
from redline.run import FileResult, Inputs, keep_out_of_git, measure_inputs, new_run_dir
from redline.separate import Separator
from redline.timbre import bitcrush, double, octave_layer
from redline.vocal_run import ORIGINAL, VocalRun, check_folders, reference_target

Samples = npt.NDArray[np.floating]

VOCAL_LEVEL = "loudest"
VOCAL_DB = -6.0
QUIET_MASTER_LUFS = -11.0
NO_MASTER = "no master"
BASE = "base"
EFFECTS = ("doubles", "octave down", "octave up", "bitcrush")
TIMBRES = ("doubles", "octave down", "octave up", "bitcrush")
TIMBRE = "octave down"
OCTAVE_LEVEL_DB = -6.0
CRUSH_BITS = 8
CRUSH_HOLD = 3
log = logging.getLogger(__name__)


def _reference_loudness(refs: list[FileResult]) -> list[float]:
    levels = [r.values.get("mix.lufs") for r in refs]
    if any(level is None for level in levels):
        raise ValueError("a reference has no measured loudness")
    return [float(level) for level in levels if level is not None]


def master_targets(refs: list[FileResult]) -> dict[str, float]:
    """Loudness targets for a round: a quiet master, the references' median, their loudest."""
    known = _reference_loudness(refs)
    targets = sorted(
        {round(t, 1) for t in (QUIET_MASTER_LUFS, statistics.median(known), max(known))}
    )
    return {f"master {t:.1f}": t for t in targets}


def _chosen_mix(
    track: Path, refs: list[Path], work_dir: Path, separator: Separator
) -> tuple[Inputs, Samples, Samples, ChainResult]:
    """The original and the chosen vocal (processed, lowered) of a track; beat untouched."""
    inputs = measure_inputs(track, refs, work_dir, separator)
    stems = inputs.track_stems
    rate = stems.rate
    original = stems.vocal + stems.beat
    check_vocal(integrated_lufs(stems.vocal, rate), integrated_lufs(original, rate))
    vocal, chain = process_vocal(
        stems.vocal, rate, ALL_LEVELS[VOCAL_LEVEL], reference_target(inputs.refs), inputs.upper
    )
    return inputs, original, vocal * np.float32(10 ** (VOCAL_DB / 20)), chain


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
    inputs, original, vocal, chain = _chosen_mix(track, refs, work_dir, separator)
    rate = inputs.track_stems.rate
    mix = vocal + inputs.track_stems.beat
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


def _effects(name: str) -> list[str]:
    return [part.strip() for part in name.split("+")]


def _with_timbre(name: str, vocal: Samples, rate: int) -> Samples:
    """Apply one effect, or several joined by " + ", in order."""
    out = vocal
    for effect in _effects(name):
        if effect == "doubles":
            out = double(out, rate)
        elif effect == "octave down":
            out = octave_layer(out, rate, -1, OCTAVE_LEVEL_DB)
        elif effect == "octave up":
            out = octave_layer(out, rate, 1, OCTAVE_LEVEL_DB)
        elif effect == "bitcrush":
            # the crush works on the vocal's own scale, not on its level under the beat
            peak = float(np.abs(out).max())
            out = bitcrush(out / peak, rate, CRUSH_BITS, CRUSH_HOLD) * np.float32(peak)
        else:
            raise ValueError(f"unknown timbre {effect!r}, expected some of {', '.join(EFFECTS)}")
    return out


def timbre_round(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
    timbres: tuple[str, ...] = TIMBRES,
) -> VocalRun:
    """Blind round on the vocal's timbre: the original, the chosen sound, one file per effect."""
    unknown = [e for name in timbres for e in _effects(name) if e not in EFFECTS]
    if unknown:
        raise ValueError(
            f"unknown timbre {', '.join(unknown)}, expected some of {', '.join(EFFECTS)}"
        )
    check_folders(work_dir, out_dir)
    inputs, original, vocal, _ = _chosen_mix(track, refs, work_dir, separator)
    beat, rate = inputs.track_stems.beat, inputs.track_stems.rate
    target = round(statistics.median(_reference_loudness(inputs.refs)), 1)
    variants = {ORIGINAL: original, BASE: master_clip(vocal + beat, rate, target)[0]}
    for name in timbres:
        log.info("timbre: %s", name)
        variants[name] = master_clip(_with_timbre(name, vocal, rate) + beat, rate, target)[0]
    run_dir = new_run_dir(work_dir / "runs")
    out_run = new_run_dir(out_dir)
    keep_out_of_git(out_run)
    listen_dir = out_run / "listen"
    seed = secrets.randbits(32) if seed is None else seed
    key = write_listen_set(variants, rate, listen_dir, seed)
    (run_dir / "key.json").write_text(
        json.dumps({"axis": "timbre", "seed": seed, "key": key, "listen_dir": str(listen_dir)}),
        encoding="utf-8",
    )
    return VocalRun(run_dir, listen_dir)


def run_track(
    track: Path, refs: list[Path], work_dir: Path, out_dir: Path, separator: Separator
) -> Path:
    """The spec's run: the chosen vocal and master, written as the spec's result folder."""
    check_folders(work_dir, out_dir)
    inputs, original, vocal, chain = _chosen_mix(track, refs, work_dir, separator)
    beat, rate = inputs.track_stems.beat, inputs.track_stems.rate
    vocal = _with_timbre(TIMBRE, vocal, rate)
    target = round(statistics.median(_reference_loudness(inputs.refs)), 1)
    final, master = master_clip(vocal + beat, rate, target)
    final_lufs = integrated_lufs(final, rate)
    folder = new_run_dir(out_dir)
    keep_out_of_git(folder)
    (folder / "stems").mkdir()
    sf.write(folder / "final.wav", final, rate, subtype="PCM_24")
    lift = 10 ** ((final_lufs - integrated_lufs(original, rate)) / 20)
    sf.write(folder / "original.wav", original * np.float32(lift), rate, subtype="FLOAT")
    sf.write(folder / "stems" / "vocal.wav", vocal, rate, subtype="FLOAT")
    sf.write(folder / "stems" / "beat.wav", beat, rate, subtype="FLOAT")
    pair = {"final": final, "original": original}
    level = common_lufs(pair, rate)
    (folder / "compare").mkdir()
    for name, samples in pair.items():
        gain = 10 ** ((level - integrated_lufs(samples, rate)) / 20)
        sf.write(folder / "compare" / f"{name}.wav", samples * gain, rate, subtype="PCM_24")
    record = {
        "vocal_level": VOCAL_LEVEL,
        "vocal_db": VOCAL_DB,
        "timbre": TIMBRE,
        "vocal": asdict(chain),
        "master": {**asdict(master), "final_lufs": final_lufs},
    }
    (folder / "chain.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    after = {
        "mix.lufs": final_lufs,
        "mix.plr_db": true_peak_db(final) - final_lufs,
        "mix.crest_db": gated_crest_db(final, rate),
        "pair.vocal_minus_beat_lu": integrated_lufs(vocal, rate) - integrated_lufs(beat, rate),
    }
    write_final_report(
        folder / "report.md",
        chain,
        VOCAL_LEVEL,
        VOCAL_DB,
        TIMBRE,
        master,
        final_lufs,
        inputs.track.values,
        after,
        [r.values for r in inputs.refs],
    )
    return folder
