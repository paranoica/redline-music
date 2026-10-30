"""Stage 3: the blind rounds on the master, the timbre and the 808, and the run itself."""

import ctypes
import errno
import json
import os
import re
import shutil
import statistics
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.audio import true_peak_db
from redline.core.listen import common_lufs
from redline.core.loudness import gated_crest_db, integrated_lufs
from redline.core.spectrum import low_end
from redline.engine import racks_render
from redline.engine.render import Engine, timbre_of
from redline.engine.settings import SPECS, Settings, to_dict
from redline.engine.track import reference_loudness, track_engine
from redline.master.report import write_final_report
from redline.measure.run import FileResult, Inputs, keep_out_of_git, measure_inputs
from redline.measure.separate import Separator
from redline.output import ORIGINAL, VocalRun, check_folders
from redline.vocal.rounds import Heard, blind_round

Samples = npt.NDArray[np.floating]

QUIET_MASTER_LUFS = -11.0
NO_MASTER = "no master"
BASE = "base"
TIMBRE_EFFECTS = ("doubles", "octave down", "bitcrush")
VOICE_EFFECTS = (
    "formant up",
    "formant down",
    "formant x<ratio>",
    "hard tune",
    "octave up",
    "voice up",
    "nightcore",
)
EFFECTS = TIMBRE_EFFECTS + VOICE_EFFECTS
TIMBRE = "octave down + doubles"
# each new voice on top of the timbre already picked, pushed far enough to be heard under
# the drive and the beat
VOICES = (
    f"formant x1.35 + {TIMBRE}",
    f"formant x0.78 + {TIMBRE}",
    f"voice up + {TIMBRE}",
    f"nightcore + {TIMBRE}",
)
FORMANT_UP = 1.2
FORMANT_DOWN = 0.85
FORMANT_RATIO = re.compile(r"formant x(\d+(?:\.\d+)?)")
BASS_DRIVES_DB = (0.0, 6.0, 12.0, 18.0, 24.0)
# the chosen sound without its timbre, what each named effect is switched on over
BARE = replace(Settings(), octave_down_on=False, doubles_on=False)
EFFECT_SETTINGS: dict[str, dict[str, Any]] = {
    "doubles": {"doubles_on": True},
    "octave down": {"octave_down_on": True},
    "bitcrush": {"bitcrush_on": True},
    "formant up": {"formant": FORMANT_UP},
    "formant down": {"formant": FORMANT_DOWN},
    "hard tune": {"hard_tune": 100.0},
    "octave up": {"octave_up_on": True},
    "voice up": {"voice_octave": 1},
    "nightcore": {"nightcore": True},
}


def master_targets(refs: list[FileResult]) -> dict[str, float]:
    """Loudness targets for a round: a quiet master, the references' median, their loudest."""
    known = reference_loudness(refs)
    targets = sorted(
        {round(t, 1) for t in (QUIET_MASTER_LUFS, statistics.median(known), max(known))}
    )
    return {f"master {t:.1f}": t for t in targets}


def _master_record(name: str, rendered: Heard, rate: int) -> dict[str, Any]:
    if rendered.master is None:
        raise ValueError(f"{name}: rendered without a master")
    return {
        "name": name,
        **asdict(rendered.master),
        "final_lufs": integrated_lufs(rendered.final, rate),
    }


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
    engine = track_engine(inputs)
    rate = engine.context.rate
    chosen = Settings()
    targets = master_targets(inputs.refs)
    variants: dict[str, Settings | Samples] = {
        ORIGINAL: engine.vocal + engine.beat,
        NO_MASTER: replace(chosen, master_on=False),
    }
    for name, target in targets.items():
        variants[name] = replace(chosen, master_auto=False, master_lufs=target)

    def record(renders: dict[str, Heard]) -> dict[str, Any]:
        return {
            "vocal": asdict(renders[NO_MASTER].chain),
            "vocal_db": chosen.vocal_db,
            "timbre": timbre_of(chosen),
            "bass_drive_db": chosen.bass_drive_db,
            "masters": [_master_record(name, renders[name], rate) for name in targets],
        }

    return blind_round(engine, variants, work_dir, out_dir, "master", seed, record)[0]


