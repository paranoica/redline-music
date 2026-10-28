"""Vocal timbre effects: doubles, octave layers, bitcrush."""

import librosa
import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.floating]

DOUBLE_DELAY_MS = (18.0, 27.0)
DOUBLE_DEPTH_MS = 2.0
DOUBLE_LFO_HZ = (0.31, 0.43)
DOUBLE_LEVEL_DB = -4.0


def bitcrush(samples: Samples, rate: int, bits: int, hold: int) -> Samples:
    """Quantize to 2^bits steps over -1 … 1 and hold every hold-th sample."""
    step = 2.0 / 2**bits
    out = np.round(samples / step) * step
    if hold > 1:
        index = (np.arange(len(out)) // hold) * hold
        out = out[index]
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def pitch_shift(samples: Samples, rate: int, semitones: float) -> Samples:
    """Shift the pitch and keep the length (phase vocoder, per channel)."""
    channels = [
        librosa.effects.pitch_shift(np.ascontiguousarray(samples[:, c]), sr=rate, n_steps=semitones)
        for c in range(samples.shape[1])
    ]
    return np.column_stack(channels)[: len(samples)].astype(np.float32)


def octave_layer(samples: Samples, rate: int, octaves: int, level_db: float) -> Samples:
    """The voice with a copy whole octaves away layered in; it stays in key with the beat."""
    layer = pitch_shift(samples, rate, 12.0 * octaves)
    return (samples + layer * np.float32(10 ** (level_db / 20))).astype(np.float32)


def _modulated_delay(mono: Samples, rate: int, delay_ms: float, lfo_hz: float) -> Samples:
    n = np.arange(len(mono))
    delay = (delay_ms + DOUBLE_DEPTH_MS * np.sin(2 * np.pi * lfo_hz * n / rate)) * rate / 1000
    return np.interp(n - delay, n, mono, left=0.0).astype(np.float32)


def double(samples: Samples, rate: int) -> Samples:
    """Two slowly wandering delayed copies of the voice, one per side, under the dry voice.

    The wandering delay detunes each copy by a few cents, as a second take would be.
    """
    mono = samples.mean(axis=1)
    level = np.float32(10 ** (DOUBLE_LEVEL_DB / 20))
    left = _modulated_delay(mono, rate, DOUBLE_DELAY_MS[0], DOUBLE_LFO_HZ[0])
    right = _modulated_delay(mono, rate, DOUBLE_DELAY_MS[1], DOUBLE_LFO_HZ[1])
    return (samples + level * np.column_stack([left, right])).astype(np.float32)
