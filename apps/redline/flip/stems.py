"""Named stems of a type beat (drums, bass, ...; kick, snare, ...) with a content cache."""

import hashlib
import importlib
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt
import soundfile as sf
from scipy.signal import resample_poly

from redline.measure.fingerprint import fingerprint, library_versions, module_sources
from redline.measure.separate import (
    HEADROOM_PEAK,
    MODEL_DIR,
    SeparationError,
    python_command,
    verify_files,
)

Samples = npt.NDArray[np.float32]
NamedRunner = Callable[[Path, Path], dict[str, Path]]
log = logging.getLogger(__name__)

FOUR_STEMS = ("drums", "bass", "other", "vocals")
DRUM_STEMS = ("kick", "snare", "toms", "hh", "ride", "crash")
FOUR_STEMS_MODEL = "htdemucs_ft.yaml"
DRUMSEP_MODEL = "MDX23C-DrumSep-aufr33-jarredou.ckpt"
FOUR_STEMS_SHA256 = {
    "htdemucs_ft.yaml": "69470b8c1bbd674437b51bc9fb491327a10ab0396b702c93389b9cf750016346",
    "04573f0d-f3cf25b2.th": "f3cf25b222c4eed7cd49dd8b2c9597d50c18bd154090f7b919cfa5f93cf22c49",
    "92cfc3b6-ef3bcb9c.th": "ef3bcb9c8b40d14ae5d51b6db2587339cc12c6b77c0be151ce6d69002e087bf2",
    "d12395a8-e57c48e6.th": "e57c48e6b0e38af4f7118d7bd08c49f0a0c0edf7d09143bdd902ea0d237303e6",
    "f7e0c4bc-ba3fe64a.th": "ba3fe64ae8ef66ac9a4857222ce48efbdc5eb3ad375cb79dd13debee5aaa4066",
}
DRUMSEP_SHA256 = {
    "MDX23C-DrumSep-aufr33-jarredou.ckpt": (
        "d2a4aa53eb584d21eead358a4e66d1882ad182911be018f052b5da73be9096d0"
    ),
    "config_drumsep_mdx23c.yaml": (
        "17d1649a227f841165bdb4c11a42082898192a1ea3ceab7e7e0b9293d6589dd6"
    ),
}
MODELS = {
    "four": (FOUR_STEMS_MODEL, FOUR_STEMS, FOUR_STEMS_SHA256),
    "drumsep": (DRUMSEP_MODEL, DRUM_STEMS, DRUMSEP_SHA256),
}
FLIP_RATE = 48000


class NamedSeparator(Protocol):
    """Splits a stereo signal into named stems, each shaped like the input."""

    id: str
    names: tuple[str, ...]

    def __call__(self, samples: Samples, rate: int) -> dict[str, Samples]:
        """Return one array per name in `names`."""
        ...


def stems_code() -> str:
    """Fingerprint of the stem code and the separation libraries, part of every cache key."""
    return fingerprint(
        module_sources(["flip.stems", "measure.separate"]),
        library_versions(["torch", "audio-separator", "demucs"]),
    )


def cache_key(samples: Samples, rate: int, separator_id: str, code: str | None = None) -> str:
    """Hash of the samples, the rate, the separator and the stem code."""
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(samples, dtype=np.float32).tobytes())
    digest.update(f"|{samples.shape}|{rate}|{separator_id}|{code or stems_code()}".encode())
    return digest.hexdigest()


def separate_named(
    samples: Samples, rate: int, separator: NamedSeparator, cache_dir: Path
) -> dict[str, Samples]:
    """Return the named stems from the cache, or run the separator and cache them atomically."""
    folder = cache_dir / "named-stems" / cache_key(samples, rate, separator.id)
    cached = _cached(folder, separator.names, samples.shape)
    if cached is not None:
        log.info("stems from cache: %s", folder.name[:12])
        return cached
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    scale = HEADROOM_PEAK / peak if peak > HEADROOM_PEAK else 1.0
    found = separator(samples * np.float32(scale), rate)
    missing = [name for name in separator.names if name not in found]
    if missing:
        raise SeparationError(f"the separator returned no {', '.join(missing)} stem")
    stems: dict[str, Samples] = {}
    for name in separator.names:
        if found[name].shape != samples.shape:
            raise ValueError(
                f"stem {name} has shape {found[name].shape}, the input {samples.shape}"
            )
        stems[name] = (found[name] / scale).astype(np.float32)
    _store(folder, stems, rate, separator.names)
    return stems


def _cached(
    folder: Path, names: tuple[str, ...], shape: tuple[int, ...]
) -> dict[str, Samples] | None:
    if not folder.is_dir():
        return None
    try:
        if not _complete(folder, names):
            raise FileNotFoundError(folder)
        stems = {name: _read(folder / f"{name}.wav") for name in names}
    except (OSError, sf.LibsndfileError) as error:
        log.warning("cached stems unreadable (%s); separating again", error)
    else:
        if all(stem.shape == shape for stem in stems.values()):
            return stems
        log.warning("cached stems differ in shape; separating again")
    shutil.rmtree(folder, ignore_errors=True)
    return None


