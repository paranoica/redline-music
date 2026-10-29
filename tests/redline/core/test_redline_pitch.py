import math

import librosa
import numpy as np
import pytest
from redline.core.pitch import MIN_VOICED_FRAMES, estimate_key, measure_pitch, tempo_candidates
from redline_signals import make_rng

RATE = 44100


def harmonic(freq_curve: np.ndarray) -> np.ndarray:
    """A voice-like tone following an instantaneous frequency curve, stereo."""
    phase = 2 * np.pi * np.cumsum(freq_curve) / RATE
    mono = sum(np.sin(k * phase) / k for k in range(1, 7)) * 0.2
    return np.column_stack([mono, mono]).astype(np.float32)


def steady(freq: float, seconds: float = 6.0) -> np.ndarray:
    return harmonic(np.full(int(seconds * RATE), freq))


def cents(offset: float) -> float:
    return 440 * 2 ** (offset / 1200)


def test_tone_on_the_grid_is_fully_quantised():
    result = measure_pitch(steady(440.0), RATE)
    assert result.tuning_cents == pytest.approx(0, abs=2)
    assert result.median_deviation_cents < 3
    assert result.share_within_10_cents > 0.9
    assert result.stable_share > 0.9


def test_detuned_steady_tone_is_quantised_to_its_own_grid():
    result = measure_pitch(steady(cents(30)), RATE)
    assert result.tuning_cents == pytest.approx(30, abs=3)
    assert result.median_deviation_cents < 3


def test_tuning_resolves_below_ten_cents():
    # a 0.1-semitone pitch grid (pyin's default) puts +3 and +7 cents 10 apart or together
    low = measure_pitch(steady(cents(3)), RATE).tuning_cents
    high = measure_pitch(steady(cents(7)), RATE).tuning_cents
    assert high - low == pytest.approx(4, abs=1)


def test_vibrato_is_not_quantised():
    # ±50 cents at 5 Hz: time within ±10 cents of the note is (2/π)·asin(0.2) ≈ 0.13;
    # the pitch moves under 5 cents per 23 ms frame only near the turning points,
    # (2/π)·asin(5 / (2π·5·50·0.0232)) ≈ 0.09 of the time
    t = np.arange(int(8 * RATE)) / RATE
    result = measure_pitch(harmonic(440 * 2 ** (50 * np.sin(2 * np.pi * 5 * t) / 1200)), RATE)
    assert result.share_within_10_cents < 0.25
    assert result.stable_share < 0.2


def test_noise_has_too_few_voiced_frames():
    noise = make_rng().standard_normal((RATE * 6, 2)).astype(np.float32) * 0.1
    result = measure_pitch(noise, RATE)
    assert result.voiced_frames < MIN_VOICED_FRAMES
    assert result.tuning_cents is None
    assert result.median_deviation_cents is None
    assert result.share_within_10_cents is None
    assert result.stable_share is None


def test_silence_has_no_pitch():
    result = measure_pitch(np.zeros((RATE * 4, 2), dtype=np.float32), RATE)
    assert result.voiced_frames == 0
    assert result.share_within_10_cents is None


def test_click_track_tempo_is_among_the_candidates():
    bpm = 140
    x = np.zeros(RATE * 30, dtype=np.float32)
    for i in range(int(30 * bpm / 60)):
        start = int(i * 60 / bpm * RATE)
        x[start : start + 200] = np.hanning(200)
    candidates = tempo_candidates(np.column_stack([x, x]), RATE)
    assert any(abs(c - bpm) <= 2 for c in candidates)


def test_a_harmonic_minor_progression_is_a_minor():
    def chord(*notes: int) -> np.ndarray:
        # MIDI note numbers, one second each
        t = np.arange(RATE) / RATE
        mono = sum(np.sin(2 * np.pi * 440 * 2 ** ((n - 69) / 12) * t) for n in notes) * 0.2
        return mono

    am, dm, e = chord(57, 60, 64), chord(62, 65, 69), chord(64, 68, 71)
    mono = np.concatenate([am, dm, e, am] * 6)
    assert estimate_key(np.column_stack([mono, mono]).astype(np.float32), RATE) == "A minor"


def test_cents_helper_matches_definition():
    assert cents(1200) == pytest.approx(880)
    assert math.isclose(cents(0), 440)


def test_voicing_does_not_trust_the_viterbi_flag(monkeypatch):
    # on processed rap vocals pyin's HMM marked a whole song unvoiced while per-frame
    # voiced probabilities reached 0.9; the measure must follow the probabilities
    real_pyin = librosa.pyin

    def all_unvoiced(*args, **kwargs):
        f0, flag, probability = real_pyin(*args, **kwargs)
        return np.full_like(f0, np.nan), np.zeros_like(flag), probability

    monkeypatch.setattr(librosa, "pyin", all_unvoiced)
    result = measure_pitch(steady(440.0), RATE)
    assert result.voiced_frames >= MIN_VOICED_FRAMES
    assert result.median_deviation_cents < 3