def _effects(name: str) -> list[str]:
    return [part.strip() for part in name.split("+")]


def _effect_settings(effect: str) -> dict[str, Any] | None:
    if effect in EFFECT_SETTINGS:
        return EFFECT_SETTINGS[effect]
    named = FORMANT_RATIO.fullmatch(effect)
    spec = SPECS["formant"]
    if named and spec.low <= float(named.group(1)) <= spec.high:
        return {"formant": float(named.group(1))}
    return None


def timbre_settings(name: str) -> Settings:
    """The chosen sound without its timbre, with the named effects switched on.

    The engine renders them in its own order, the voice before the layers, whatever order
    they are named in.

    Args:
        name: The effects, joined by " + ".

    Raises:
        ValueError: An effect is unknown, or two of them set the same setting.
    """
    changes: dict[str, Any] = {}
    for effect in _effects(name):
        change = _effect_settings(effect)
        if change is None:
            raise ValueError(f"unknown timbre {effect}, expected some of {', '.join(EFFECTS)}")
        if again := sorted(changes.keys() & change.keys()):
            raise ValueError(f"{name!r} sets {', '.join(again)} twice")
        changes.update(change)
    return replace(BARE, **changes)


def timbre_round(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    seed: int | None = None,
    timbres: tuple[str, ...] = TIMBRE_EFFECTS,
    axis: str = "timbre",
) -> VocalRun:
    """Blind round on the vocal's timbre: the original, the chosen sound, one file per effect.

    On the voice axis the base carries the chosen timbre, as the final does, and each variant
    names the chosen timbre with one new effect.
    """
    unknown = [e for name in timbres for e in _effects(name) if _effect_settings(e) is None]
    if unknown:
        raise ValueError(
            f"unknown timbre {', '.join(unknown)}, expected some of {', '.join(EFFECTS)}"
        )
    named = {name: timbre_settings(name) for name in timbres}
    check_folders(work_dir, out_dir)
    engine = track_engine(measure_inputs(track, refs, work_dir, separator))
    variants: dict[str, Settings | Samples] = {BASE: Settings() if axis == "voice" else BARE}
    if axis != "voice":
        variants[ORIGINAL] = engine.vocal + engine.beat
    variants.update(named)
    record = {"timbres": timbres, "tuning_cents": engine.context.tuning_cents}
    return blind_round(engine, variants, work_dir, out_dir, axis, seed, lambda _: record)[0]


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
    inputs = measure_inputs(track, refs, work_dir, separator)
    engine = track_engine(inputs)
    rate = engine.context.rate
    drives = {f"808 +{drive_db:.0f} dB": drive_db for drive_db in BASS_DRIVES_DB}
    variants: dict[str, Settings | Samples] = {ORIGINAL: engine.vocal + engine.beat}
    for name, drive_db in drives.items():
        variants[name] = replace(Settings(), bass_drive_db=drive_db)

    def record(renders: dict[str, Heard]) -> dict[str, Any]:
        masters = [
            {
                **_master_record(name, renders[name], rate),
                "drive_db": drive_db,
                "low_crest_db": low_end(renders[name].final, rate).crest_db,
            }
            for name, drive_db in drives.items()
        ]
        refs_low = [r.values.get("beat.low_crest_db") for r in inputs.refs]
        return {"masters": masters, "refs_low_crest_db": refs_low}

    return blind_round(engine, variants, work_dir, out_dir, "808", seed, record)[0]


def run_track(
    track: Path,
    refs: list[Path],
    work_dir: Path,
    out_dir: Path,
    separator: Separator,
    settings: Settings | None = None,
    classic: bool = False,
) -> Path:
    """The spec's run: the chain at the given settings (the picks by default), as a folder.

    Rendered through the studio's racks, or through the Python engine when classic.
    """
    check_folders(work_dir, out_dir)
    inputs = measure_inputs(track, refs, work_dir, separator)
    engine = track_engine(inputs)
    return write_result(inputs, engine, out_dir, track.stem, settings, classic=classic)