def _store(folder: Path, stems: Mapping[str, Samples], rate: int, names: tuple[str, ...]) -> None:
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tmp-", dir=folder.parent))
    try:
        for name, stem in stems.items():
            sf.write(staging / f"{name}.wav", stem, rate, subtype="FLOAT")
        staging.rename(folder)
    except OSError:
        # another run may have finished the same entry first
        if not _complete(folder, names):
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def stems_at(
    stems: Mapping[str, Samples], rate: int, target: int = FLIP_RATE
) -> dict[str, Samples]:
    """Every stem resampled once from `rate` to `target`, as float32."""
    if rate == target:
        return {name: np.asarray(stem, dtype=np.float32) for name, stem in stems.items()}
    divisor = math.gcd(target, rate)
    return {
        name: resample_poly(stem, target // divisor, rate // divisor, axis=0).astype(np.float32)
        for name, stem in stems.items()
    }


def _complete(folder: Path, names: tuple[str, ...]) -> bool:
    return all((folder / f"{name}.wav").is_file() for name in names)


def _read(path: Path) -> Samples:
    data, _ = sf.read(path, always_2d=True, dtype="float32")
    return np.asarray(data, dtype=np.float32)


def _fit(stem: Samples, length: int) -> Samples:
    if len(stem) >= length:
        return stem[:length]
    return np.pad(stem, ((0, length - len(stem)), (0, 0)))


def _library_version() -> str:
    try:
        return metadata.version("audio-separator")
    except metadata.PackageNotFoundError:
        return "missing"


def _is_gpu_error(error: RuntimeError) -> bool:
    text = str(error).lower()
    return "cuda" in text or "cudnn" in text or "out of memory" in text


def run_named_model(
    source: Path,
    out: Path,
    kind: str,
    engine: Any = None,
    model_dir: Path = MODEL_DIR,
    pins: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    """Download, verify, load and run a model; return its stem file paths by name."""
    model, names, known = MODELS[kind]
    pins = known if pins is None else pins
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
    written = {Path(name).name for name in engine.separate(str(source), {n: n for n in names})}
    return {name: out / f"{name}.wav" for name in names if f"{name}.wav" in written}


def run_named_on_cpu_process(kind: str) -> NamedRunner:
    """A runner that runs the model in a child process that cannot see the GPU."""

    def run(source: Path, out: Path) -> dict[str, Path]:
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
        subprocess.run(
            [*python_command(), "-m", "redline.flip.stems", kind, str(source), str(out)],
            env=env,
            check=True,
        )
        return {name: out / f"{name}.wav" for name in MODELS[kind][1]}

    return run


class _ModelSeparator:
    """A pinned audio-separator model; falls back to the CPU when the GPU fails."""

    kind = ""

    def __init__(self, run: NamedRunner | None = None, run_on_cpu: NamedRunner | None = None):
        """Take the model runners; the defaults run the real model in and out of process."""
        model, names, _ = MODELS[self.kind]
        self.names = names
        self.id = f"audio-separator {_library_version()}:{model}:normalization=1.0"
        self.fallback_used = False
        self._run = run or (lambda source, out: run_named_model(source, out, self.kind))
        self._run_on_cpu = run_on_cpu or run_named_on_cpu_process(self.kind)

    def __call__(self, samples: Samples, rate: int) -> dict[str, Samples]:
        """Separate on the GPU if there is one; retry on the CPU when the GPU fails."""
        self.fallback_used = False
        with tempfile.TemporaryDirectory(prefix="redline-sep-") as work:
            source = Path(work) / "input.wav"
            sf.write(source, samples, rate, subtype="FLOAT")
            try:
                paths = self._run(source, Path(work))
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
                    paths = self._run_on_cpu(source, Path(work))
                except (subprocess.CalledProcessError, OSError) as cpu_error:
                    raise SeparationError(
                        f"separation failed on the GPU ({error}) and on the CPU ({cpu_error})"
                    ) from cpu_error
            try:
                return {name: _fit(_read(paths[name]), len(samples)) for name in self.names}
            except (sf.LibsndfileError, KeyError) as error:
                raise SeparationError(f"the model wrote no readable stems: {error}") from error


class FourStems(_ModelSeparator):
    """htdemucs_ft: drums, bass, other, vocals."""

    kind = "four"


class DrumSep(_ModelSeparator):
    """MDX23C DrumSep: kick, snare, toms, hh, ride, crash; feed it the drums stem only."""

    kind = "drumsep"


if __name__ == "__main__":
    run_named_model(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[1])
