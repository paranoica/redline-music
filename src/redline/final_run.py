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
from redline.master import master_clip, saturate_low
from redline.run import FileResult, Inputs, keep_out_of_git, measure_inputs, new_run_dir
from redline.separate import Separator
from redline.spectrum import low_end
from redline.timbre import bitcrush, double, formant_shift, hard_tune, octave_layer
from redline.vocal_run import ORIGINAL, VocalRun, check_folders, reference_target

Samples = npt.NDArray[np.floating]

VOCAL_LEVEL = "loudest"
VOCAL_DB = -6.0
QUIET_MASTER_LUFS = -11.0
NO_MASTER = "no master"
BASE = "base"
TIMBRE_EFFECTS = ("doubles", "octave down", "bitcrush")
VOICE_EFFECTS = ("formant up", "formant down", "hard tune", "octave up")
EFFECTS = TIMBRE_EFFECTS + VOICE_EFFECTS
TIMBRES = TIMBRE_EFFECTS
TIMBRE = "octave down + doubles"
# each new voice on top of the timbre already picked; formant down only when asked for
VOICES = (f"{TIMBRE} + formant up", f"hard tune + {TIMBRE}", f"{TIMBRE} + octave up")
FORMANT_UP = 1.2
FORMANT_DOWN = 0.85
BASS_FREQ_HZ = 120.0
BASS_DRIVES_DB = (0.0, 6.0, 12.0, 18.0, 24.0)
BASS_DRIVE_DB = 12.0
# the level picked by ear
OCTAVE_LEVEL_DB = -9.5
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


def _chosen_sound(vocal: Samples, beat: Samples, rate: int) -> tuple[Samples, Samples]:
    """The chosen timbre on the vocal and the chosen drive on the 808."""
    return _with_timbre(TIMBRE, vocal, rate), saturate_low(beat, rate, BASS_FREQ_HZ, BASS_DRIVE_DB)


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
    vocal, beat = _chosen_sound(vocal, inputs.track_stems.beat, rate)
    mix = vocal + beat
    variants = {ORIGINAL: original, NO_MASTER: mix}
    masters = []
    for name, target in master_targets(inputs.refs).items():
        log.info("mastering: %s", name)
        mastered, result = master_clip(mix, rate, target)
        variants[name] = mastered
        masters.append(
            {"name": name, **asdict(result), "final_lufs": integrated_lufs(mastered, rate)}
        )
    record = {
        "vocal": asdict(chain),
        "vocal_db": VOCAL_DB,
        "timbre": TIMBRE,
        "bass_drive_db": BASS_DRIVE_DB,
        "masters": masters,
    }
    return _write_round(work_dir, out_dir, "master", variants, rate, seed, record)


def _effects(name: str) -> list[str]:
    return [part.strip() for part in name.split("+")]


def _with_timbre(name: str, vocal: Samples, rate: int, tuning_cents: float = 0.0) -> Samples:
    """Apply one effect, or several joined by " + ", in order.

    Args:
        name: The effects, joined by " + ".
        vocal: The processed vocal.
        rate: Its sample rate.
        tuning_cents: The track's own tuning against A440, the grid hard tune snaps to.
    """
    out = vocal
    for effect in _effects(name):
        if effect == "doubles":
            out = double(out, rate)
        elif effect in ("octave down", "octave up"):
            out = octave_layer(out, rate, -1 if effect == "octave down" else 1, OCTAVE_LEVEL_DB)
        elif effect in ("formant up", "formant down"):
            out = formant_shift(out, rate, FORMANT_UP if effect == "formant up" else FORMANT_DOWN)
        elif effect == "hard tune":
            out = hard_tune(out, rate, tuning_cents)
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
    axis: str = "timbre",
) -> VocalRun:
    """Blind round on the vocal's timbre: the original, the chosen sound, one file per effect.

    On the voice axis the base carries the chosen timbre, as the final does, and each variant
    names the chosen timbre with one new effect.
    """
    unknown = [e for name in timbres for e in _effects(name) if e not in EFFECTS]
    if unknown:
        raise ValueError(
            f"unknown timbre {', '.join(unknown)}, expected some of {', '.join(EFFECTS)}"
        )
    check_folders(work_dir, out_dir)
    inputs, original, vocal, _ = _chosen_mix(track, refs, work_dir, separator)
    rate = inputs.track_stems.rate
    beat = saturate_low(inputs.track_stems.beat, rate, BASS_FREQ_HZ, BASS_DRIVE_DB)
    target = round(statistics.median(_reference_loudness(inputs.refs)), 1)
    tuning = float(inputs.track.values.get("vocal.tuning_cents") or 0.0)
    base = _with_timbre(TIMBRE, vocal, rate) if axis == "voice" else vocal
    variants = {ORIGINAL: original, BASE: master_clip(base + beat, rate, target)[0]}
    for name in timbres:
        log.info("%s: %s", axis, name)
        sound = _with_timbre(name, vocal, rate, tuning)
        variants[name] = master_clip(sound + beat, rate, target)[0]
    record = {"timbres": timbres, "tuning_cents": tuning}
    return _write_round(work_dir, out_dir, axis, variants, rate, seed, record)


