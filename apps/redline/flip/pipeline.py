"""A type beat's analysis (stems, grid, 808 notes) and the render of a flip of it.

Everything of one source lives in `work/flips/<sha8>/`, the sha8 being the start of the hash of
the source file: `analysis.json`, the 48 kHz stems in `stems48/`, `draft.json`, `flip-<n>.json`
and the renders `flip-<n>.wav` with their reports.
"""

import functools
import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.audio import AudioError, decode
from redline.core.private import make_private_dir, write_private
from redline.flip import model
from redline.flip import render as mixer
from redline.flip.grid import STEPS_PER_BAR, Grid, GridError, find_grid
from redline.flip.notes import LOW_CONFIDENCE, Note, find_notes, to_steps
from redline.flip.propose import bar_features, propose
from redline.flip.score import (
    Bass808Params,
    FlipNote,
    PlayBar,
    flip_score,
    render_flip_parts,
)
from redline.flip.stems import (
    FLIP_RATE,
    DrumSep,
    FourStems,
    NamedSeparator,
    separate_named,
    stems_at,
)
from redline.lyrics.text.parse import parse_text
from redline.measure.separate import SeparationError
from redline.record.takes import file_sha256
from redline.song.model import SongError, relative_to_root, resolve_inside

Samples = npt.NDArray[np.float32]

FORMAT = 1
ANALYSIS = "analysis.json"
STEMS_DIR = "stems48"
SOURCE_STEMS = ("drums", "bass", "other", "vocals")
FLIP_RENDER = re.compile(r"work/flips/([0-9a-f]{8})/flip-([1-9][0-9]*)\.wav")
PEAK_POINTS = 1000


class PipelineError(ValueError):
    """A flip step that cannot run, with the reason."""


@dataclass(frozen=True)
class Lineage:
    """Where a song's beat comes from.

    Attributes:
        source: The original beat file, relative to the repository.
        sha8: The folder of the source under work/flips.
        used: The flip render the beat is, or None for the source file itself.
    """

    source: str
    sha8: str
    used: int | None


@functools.lru_cache(maxsize=32)
def _sha8(path: str, mtime_ns: int, size: int) -> str:
    return file_sha256(Path(path))[:8]


def source_sha8(path: Path) -> str:
    """The folder name of a source: the start of its content hash, kept while the file stays."""
    try:
        stat = path.stat()
    except OSError as error:
        raise PipelineError(f"{path.name}: {error.strerror or error}") from None
    return _sha8(str(path), stat.st_mtime_ns, stat.st_size)


def flip_dir(root: Path, sha8: str) -> Path:
    """The folder of one source, resolved inside work/."""
    try:
        return resolve_inside(f"work/flips/{sha8}", root, "flip folder")
    except SongError as error:
        raise PipelineError(str(error)) from None


def inside(folder: Path, *parts: str) -> Path:
    """A file of the folder; a name that resolves (a symlink) out of the folder is refused."""
    path = folder.joinpath(*parts)
    if not path.resolve().is_relative_to(folder.resolve()):
        raise PipelineError(f"{'/'.join(parts)}: leaves the flip folder")
    return path


def lineage(beat: str, root: Path) -> Lineage:
    """The source of a song's beat file: a flip render leads back to its flip's source.

    Raises:
        PipelineError: The beat is a flip render without a readable flip, or the source is gone.
    """
    try:
        found = FLIP_RENDER.fullmatch(beat)
        if found is None:
            path = resolve_inside(beat, root, "beat")
            if not path.is_file():
                raise PipelineError(f"{beat}: no such file")
            return Lineage(beat, source_sha8(path), None)
        sha8, n = found.group(1), int(found.group(2))
        folder = flip_dir(root, sha8)
        flip = model.load(inside(folder, f"flip-{n}.json"))
        if not resolve_inside(flip.source, root, "flip source").is_file():
            raise PipelineError(f"{flip.source}: the flip's source is gone")
        return Lineage(flip.source, sha8, n)
    except (SongError, model.FlipError) as error:
        raise PipelineError(str(error)) from None


