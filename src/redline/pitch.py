"""Vocal pitch quantisation (the hard-tune signature), tempo candidates and key."""

import math
from dataclasses import dataclass

import librosa
import numpy as np
import numpy.typing as npt
from scipy.signal import resample_poly

PITCH_RATE = 22050
FRAME = 2048
HOP = 512
TEMPO_HOP = 128
FMIN_HZ = 65.0
FMAX_HZ = 1000.0
MIN_VOICED_PROBABILITY = 0.5
ENERGY_GATE_DB = -30.0
MIN_VOICED_FRAMES = 200
MIN_STABLE_FRAMES = 50
STABLE_STEP_CENTS = 5.0
QUANTISED_CENTS = 10.0
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

Samples = npt.NDArray[np.floating]


@dataclass(frozen=True)
class Pitch:
    """How tightly a vocal sits on a semitone grid; None when too few voiced frames."""

    voiced_frames: int
    tuning_cents: float | None
    tuning_assumed: bool
    median_deviation_cents: float | None
    share_within_10_cents: float | None
    stable_share: float | None


def _mono_at_pitch_rate(samples: Samples, rate: int) -> npt.NDArray[np.float64]:
    mono = np.asarray(samples, dtype=np.float64).mean(axis=1)
    divisor = math.gcd(PITCH_RATE, rate)
    return np.asarray(resample_poly(mono, PITCH_RATE // divisor, rate // divisor))


def _voiced_cents(mono: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Cents above A440 per frame; NaN where unvoiced, unsure or too quiet.

    Voicing comes from pyin's per-frame probability, not its Viterbi flag: on processed rap
    vocals the HMM marks whole songs unvoiced. Frequency comes from YIN, which interpolates
    between lags instead of snapping to pyin's grid; its octave errors vanish modulo 100 cents.
    """
    _, _, probability = librosa.pyin(
        mono, fmin=FMIN_HZ, fmax=FMAX_HZ, sr=PITCH_RATE, frame_length=FRAME, hop_length=HOP
    )
    f0 = librosa.yin(
        mono, fmin=FMIN_HZ, fmax=FMAX_HZ, sr=PITCH_RATE, frame_length=FRAME, hop_length=HOP
    )
    rms = librosa.feature.rms(y=mono, frame_length=FRAME, hop_length=HOP)[0][: len(f0)]
    loud = rms >= np.percentile(rms, 95) * 10 ** (ENERGY_GATE_DB / 20) if rms.any() else rms > 0
    keep = (probability >= MIN_VOICED_PROBABILITY) & loud & np.isfinite(f0)
    with np.errstate(invalid="ignore", divide="ignore"):
        cents = 1200 * np.log2(f0 / 440.0)
    return np.where(keep, cents, np.nan)


def _stable(cents: npt.NDArray[np.float64]) -> npt.NDArray[np.bool_]:
    """Voiced frames whose pitch moves less than STABLE_STEP_CENTS to both neighbours."""
    step = np.abs(np.diff(cents))
    before = np.concatenate([[np.nan], step])
    after = np.concatenate([step, [np.nan]])
    with np.errstate(invalid="ignore"):
        return (before < STABLE_STEP_CENTS) & (after < STABLE_STEP_CENTS)


def measure_pitch(samples: Samples, rate: int) -> Pitch:
    """Tuning offset from stable frames, then deviation of every voiced frame from that grid."""
    cents = _voiced_cents(_mono_at_pitch_rate(samples, rate))
    voiced = cents[np.isfinite(cents)]
    if voiced.size < MIN_VOICED_FRAMES:
        return Pitch(int(voiced.size), None, False, None, None, None)
    stable = _stable(cents)
    stable_cents = cents[stable]
    assumed = stable_cents.size < MIN_STABLE_FRAMES
    if assumed:
        tuning = 0.0
    else:
        freqs = 440.0 * 2 ** (stable_cents / 1200)
        tuning = 100 * float(librosa.pitch_tuning(freqs, resolution=0.01))
    deviation = np.abs((voiced - tuning + 50) % 100 - 50)
    return Pitch(
        voiced_frames=int(voiced.size),
        tuning_cents=tuning,
        tuning_assumed=assumed,
        median_deviation_cents=float(np.median(deviation)),
        share_within_10_cents=float((deviation <= QUANTISED_CENTS).mean()),
        stable_share=float(stable.sum() / voiced.size),
    )


def tempo_candidates(samples: Samples, rate: int) -> tuple[float, ...]:
    """Tempo estimate with its half and double, the usual octave confusions."""
    mono = _mono_at_pitch_rate(samples, rate)
    if not mono.any():
        return ()
    # A 512-sample hop quantises the beat lag to 23 ms (3.7 BPM steps near 140); 128 cuts that 4x.
    onsets = librosa.onset.onset_strength(y=mono, sr=PITCH_RATE, hop_length=TEMPO_HOP)
    bpm = float(
        librosa.feature.tempo(onset_envelope=onsets, sr=PITCH_RATE, hop_length=TEMPO_HOP)[0]
    )
    return tuple(sorted({round(bpm / 2, 1), round(bpm, 1), round(bpm * 2, 1)}))


def estimate_key(samples: Samples, rate: int) -> str | None:
    """Krumhansl-Kessler key profiles against the mean chroma; unreliable on real mixes."""
    mono = _mono_at_pitch_rate(samples, rate)
    if not mono.any():
        return None
    chroma = librosa.feature.chroma_cqt(y=mono, sr=PITCH_RATE).mean(axis=1)
    best, name = -math.inf, None
    for mode, profile in (("major", KK_MAJOR), ("minor", KK_MINOR)):
        for tonic in range(12):
            score = float(np.corrcoef(chroma, np.roll(profile, tonic))[0, 1])
            if score > best:
                best, name = score, f"{NOTES[tonic]} {mode}"
    return name