def run_stamp(label: str) -> str:
    """The time and the label a result folder is named by."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = re.sub(r"[^\w.-]+", "_", label).strip("._")[:40]
    return f"{stamp}-{label}" if label else stamp


def claim_tmp_dir(out_dir: Path, stamp: str) -> Path:
    """Make the hidden folder a result is built in; the visible name is taken only at the end."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for attempt in range(100):
        folder = out_dir / f".tmp-{stamp}{'' if attempt == 0 else f'-{attempt}'}"
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        return folder
    raise RuntimeError(f"cannot create a build folder under {out_dir}")


AT_FDCWD = -100
RENAME_NOREPLACE = 1


def _rename_noreplace(source: Path, target: Path) -> int:
    """renameat2 with RENAME_NOREPLACE; 0, or the errno (ENOSYS when the libc lacks it)."""
    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        return errno.ENOSYS
    done = renameat2(AT_FDCWD, os.fsencode(source), AT_FDCWD, os.fsencode(target), RENAME_NOREPLACE)
    return 0 if done == 0 else ctypes.get_errno()


def _rename_new(source: Path, target: Path) -> None:
    """Rename, refusing an existing target, an empty folder too (which rename takes over)."""
    code = _rename_noreplace(source, target)
    if code in (errno.EINVAL, errno.ENOSYS):
        # a filesystem or libc without the flag: check, then rename, as before it
        if target.exists():
            raise FileExistsError(errno.EEXIST, "exists", str(target))
        source.replace(target)
    elif code:
        raise OSError(code, os.strerror(code), str(target))


def publish(folder: Path, out_dir: Path, stamp: str) -> Path:
    """Rename the built folder to a result name that does not exist yet."""
    for attempt in range(100):
        target = out_dir / (stamp if attempt == 0 else f"{stamp}-{attempt}")
        if target.exists():
            continue
        try:
            _rename_new(folder, target)
        except OSError as error:
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY):
                continue
            raise
        return target
    raise RuntimeError(f"cannot name a result folder under {out_dir}")


def write_result(
    inputs: Inputs,
    engine: Engine,
    out_dir: Path,
    label: str,
    settings: Settings | None = None,
    extra: dict[str, Any] | None = None,
    classic: bool = False,
) -> Path:
    """Render a measured track and write the result folder.

    Args:
        inputs: The track's and the references' measurements, with the track's stems.
        engine: The track's engine, from track_engine.
        out_dir: Where the result folder is made.
        label: The folder's name after the time.
        settings: The settings to render at; the picks when None.
        extra: More entries for chain.json, after the usual ones.
        classic: Render with the Python engine instead of the racks the studio plays.

    Returns:
        The folder with final.wav, original.wav, stems/, compare/, chain.json and report.md.
    """
    settings = settings or Settings()
    rate = engine.context.rate
    original = inputs.track_stems.vocal + inputs.track_stems.beat
    rendered = engine.render(settings) if classic else racks_render.render(engine, settings)
    vocal, beat, final = rendered.vocal, rendered.beat, rendered.final
    chain, master = rendered.chain, rendered.master
    final_lufs = integrated_lufs(final, rate)
    stamp = run_stamp(label)
    folder = claim_tmp_dir(out_dir, stamp)
    try:
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
            "render": "classic" if classic else "racks",
            "vocal_level": chain.level,
            "vocal_db": settings.vocal_db,
            "timbre": timbre_of(settings),
            "bass_drive_db": settings.bass_drive_db,
            "settings": to_dict(settings),
            "tuning_cents": engine.context.tuning_cents,
            "vocal": asdict(chain),
            "master": None if master is None else {**asdict(master), "final_lufs": final_lufs},
            **(extra or {}),
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
            chain.level,
            settings.vocal_db,
            timbre_of(settings),
            settings.bass_drive_db,
            master,
            final_lufs,
            inputs.track.values,
            after,
            [r.values for r in inputs.refs],
        )
        return publish(folder, out_dir, stamp)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
