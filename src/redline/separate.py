"""Vocal/beat separation behind one interface, with a content-keyed stem cache."""

import hashlib
import importlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.audio import Audio

PIPELINE_VERSION = "1"
HEADROOM_PEAK = 0.99
MODEL = "vocals_mel_band_roformer.ckpt"
MODEL_DIR = Path.home() / ".cache" / "redline" / "models"

Samples = npt.NDArray[np.float32]
log = logging.getLogger(__name__)


class Separator(Protocol):
    """Splits a stereo signal into vocal and beat of the same shape."""

    id: str

    def __call__(self, samples: Samples, rate: int) -> tuple[Samples, Samples]:
        """Return (vocal, beat), each shaped like samples."""
        ...


@dataclass(frozen=True)
class Stems:
    """Vocal and beat at the analysis rate; cached tells whether the model ran."""

    vocal: Samples
    beat: Samples
    rate: int
    cached: bool


def cache_key(audio: Audio, separator_id: str) -> str:
    """Hash of the samples, the rate, the separator and the pipeline version."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(audio.samples, dtype=np.float32).tobytes())
    digest.update(f"|{audio.rate}|{separator_id}|{PIPELINE_VERSION}".encode())
    return digest.hexdigest()


def separate(audio: Audio, separator: Separator, cache_dir: Path) -> Stems:
    """Return the stems from the cache, or run the separator and cache them atomically."""
    folder = cache_dir / "stems" / cache_key(audio, separator.id)
    if (folder / "vocal.wav").is_file() and (folder / "beat.wav").is_file():
        return Stems(_read(folder / "vocal.wav"), _read(folder / "beat.wav"), audio.rate, True)
    peak = float(np.abs(audio.samples).max())
    scale = HEADROOM_PEAK / peak if peak > HEADROOM_PEAK else 1.0
    vocal, beat = separator(audio.samples * np.float32(scale), audio.rate)
    for stem in (vocal, beat):
        if stem.shape != audio.samples.shape:
            raise ValueError(f"stem length {stem.shape} differs from input {audio.samples.shape}")
    vocal = (vocal / scale).astype(np.float32)
    beat = (beat / scale).astype(np.float32)
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=folder.parent))
    try:
        sf.write(staging / "vocal.wav", vocal, audio.rate, subtype="FLOAT")
        sf.write(staging / "beat.wav", beat, audio.rate, subtype="FLOAT")
        staging.rename(folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Stems(vocal, beat, audio.rate, False)


def _read(path: Path) -> Samples:
    data, _ = sf.read(path, always_2d=True, dtype="float32")
    return np.asarray(data, dtype=np.float32)


def _fit(stem: Samples, length: int) -> Samples:
    if len(stem) >= length:
        return stem[:length]
    return np.pad(stem, ((0, length - len(stem)), (0, 0)))


class RoformerSeparator:
    """MelBand Roformer through audio-separator; falls back to CPU when the GPU fails."""

    id = f"audio-separator:{MODEL}:normalization=1.0"

    def __call__(self, samples: Samples, rate: int) -> tuple[Samples, Samples]:
        """Separate on the GPU if there is one, else or on failure on the CPU."""
        with tempfile.TemporaryDirectory(prefix="redline-sep-") as work:
            source = Path(work) / "input.wav"
            sf.write(source, samples, rate, subtype="FLOAT")
            try:
                vocal_path, beat_path = run_model(source, Path(work))
            except RuntimeError as error:
                log.warning("separation on the GPU failed (%s); retrying on the CPU", error)
                vocal_path, beat_path = _run_on_cpu(source, Path(work))
            return _fit(_read(vocal_path), len(samples)), _fit(_read(beat_path), len(samples))


def run_model(source: Path, out: Path) -> tuple[Path, Path]:
    """Run the model on a WAV file and return the vocal and beat file paths."""
    module = importlib.import_module("audio_separator.separator")
    engine = module.Separator(
        model_file_dir=str(MODEL_DIR),
        output_dir=str(out),
        output_format="WAV",
        normalization_threshold=1.0,
        log_level=logging.WARNING,
    )
    if "cuda" not in str(getattr(engine, "torch_device", "")):
        log.warning("no CUDA device found; separating on the CPU, expect several minutes")
    engine.load_model(model_filename=MODEL)
    names = {"Vocals": "vocal", "Other": "beat", "Instrumental": "beat"}
    written = [out / Path(name).name for name in engine.separate(str(source), names)]
    return out / "vocal.wav", next(p for p in written if p.stem == "beat")


def _run_on_cpu(source: Path, out: Path) -> tuple[Path, Path]:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    subprocess.run(
        [sys.executable, "-m", "redline.separate", str(source), str(out)], env=env, check=True
    )
    return out / "vocal.wav", out / "beat.wav"


if __name__ == "__main__":
    run_model(Path(sys.argv[1]), Path(sys.argv[2]))