def read_analysis(folder: Path) -> dict[str, Any] | None:
    """The analysis of the folder's source; None before it has run."""
    try:
        data = json.loads(inside(folder, ANALYSIS).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise PipelineError(f"{ANALYSIS}: {error}") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise PipelineError(f"{ANALYSIS}: not an analysis this version reads")
    return data


def _need_analysis(folder: Path) -> dict[str, Any]:
    data = read_analysis(folder)
    if data is None:
        raise PipelineError("the beat is not analysed yet: run the flip_stems job")
    return data


def summary(analysis: dict[str, Any]) -> dict[str, Any]:
    """The analysis as the page shows it."""
    return {
        "bpm": analysis["bpm"],
        "downbeat_s": analysis["downbeat_s"],
        "bars": len(analysis["bar_starts_s"]),
        "bar_s": analysis["bar_s"],
        "detune_cents": analysis["detune_cents"],
        "notes": analysis["counts"]["notes"],
        "low_confidence": analysis["counts"]["low_confidence"],
        "duration_s": analysis["duration_s"],
    }


def versions(folder: Path) -> list[int]:
    """The n of every flip-<n>.json, ascending."""
    if not folder.is_dir():
        return []
    found = (model.FLIP_FILE.fullmatch(p.name) for p in folder.iterdir())
    return sorted(int(m.group(1)) for m in found if m)


def renders(folder: Path) -> list[int]:
    """The n of every flip-<n>.wav that exists, ascending."""
    if not folder.is_dir():
        return []
    found = (re.fullmatch(r"flip-(\d+)\.wav", p.name) for p in folder.iterdir())
    return sorted(int(m.group(1)) for m in found if m)


def _store_stems(folder: Path, stems: Mapping[str, Samples]) -> None:
    target = folder / STEMS_DIR
    staging = Path(tempfile.mkdtemp(prefix=".tmp-stems-", dir=folder))
    try:
        for name, stem in stems.items():
            sf.write(staging / f"{name}.wav", stem, FLIP_RATE, subtype="FLOAT")
        staging.chmod(0o700)
        shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def stem_names(folder: Path) -> list[str]:
    """The names of the 48 kHz stems the analysis kept."""
    base = folder / STEMS_DIR
    return sorted(p.stem for p in base.glob("*.wav")) if base.is_dir() else []


def load_stems(folder: Path) -> dict[str, Samples]:
    """The 48 kHz stems of the analysis, by name."""
    names = stem_names(folder)
    if not {"drums", "bass", "other"} <= set(names):
        raise PipelineError("the stems are missing: run the flip_stems job")
    out = {}
    for name in names:
        data, rate = sf.read(inside(folder, STEMS_DIR, f"{name}.wav"), dtype="float32")
        if rate != FLIP_RATE:
            raise PipelineError(f"stem {name} is at {rate} Hz, not {FLIP_RATE}")
        out[name] = np.asarray(data.reshape(len(data), -1), dtype=np.float32)
    return out


def _default_draft(source: str, analysis: dict[str, Any]) -> model.Flip:
    bars = len(analysis["bar_starts_s"])
    tune = round(analysis["detune_cents"] / 100.0, 3)
    return model.parse(
        {
            "format": model.FORMAT,
            "source": source,
            "grid": {"bpm": analysis["bpm"], "downbeat_s": analysis["downbeat_s"], "swing": 50},
            "bass808": {"mode": "original", "params": {"tune": tune}},
            "hats": {"mode": "original", "remove": [], "patterns": []},
            "gains": {"drums": 0, "other": 0, "bass": 0},
            "arrangement": [
                {"label": "all", "from_bar": 0, "to_bar": max(1, min(bars, model.MAX_BARS))}
            ],
            "effects": [],
        }
    )


def analyse(
    source_path: Path,
    work: Path,
    separator: NamedSeparator | None = None,
    root: Path | None = None,
    bpm: float | None = None,
    half: bool = False,
    double: bool = False,
) -> dict[str, Any]:
    """Separate a beat into stems, find its grid and 808 notes, and write them under work.

    Writes `analysis.json` and the 48 kHz stems, and a default draft when the folder has none.

    Args:
        source_path: The beat file.
        work: The work folder; the stems cache lives in it too.
        separator: The four-stem separator (htdemucs_ft by default).
        root: The repository root, which the stored source path is relative to; defaults to
            the parent of `work`.
        bpm: The tempo to use instead of the tracker's.
        half: Halve the tracker's tempo.
        double: Double it.

    Returns:
        The analysis as written.

    Raises:
        PipelineError: The file cannot be read, separated or gridded.
    """
    base = (root or work.parent).resolve()
    try:
        source = relative_to_root(source_path, base)
        decoded = decode(resolve_inside(source, base, "source"))
        stems = separate_named(decoded.samples, decoded.rate, separator or FourStems(), work)
        stems48 = stems_at(stems, decoded.rate)
        mono = {name: stem.mean(axis=1) for name, stem in stems48.items()}
        grid = find_grid(mono["drums"], mono["bass"], FLIP_RATE, bpm, half, double)
        notes, detune = find_notes(mono["bass"], FLIP_RATE, grid)
    except (AudioError, GridError, SeparationError, SongError) as error:
        raise PipelineError(str(error)) from None
    sha8 = source_sha8(resolve_inside(source, base, "source"))
    folder = make_private_dir(base / "work" / "flips" / sha8, base)
    steps = to_steps(notes, grid)
    whole = [b for b in grid.bars if b + grid.bar_s <= grid.duration_s + 1e-6]
    analysis: dict[str, Any] = {
        "format": FORMAT,
        "source": source,
        "sha8": sha8,
        "rate": FLIP_RATE,
        "frames": len(stems48["drums"]),
        "duration_s": grid.duration_s,
        "bpm": grid.bpm,
        "downbeat_s": grid.downbeat_s,
        "bar_s": grid.bar_s,
        "bar_starts_s": whole,
        "detune_cents": detune,
        "notes": [asdict(s) for s in steps],
        "note_times": [asdict(n) for n in notes],
        "counts": {
            "notes": len(steps),
            "low_confidence": sum(s.confidence < LOW_CONFIDENCE for s in steps),
        },
    }
    _store_stems(folder, stems48)
    write_private(folder / ANALYSIS, json.dumps(analysis, ensure_ascii=False))
    if not (folder / model.DRAFT).exists():
        model.save_draft(folder, _default_draft(source, analysis))
    return analysis


def _bass_params(values: Mapping[str, float]) -> Bass808Params:
    known = {f.name for f in fields(Bass808Params)}
    unknown = sorted(set(values) - known)
    if unknown:
        raise PipelineError(f"bass808.params: no {', '.join(unknown)}; one of {', '.join(known)}")
    return Bass808Params(**values)


def _play_bars(flip: model.Flip, analysis: dict[str, Any], frames: int) -> list[PlayBar]:
    """The play order with, for our 808, the source notes re-snapped on the flip's own grid."""
    notes_of: dict[int, list[FlipNote]] = {}
    if flip.bass_mode == "ours":
        grid = Grid(flip.bpm, flip.downbeat_s, (), frames / FLIP_RATE)
        notes = [Note(**raw) for raw in analysis["note_times"]]
        for step in to_steps(notes, grid):
            bar, at = divmod(step.step, STEPS_PER_BAR)
            if bar >= 0:
                notes_of.setdefault(bar, []).append(FlipNote(at, step.length, step.midi))
    play = []
    for index, block in enumerate(flip.arrangement):
        hat = flip.hats_patterns[index] if flip.hats_mode == "ours" else None
        for bar in range(block.from_bar, block.to_bar):
            play.append(PlayBar(bar, tuple(notes_of.get(bar, ())), hat))
    return play


def render(folder: Path, n: int, drumsep: NamedSeparator | None = None) -> Path:
    """Render flip-<n>.json of the folder to flip-<n>.wav with its report.

    The flip's grid replaces the analysis grid; our 808 and hats come from a generated score
    in play order; DrumSep runs only when the hats are removed or replaced.

    Args:
        folder: `work/flips/<sha8>`.
        n: The flip's number.
        drumsep: The drum separator (MDX23C DrumSep by default).

    Returns:
        The path of the wav.

    Raises:
        PipelineError: The flip or the analysis is missing or unusable.
        ValueError: The arrangement leaves the source, or a score setting is refused.
    """
    path = inside(folder, f"flip-{n}.json")
    if not path.is_file():
        raise PipelineError(f"flip-{n}.json: no such flip")
    try:
        flip = model.load(path)
    except model.FlipError as error:
        raise PipelineError(str(error)) from None
    analysis = _need_analysis(folder)
    stems = load_stems(folder)
    frames = len(stems["drums"])
    source = mixer.source_bar_starts(flip, frames, FLIP_RATE)
    mixer.play_bar_starts(source, flip.arrangement)

    notes: list[str] = []
    ours: dict[str, Samples] = {}
    if "ours" in (flip.bass_mode, flip.hats_mode):
        score = flip_score(
            _play_bars(flip, analysis, frames),
            flip.bpm,
            flip.swing,
            _bass_params(flip.bass_params),
            compromises=notes,
        )
        parts = render_flip_parts(score)
        ours = {"bass808": parts["bass"], "hats": parts["hat"]}
    if flip.hats_mode != "original":
        stems.update(separate_named(stems["drums"], FLIP_RATE, drumsep or DrumSep(), _work(folder)))
    mix, lowered_db = mixer.fit_ceiling(mixer.mix_flip(stems, ours, flip))
    return mixer.write_render(folder, n, mix, FLIP_RATE, _report(flip, notes, lowered_db))


def _work(folder: Path) -> Path:
    return folder.resolve().parent.parent


def _report(flip: model.Flip, compromises: list[str], lowered_db: float = 0.0) -> str:
    lines = [f"{flip.play_bars} bars in play order at {flip.bpm:.2f} BPM, swing {flip.swing:g}."]
    if lowered_db:
        lines.append(
            f"The render was lowered by {-lowered_db:.1f} dB to peak at {mixer.CEILING_DB:g} dBFS."
        )
    if flip.bass_mode == "ours":
        lines.append("The 808 is ours, played from the notes read off the bass stem.")
    if flip.hats_mode != "original":
        lines.append(f"Removed with DrumSep: {', '.join(flip.hats_remove)}.")
    if compromises:
        lines += ["", "Notes the score had to shorten:", *(f"- {line}" for line in compromises)]
    return "\n".join(lines)


def clean(folder: Path, keep: set[int]) -> list[int]:
    """Remove old renders of the folder but the newest and the kept ones; returns the n removed."""
    return mixer.clean(folder, keep)


def waveform(mono: npt.NDArray[np.floating[Any]], points: int = PEAK_POINTS) -> list[list[float]]:
    """About `points` [min, max] pairs over a mono signal."""
    if mono.size == 0:
        return []
    size = max(1, -(-mono.size // points))
    padded = np.pad(mono, (0, -mono.size % size), mode="edge").reshape(-1, size)
    low, high = padded.min(axis=1), padded.max(axis=1)
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(low, high, strict=True)]


def source_waveform(path: Path) -> list[list[float]]:
    """The peaks of a source file, mono."""
    try:
        return waveform(decode(path).samples.mean(axis=1))
    except AudioError as error:
        raise PipelineError(str(error)) from None


def stem_waveform(folder: Path, name: str) -> list[list[float]]:
    """The peaks of one 48 kHz stem."""
    if name not in stem_names(folder):
        raise PipelineError(f"no stem {name!r}")
    return waveform(load_stems(folder)[name].mean(axis=1))


def sections_of(text: str) -> list[tuple[str, int]]:
    """A lyrics text as (section kind, line count) in order."""
    return [(s.kind, len(s.lines)) for s in parse_text(text).sections]


def propose_blocks(source_path: Path, folder: Path, text: str) -> list[dict[str, int | str]]:
    """A first arrangement for a lyrics text, over the bars of the draft's grid.

    Raises:
        PipelineError: The text has no lines, the beat is not analysed, or it cannot be read.
    """
    analysis = _need_analysis(folder)
    sections = sections_of(text)
    if not any(count for _, count in sections):
        raise PipelineError("the text has no lines")
    bpm, downbeat = analysis["bpm"], analysis["downbeat_s"]
    draft = folder / model.DRAFT
    if draft.is_file():
        try:
            flip = model.load(inside(folder, model.DRAFT))
            bpm, downbeat = flip.bpm, flip.downbeat_s
        except model.FlipError:
            pass
    grid = Grid(bpm, downbeat, (), analysis["duration_s"]).with_downbeat(downbeat)
    bars = [b for b in grid.bars if b + grid.bar_s <= grid.duration_s + 1e-6]
    try:
        decoded = decode(source_path)
    except AudioError as error:
        raise PipelineError(str(error)) from None
    features = bar_features(decoded.samples.mean(axis=1), decoded.rate, bars)
    if len(features) == 0:
        raise PipelineError("the beat has no whole bar")
    return propose(sections, features)
