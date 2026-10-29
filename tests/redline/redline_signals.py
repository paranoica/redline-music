"""Synthetic signals for the redline tests; no real audio anywhere."""

from pathlib import Path

import numpy as np
import soundfile as sf
from redline.core.spectrum import tonal_curve
from redline.engine.render import Context
from redline.vocal.chain import Target


def tone(
    freq: float, seconds: float, rate: int = 44100, amp: float = 1.0, phase: float = 0.0
) -> np.ndarray:
    """Stereo sine, shape (n, 2)."""
    t = np.arange(int(seconds * rate)) / rate
    mono = amp * np.sin(2 * np.pi * freq * t + phase)
    return np.column_stack([mono, mono]).astype(np.float32)


def lowpass_brickwall(x: np.ndarray, rate: int, cutoff: float) -> np.ndarray:
    """Zero every FFT bin above the cutoff, per channel."""
    spec = np.fft.rfft(x, axis=0)
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    spec[freqs > cutoff] = 0
    return np.fft.irfft(spec, n=len(x), axis=0).astype(np.float32)


SEED = 20261027


def make_rng() -> np.random.Generator:
    """The seeded generator every noise-based test uses."""
    return np.random.default_rng(SEED)


def write(folder: Path, name: str, data: np.ndarray, rate: int, **kwargs: str) -> Path:
    """Write a test signal to a file and return its path."""
    path = folder / name
    sf.write(path, data, rate, **kwargs)
    return path


RATE = 44100


def engine_stems(seconds=6.0):
    """A voice and a kick as two stems, for the engine and the studio."""
    rng = make_rng()
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    voice = 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    voice = voice + 0.1 * np.sin(2 * np.pi * 900 * t) + 0.001 * rng.standard_normal(n)
    kick = 0.5 * np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    vocal = np.column_stack([voice, voice]).astype(np.float32)
    beat = np.column_stack([kick, kick]).astype(np.float32)
    return vocal, beat


def engine_context(vocal):
    """What the engine measures on a track, taken from the synthetic voice."""
    curve = tonal_curve(vocal * 0.8, RATE, 16000.0)
    return Context(RATE, Target(curve.centers_hz, curve.curve_db), 16000.0, 0.0, -9.0)
