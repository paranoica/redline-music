"""Vocal timbre effects: doubles, octave layers, bitcrush, formant shift, hard tune."""

import librosa
import numpy as np
import numpy.typing as npt
import parselmouth
from parselmouth.praat import call

Samples = npt.NDArray[np.floating]

DOUBLE_DELAY_MS = (18.0, 27.0)
DOUBLE_DEPTH_MS = 2.0
DOUBLE_LFO_HZ = (0.31, 0.43)
DOUBLE_LEVEL_DB = -4.0
PITCH_FLOOR_HZ = 75.0
PITCH_CEILING_HZ = 600.0
PITCH_STEP_S = 0.01


def bitcrush(samples: Samples, rate: int, bits: int, hold: int) -> Samples:
    """Quantize to 2^bits steps over -1 … 1 and hold every hold-th sample."""
    step = 2.0 / 2**bits
    out = np.round(samples / step) * step
    if hold > 1:
        index = (np.arange(len(out)) // hold) * hold
        out = out[index]
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def _rms(samples: Samples) -> float:
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def pitch_shift(samples: Samples, rate: int, semitones: float) -> Samples:
    """Shift the pitch and keep the length and the level (phase vocoder, per channel)."""
    channels = [
        librosa.effects.pitch_shift(np.ascontiguousarray(samples[:, c]), sr=rate, n_steps=semitones)
        for c in range(samples.shape[1])
    ]
    shifted = np.column_stack(channels)[: len(samples)]
    # the vocoder loses level on noisy material, several dB on a dirty voice
    before, after = _rms(samples), _rms(shifted)
    if after > 0:
        shifted = shifted * (before / after)
    return shifted.astype(np.float32)


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


def _on_mid(samples: Samples, rate: int, change) -> Samples:
    # the side holds the stem's width and room; only the voice in the middle is changed
    mid = samples.mean(axis=1).astype(np.float64)
    side = (samples[:, 0] - samples[:, 1]) / 2
    if not mid.any():
        return samples.copy()
    try:
        sound = change(parselmouth.Sound(mid, sampling_frequency=rate))
    except parselmouth.PraatError as error:
        raise ValueError(f"the voice effect failed: {error}") from error
    out = np.zeros(len(mid))
    values = sound.values[0][: len(mid)]
    out[: len(values)] = values
    return np.column_stack([out + side, out - side]).astype(np.float32)


def formant_shift(samples: Samples, rate: int, ratio: float) -> Samples:
    """Move the voice's formants by ratio and keep its notes: above 1 younger, below 1 bigger."""
    return _on_mid(
        samples,
        rate,
        lambda sound: call(
            sound, "Change gender", PITCH_FLOOR_HZ, PITCH_CEILING_HZ, ratio, 0.0, 1.0, 1.0
        ),
    )


def hard_tune(samples: Samples, rate: int, tuning_cents: float) -> Samples:
    """Snap every pitch point to the nearest semitone of the voice's own tuning, no glide."""
    reference = 440.0 * 2 ** (tuning_cents / 1200)

    def change(sound: parselmouth.Sound) -> parselmouth.Sound:
        manipulation = call(
            sound, "To Manipulation", PITCH_STEP_S, PITCH_FLOOR_HZ, PITCH_CEILING_HZ
        )
        tier = call(manipulation, "Extract pitch tier")
        call(tier, "Formula", f"{reference} * 2 ^ (round(12 * log2(self / {reference})) / 12)")
        call([tier, manipulation], "Replace pitch tier")
        return call(manipulation, "Get resynthesis (overlap-add)")

    return _on_mid(samples, rate, change)


def voice_octaves(samples: Samples, rate: int, octaves: int) -> Samples:
    """The whole voice whole octaves away, its formants kept: higher, not a cartoon."""
    factor = 2.0**octaves

    def change(sound: parselmouth.Sound) -> parselmouth.Sound:
        manipulation = call(
            sound, "To Manipulation", PITCH_STEP_S, PITCH_FLOOR_HZ, PITCH_CEILING_HZ
        )
        tier = call(manipulation, "Extract pitch tier")
        call(tier, "Formula", f"self * {factor}")
        call([tier, manipulation], "Replace pitch tier")
        return call(manipulation, "Get resynthesis (overlap-add)")

    return _on_mid(samples, rate, change)
