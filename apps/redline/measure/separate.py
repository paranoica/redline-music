"""Vocal/beat separation behind one interface, with a content-keyed stem cache."""

import hashlib
import importlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.audio import Audio
from redline.measure.fingerprint import fingerprint, library_versions, module_sources

HEADROOM_PEAK = 0.99
MODEL = "vocals_mel_band_roformer.ckpt"
MODEL_DIR = Path.home() / ".cache" / "redline" / "models"
# Pinned on first download (the files the owner listened to); a changed file is refused.
MODEL_SHA256 = {
    "vocals_mel_band_roformer.ckpt": (
        "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"
    ),
    "vocals_mel_band_roformer.yaml": (
        "b958b29c8f7195f0d86bee6759a33980db675c4ecaf2fcaa80fa125828e6cd38"
    ),
}

Samples = npt.NDArray[np.float32]
ModelRunner = Callable[[Path, Path], tuple[Path, Path]]
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


def stems_code() -> str:
    """Fingerprint of the separation code and torch, part of every stem cache key."""
    return fingerprint(
        module_sources(["measure.separate"]),
        library_versions(["torch", "rotary-embedding-torch", "einops"]),
    )


def cache_key(audio: Audio, separator_id: str, code: str | None = None) -> str:
    """Hash of the samples, the rate, the separator and the separation code."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(audio.samples, dtype=np.float32).tobytes())
    digest.update(f"|{audio.rate}|{separator_id}|{code or stems_code()}".encode())
    return digest.hexdigest()


def separate(audio: Audio, separator: Separator, cache_dir: Path) -> Stems:
    """Return the stems from the cache, or run the separator and cache them atomically."""
    folder = cache_dir / "stems" / cache_key(audio, separator.id)
    if _complete(folder):
        try:
            cached = Stems(
                _read(folder / "vocal.wav"), _read(folder / "beat.wav"), audio.rate, True
            )
        except sf.LibsndfileError as error:
            log.warning("cached stems unreadable (%s); separating again", error)
            shutil.rmtree(folder, ignore_errors=True)
        else:
            log.info("stems from cache: %s", folder.name[:12])
            return cached
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
    except OSError:
        # another run may have finished the same entry first
        if not _complete(folder):
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Stems(vocal, beat, audio.rate, False)


def _complete(folder: Path) -> bool:
    return (folder / "vocal.wav").is_file() and (folder / "beat.wav").is_file()


def _read(path: Path) -> Samples:
    data, _ = sf.read(path, always_2d=True, dtype="float32")
    return np.asarray(data, dtype=np.float32)


def _fit(stem: Samples, length: int) -> Samples:
    if len(stem) >= length:
        return stem[:length]
    return np.pad(stem, ((0, length - len(stem)), (0, 0)))


class SeparationError(Exception):
    """Separation could not run; the message says what to do."""


def separator_id(library_version: str) -> str:
    """Cache identity of the Roformer setup: a new library version may change the stems."""
    return f"audio-separator {library_version}:{MODEL}:normalization=1.0"


def _library_version() -> str:
    try:
        return metadata.version("audio-separator")
    except metadata.PackageNotFoundError:
        return "missing"


def _is_gpu_error(error: RuntimeError) -> bool:
    text = str(error).lower()
    return "cuda" in text or "cudnn" in text or "out of memory" in text


class RoformerSeparator:
    """MelBand Roformer through audio-separator; falls back to CPU when the GPU fails."""

    def __init__(self, run: ModelRunner | None = None, run_on_cpu: ModelRunner | None = None):
        """Take the model runners; the defaults run the real model in and out of process."""
        self.id = separator_id(_library_version())
        self.fallback_used = False
        self._run = run or run_model
        self._run_on_cpu = run_on_cpu or run_on_cpu_process

    def __call__(self, samples: Samples, rate: int) -> tuple[Samples, Samples]:
        """Separate on the GPU if there is one; retry on the CPU when the GPU fails."""
        self.fallback_used = False
        with tempfile.TemporaryDirectory(prefix="redline-sep-") as work:
            source = Path(work) / "input.wav"
            sf.write(source, samples, rate, subtype="FLOAT")
            try:
                vocal_path, beat_path = self._run(source, Path(work))
            except ModuleNotFoundError as error:
                raise SeparationError(
                    f"{error.name or error}: install the models with `uv sync --extra separation`"
                ) from error
            except OSError as error:
                raise SeparationError(
                    f"model download or file access failed ({error}); "
                    "the first run needs the internet"
                ) from error
            except RuntimeError as error:
                if not _is_gpu_error(error):
                    raise SeparationError(f"separation failed: {error}") from error
                log.warning("separation on the GPU failed (%s); retrying on the CPU", error)
                self.fallback_used = True
                try:
                    vocal_path, beat_path = self._run_on_cpu(source, Path(work))
                except (subprocess.CalledProcessError, OSError) as cpu_error:
                    raise SeparationError(
                        f"separation failed on the GPU ({error}) and on the CPU ({cpu_error})"
                    ) from cpu_error
            try:
                vocal, beat = _read(vocal_path), _read(beat_path)
            except sf.LibsndfileError as error:
                raise SeparationError(f"the model wrote no readable stems: {error}") from error
            return _fit(vocal, len(samples)), _fit(beat, len(samples))


def verify_files(expected: dict[Path, str]) -> None:
    """Raise when a file's sha256 differs from the pinned one."""
    for path, digest in expected.items():
        if not path.is_file():
            raise SeparationError(
                f"{path} was not downloaded; the pinned files may not match this library version"
            )
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != digest:
            raise SeparationError(
                f"{path} does not match its pinned sha256; delete it to download it again"
            )


def run_model(
    source: Path,
    out: Path,
    engine: Any = None,
    model_dir: Path = MODEL_DIR,
    pins: dict[str, str] = MODEL_SHA256,
    model: str = MODEL,
) -> tuple[Path, Path]:
    """Download, verify, load and run the model; return the vocal and beat file paths."""
    if engine is None:
        module = importlib.import_module("audio_separator.separator")
        engine = module.Separator(
            model_file_dir=str(model_dir),
            output_dir=str(out),
            output_format="WAV",
            normalization_threshold=1.0,
            log_level=logging.WARNING,
        )
    if "cuda" not in str(getattr(engine, "torch_device", "cuda")):
        log.warning("no CUDA device found; separating on the CPU, expect several minutes")
    engine.download_model_files(model)
    verify_files({model_dir / name: digest for name, digest in pins.items()})
    engine.load_model(model_filename=model)
    names = {"Vocals": "vocal", "Other": "beat", "Instrumental": "beat"}
    written = [out / Path(name).name for name in engine.separate(str(source), names)]
    return out / "vocal.wav", next(p for p in written if p.stem == "beat")


def python_command() -> list[str]:
    """This interpreter with the current folder kept off the import path (-P)."""
    return [sys.executable, "-P"]


def run_on_cpu_process(source: Path, out: Path) -> tuple[Path, Path]:
    """Run the model in a child process that cannot see the GPU."""
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    subprocess.run(
        [*python_command(), "-m", "redline.measure.separate", str(source), str(out)],
        env=env,
        check=True,
    )
    return out / "vocal.wav", out / "beat.wav"


if __name__ == "__main__":
    run_model(Path(sys.argv[1]), Path(sys.argv[2]))
