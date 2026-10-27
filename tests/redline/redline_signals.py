"""Synthetic signals for the redline tests; no real audio anywhere."""

from pathlib import Path

import numpy as np
import soundfile as sf


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
