"""Rendering a seed's saved score to a beat and its stems, and measuring the render.

The render goes to `work/beats/<seed>/`: `beat.wav`, `stems/<sound>.wav` and `render.json`.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import redline_dsp
import soundfile as sf

from redline.beat import library
from redline.core.private import write_private
from redline.measure.beat import measure_beat, write_beat_report
from redline.measure.separate import Separator

ROOT = Path("private/beats")
WORK = Path("work")
RATE = 48000
# the instruments that play a library's zones
SAMPLED = ("sampler", "oneshot", "sample808")
BLOCK = 8192
QUIET = 1e-6
QUIET_S = 0.5
TAIL_S = 4.0
SAVED = re.compile(r"score-(\d+)\.json")


class RenderError(Exception):
    """A seed that cannot be rendered or measured as asked."""


@dataclass(frozen=True)
class Rendered:
    """What a render wrote.

    Attributes:
        folder: `work/beats/<seed>/`.
        frames: Frames of the mix and of every stem.
        sounds: The stems' sound names.
        non_commercial: Sounds playing a library a sold beat may not use.
    """

    folder: Path
    frames: int
    sounds: tuple[str, ...]
    non_commercial: tuple[str, ...]


@dataclass(frozen=True)
class Measured:
    """Where a measure wrote its reports."""

    markdown: Path
    json: Path


def sha256(path: Path) -> str:
    """The SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def latest_score(folder: Path) -> Path:
    """The seed folder's newest saved `score-K.json`.

    Raises:
        RenderError: The seed has no saved score.
    """
    found = []
    if folder.is_dir():
        found = [(int(m.group(1)), p) for p in folder.iterdir() if (m := SAVED.fullmatch(p.name))]
    if not found:
        raise RenderError(f"{folder}: no saved score; write one with `redline beat write`")
    return max(found)[1]


def notes_used(score_json: str, rate: float, body: dict[str, Any]) -> dict[str, set[int]]:
    """The MIDI notes each sampler sound plays, tune included, as its zones are picked."""
    table = json.loads(redline_dsp.beat_events(score_json, rate))
    names = [s["name"] for s in table["sounds"]]
    sounds = body.get("sounds", {})
    used: dict[str, set[int]] = {}
    for event in table["events"]:
        name = names[event["sound"]]
        sound = sounds.get(name, {})
        if sound.get("instrument") not in SAMPLED or "library" not in sound:
            continue
        tune = float(sound.get("params", {}).get("tune", 0.0))
        used.setdefault(name, set()).add(min(127, max(0, round(event["note"] + tune))))
    return used


def _load_samplers(
    player: Any,
    score_json: str,
    body: dict[str, Any],
    rate: float,
    sounds_root: Path,
    manifest: Path | None,
) -> list[dict[str, Any]]:
    """Load the zones every sampler plays; return the libraries used, for the report."""
    used = notes_used(score_json, rate, body)
    by_library: dict[str, list[str]] = {}
    for name in sorted(used):
        by_library.setdefault(body["sounds"][name]["library"], []).append(name)
    libraries = []
    for lib, names in sorted(by_library.items()):
        notes = set().union(*(used[n] for n in names))
        try:
            entry = library.source(lib, manifest)
            zones = library.zones(lib, sounds_root, sorted(notes), manifest)
        except library.LibraryError as error:
            raise RenderError(f"sound {', '.join(names)}: {error}") from error
        for zone in zones:
            try:
                data, zone_rate = sf.read(zone.path, dtype="int16", always_2d=False)
            except (OSError, sf.LibsndfileError) as error:
                raise RenderError(f"{zone.path}: cannot read: {error}") from error
            if data.ndim == 2 and data.shape[1] > 2:
                raise RenderError(f"{zone.path}: {data.shape[1]} channels, a zone takes 1 or 2")
            try:
                player.load_zone(
                    lib, zone.note, *zone.vel, float(zone_rate), np.ascontiguousarray(data)
                )
            except ValueError as error:
                raise RenderError(f"{zone.path}: {error}") from error
        libraries.append(
            {
                "name": lib,
                "licence": entry.licence,
                "commercial": entry.commercial,
                "sounds": names,
                "zones": len(zones),
            }
        )
    if libraries:
        try:
            player.rebuild_samplers()
        except ValueError as error:
            raise RenderError(str(error)) from error
    return libraries


Block = tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]


class _Aligned:
    """The mix and the stems with their own latencies dropped, handed out frame-aligned."""

    def __init__(self, sounds: int, mix_skip: int, stem_skip: int) -> None:
        self.mix = np.zeros((0, 2), np.float32)
        self.stems = np.zeros((sounds, 0, 2), np.float32)
        self.mix_skip = mix_skip
        self.stem_skip = stem_skip

    def push(self, mix: npt.NDArray[np.float32], stems: npt.NDArray[np.float32]) -> Block:
        drop = min(self.mix_skip, len(mix))
        self.mix_skip -= drop
        self.mix = np.concatenate([self.mix, mix[drop:]])
        drop = min(self.stem_skip, stems.shape[1])
        self.stem_skip -= drop
        self.stems = np.concatenate([self.stems, stems[:, drop:]], axis=1)
        n = min(len(self.mix), self.stems.shape[1])
        out = self.mix[:n], self.stems[:, :n]
        self.mix, self.stems = self.mix[n:], self.stems[:, n:]
        return out