def _write_round(
    work_dir: Path,
    out_dir: Path,
    axis: str,
    variants: dict[str, Samples],
    rate: int,
    seed: int | None,
    record: dict,
) -> VocalRun:
    run_dir = new_run_dir(work_dir / "runs")
    out_run = new_run_dir(out_dir)
    keep_out_of_git(out_run)
    listen_dir = out_run / "listen"
    seed = secrets.randbits(32) if seed is None else seed
    key = write_listen_set(variants, rate, listen_dir, seed)
    (run_dir / "key.json").write_text(
        json.dumps({"axis": axis, "seed": seed, "key": key, "listen_dir": str(listen_dir)}),
        encoding="utf-8",
    )
    (run_dir / "chain.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    return VocalRun(run_dir, listen_dir)


def bass_round(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
) -> VocalRun:
    """Blind round on the 808: the chosen vocal and master, the beat's low band driven harder.

    The master's target is held, so each file reads as loud as the others before the
    listening set levels them; the clip depth each one needed is recorded.
    """
    check_folders(work_dir, out_dir)
    inputs, original, vocal, _ = _chosen_mix(track, refs, work_dir, separator)
    beat, rate = inputs.track_stems.beat, inputs.track_stems.rate
    vocal = _with_timbre(TIMBRE, vocal, rate)
    target = round(statistics.median(_reference_loudness(inputs.refs)), 1)
    variants = {ORIGINAL: original}
    masters = []
    for drive_db in BASS_DRIVES_DB:
        name = f"808 +{drive_db:.0f} dB"
        log.info("808: %s", name)
        mixed = vocal + saturate_low(beat, rate, BASS_FREQ_HZ, drive_db)
        final, result = master_clip(mixed, rate, target)
        variants[name] = final
        masters.append(
            {
                "name": name,
                "drive_db": drive_db,
                **asdict(result),
                "final_lufs": integrated_lufs(final, rate),
                "low_crest_db": low_end(final, rate).crest_db,
            }
        )
    refs_low = [r.values.get("beat.low_crest_db") for r in inputs.refs]
    record = {"masters": masters, "refs_low_crest_db": refs_low}
    return _write_round(work_dir, out_dir, "808", variants, rate, seed, record)


def run_track(
    track: Path, refs: list[Path], work_dir: Path, out_dir: Path, separator: Separator
) -> Path:
    """The spec's run: the chosen vocal and master, written as the spec's result folder."""
    check_folders(work_dir, out_dir)
    inputs, original, vocal, chain = _chosen_mix(track, refs, work_dir, separator)
    beat, rate = inputs.track_stems.beat, inputs.track_stems.rate
    vocal, beat = _chosen_sound(vocal, beat, rate)
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
        "bass_drive_db": BASS_DRIVE_DB,
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
        BASS_DRIVE_DB,
        master,
        final_lufs,
        inputs.track.values,
        after,
        [r.values for r in inputs.refs],
    )
    return folder