class _Writers:
    """The mix file and one file per stem, written as frames come."""

    def __init__(self, folder: Path, sounds: list[str], rate: int) -> None:
        folder.mkdir(mode=0o700)
        (folder / "stems").mkdir(mode=0o700)

        def open_(path: Path) -> sf.SoundFile:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            return sf.SoundFile(fd, "w", samplerate=rate, channels=2, subtype="FLOAT", format="WAV")

        self.mix = open_(folder / "beat.wav")
        self.stems = [open_(folder / "stems" / f"{name}.wav") for name in sounds]
        self.frames = 0

    def write(self, block: Block) -> None:
        mix, stems = block
        self.mix.write(mix)
        for stem, part in zip(self.stems, stems, strict=True):
            stem.write(part)
        self.frames += len(mix)

    def close(self) -> None:
        for f in (self.mix, *self.stems):
            f.close()


def _stream(player: Any, writers: _Writers, rate: int, block: int) -> None:
    """Play the song and its tail, as the binding's whole render does.

    Runs past `player.end` (the song or the last voice's end, whichever is later) until the mix
    stays under QUIET for QUIET_S, or TAIL_S past `end` at the latest. Quiet frames past the
    song are held back and dropped at the end, so the files end on the last loud frame, never
    before the song's end.
    """
    song = player.length
    end = player.end
    cap = end + round(TAIL_S * rate)
    quiet_for = round(QUIET_S * rate)
    aligned = _Aligned(len(player.sounds), player.latency, player.stem_latency)
    held: list[Block] = []
    held_frames = 0
    done = 0
    while not (done >= end and (held_frames >= quiet_for or done >= cap)):
        mix, stems = aligned.push(*player.process(block))
        if done < song:
            head = min(song - done, len(mix))
            writers.write((mix[:head], stems[:, :head]))
            done += head
            mix, stems = mix[head:], stems[:, head:]
        if not len(mix):
            continue
        loud = np.flatnonzero((np.abs(mix) >= QUIET).any(axis=1))
        if len(loud):
            for part in held:
                writers.write(part)
            last = int(loud[-1]) + 1
            writers.write((mix[:last], stems[:, :last]))
            held, held_frames = [(mix[last:], stems[:, last:])], len(mix) - last
        else:
            held.append((mix, stems))
            held_frames += len(mix)
        done += len(mix)


def render_seed(
    seed: int,
    root: Path = ROOT,
    work: Path = WORK,
    rate: int = RATE,
    score: Path | None = None,
    manifest: Path | None = None,
    block: int = BLOCK,
) -> Rendered:
    """Render a seed's score to `<work>/beats/<seed>/`.

    Args:
        seed: The beat's seed.
        root: The beats folder holding `<seed>/score-K.json`.
        work: The work folder; samples are read from `<work>/sounds/`.
        rate: The render rate.
        score: A score file to render instead of the seed's newest.
        manifest: The sound manifest (default: the committed one).
        block: Frames a `process` call.

    Returns:
        What was written.

    Raises:
        RenderError: No score, a score the player refuses, a library not fetched or over the
            sample budget.
    """
    path = latest_score(root / str(seed)) if score is None else score
    try:
        text = path.read_text(encoding="utf-8")
        body = json.loads(text)
        player = redline_dsp.BeatPlayer(text, float(rate))
    except (OSError, ValueError) as error:
        raise RenderError(f"{path}: {error}") from error
    libraries = _load_samplers(player, text, body, float(rate), work / "sounds", manifest)
    sounds = list(player.sounds)
    folder = work / "beats" / str(seed)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkdir leaves a folder that already exists at its old mode
    folder.chmod(0o700)
    partial = folder / ".partial"
    shutil.rmtree(partial, ignore_errors=True)
    try:
        writers = _Writers(partial, sounds, rate)
        try:
            _stream(player, writers, rate, block)
        finally:
            writers.close()
        shutil.rmtree(folder / "stems", ignore_errors=True)
        (partial / "stems").rename(folder / "stems")
        (partial / "beat.wav").replace(folder / "beat.wav")
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    non_commercial = sorted(s for lib in libraries if not lib["commercial"] for s in lib["sounds"])
    report = {
        "score": path.name,
        "score_sha256": sha256(path),
        "rate": rate,
        "frames": writers.frames,
        "sounds": sounds,
        "libraries": libraries,
        "non_commercial": non_commercial,
    }
    write_private(folder / "render.json", json.dumps(report, indent=1) + "\n")
    return Rendered(folder, writers.frames, tuple(sounds), tuple(non_commercial))


def measure_seed(seed: int, refs: list[Path], work: Path, separator: Separator) -> Measured:
    """Measure a seed's render against the beat stems of references.

    Writes `measure.md` and `measure.json` next to the render.

    Raises:
        RenderError: The seed has no render.
        ValueError: A file cannot be measured.
    """
    folder = work / "beats" / str(seed)
    beat = folder / "beat.wav"
    if not beat.is_file():
        raise RenderError(f"{beat} is missing: run `redline beat render --seed {seed}` first")
    label = beat.name
    report = folder / "render.json"
    if report.is_file():
        with contextlib.suppress(ValueError, KeyError, TypeError):
            label = f"{json.loads(report.read_text(encoding='utf-8'))['score']} ({beat.name})"
    measured = measure_beat(beat, refs, work, separator)
    markdown, data = write_beat_report(folder, measured, label)
    return Measured(markdown, data)
