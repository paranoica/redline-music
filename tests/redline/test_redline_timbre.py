import numpy as np
import pytest
from redline_signals import make_rng, tone

from redline.timbre import bitcrush, double, formant_shift, hard_tune, octave_layer, pitch_shift

RATE = 44100


def peak_freq(x, rate=RATE):
    spec = np.abs(np.fft.rfft(x[:, 0] * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spec)]


def test_bitcrush_quantizes_and_holds():
    x = tone(440, 1, amp=0.8)
    y = bitcrush(x, RATE, bits=4, hold=4)
    assert y.shape == x.shape
    # 4 bits: 2^4 steps across -1 … 1
    assert len(np.unique(np.round(y[:, 0], 6))) <= 2**4 + 1
    blocks = y[: len(y) // 4 * 4, 0].reshape(-1, 4)
    assert np.all(blocks == blocks[:, :1])


def test_bitcrush_at_full_resolution_changes_little():
    x = tone(440, 1, amp=0.8)
    y = bitcrush(x, RATE, bits=24, hold=1)
    assert np.max(np.abs(y - x)) < 1e-6


@pytest.mark.parametrize(("semitones", "expected"), [(-12, 220.0), (2, 440 * 2 ** (2 / 12))])
def test_pitch_shift_moves_the_pitch_and_keeps_the_length(semitones, expected):
    x = tone(440, 2, amp=0.5)
    y = pitch_shift(x, RATE, semitones)
    assert y.shape == x.shape
    assert peak_freq(y) == pytest.approx(expected, abs=3.0)


@pytest.mark.parametrize("semitones", [12.0, -12.0])
def test_pitch_shift_keeps_the_level_of_a_noisy_voice(semitones):
    # the phase vocoder smears the phases of a noisy signal and the overlap-add loses level
    x = (make_rng().standard_normal((RATE * 3, 2)) * 0.1).astype(np.float32)
    y = pitch_shift(x, RATE, semitones)
    assert 20 * np.log10(np.std(y) / np.std(x)) == pytest.approx(0.0, abs=0.5)


def test_pitch_shift_of_silence_is_silence():
    y = pitch_shift(np.zeros((RATE, 2), dtype=np.float32), RATE, 12.0)
    assert np.all(y == 0)


@pytest.mark.parametrize(("octaves", "layer_hz"), [(-1, 220), (1, 880)])
def test_octave_layer_adds_the_octave_under_the_voice(octaves, layer_hz):
    x = tone(440, 2, amp=0.5)
    y = octave_layer(x, RATE, octaves, level_db=-6.0)
    spec = np.abs(np.fft.rfft(y[:, 0] * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), 1 / RATE)

    def at(freq):
        return spec[np.abs(freqs - freq) <= 3].max()

    assert 20 * np.log10(at(layer_hz) / at(440)) == pytest.approx(-6.0, abs=1.5)


def test_double_widens_a_centred_voice_and_keeps_it_on_top():
    rng = make_rng()
    n = RATE * 3
    envelope = 0.65 + 0.35 * np.cos(2 * np.pi * 3 * np.arange(n) / RATE)
    mono = (rng.standard_normal(n) * envelope * 0.1).astype(np.float32)
    x = np.column_stack([mono, mono])
    y = double(x, RATE)
    side = (y[:, 0] - y[:, 1]) / 2
    mid = (y[:, 0] + y[:, 1]) / 2
    assert 10 * np.log10(np.sum(side**2) / np.sum(mid**2)) > -20
    assert np.corrcoef(mid, mono)[0, 1] > 0.7


def sung(f0_hz, seconds=6.0, vibrato_cents=0.0, rate=RATE, formants=(700.0, 1200.0)):
    """A voiced vowel: a pulse train at f0 through two resonators, the same on both sides."""
    from scipy.signal import lfilter

    t = np.arange(int(seconds * rate)) / rate
    f0 = f0_hz * 2 ** (vibrato_cents / 1200 * np.sin(2 * np.pi * 5.5 * t))
    phase = np.cumsum(f0) / rate
    pulses = np.diff(np.floor(phase), prepend=0.0)
    x = pulses.astype(np.float64)
    for f in formants:
        r = np.exp(-np.pi * 80 / rate)
        x = lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / rate), r * r], x)
    x = 0.3 * x / np.abs(x).max()
    return np.column_stack([x, x]).astype(np.float32)


def mid_sound(x, rate=RATE):
    import parselmouth

    return parselmouth.Sound(x.mean(axis=1).astype(np.float64), sampling_frequency=rate)


def formants(x):
    """Median F1 and F2 over the steady part (Burg analysis)."""
    track = mid_sound(x).to_formant_burg(0.01, 5, 5000, 0.025, 50)
    times = np.arange(0.5, len(x) / RATE - 0.5, 0.05)
    return [float(np.nanmedian([track.get_value_at_time(k, t) for t in times])) for k in (1, 2)]


def cents_from_220(x):
    f0 = mid_sound(x).to_pitch(0.01, 75, 600).selected_array["frequency"]
    return 1200 * np.log2(f0[f0 > 0] / 220)


def test_a_formant_shift_moves_the_formants_and_keeps_the_pitch():
    x = sung(120)
    y = formant_shift(x, RATE, 1.2)
    assert y.shape == x.shape and np.isfinite(y).all()
    for b, a in zip(formants(x), formants(y), strict=True):
        assert a / b == pytest.approx(1.2, rel=0.05)
    f0 = mid_sound(y).to_pitch(0.01, 75, 600).selected_array["frequency"]
    assert np.median(f0[f0 > 0]) == pytest.approx(120, rel=0.02)


def test_a_formant_ratio_of_one_moves_nothing():
    x = sung(120)
    for b, a in zip(formants(x), formants(formant_shift(x, RATE, 1.0)), strict=True):
        assert a == pytest.approx(b, rel=0.02)


def test_hard_tune_pulls_a_sharp_wobbly_voice_onto_the_notes():
    from redline.pitch import measure_pitch

    # 20 cents sharp, wobbling 25 either way: never past half a semitone, so one note
    x = sung(220 * 2 ** (20 / 1200), vibrato_cents=25)
    assert measure_pitch(x, RATE).share_within_10_cents < 0.4
    y = hard_tune(x, RATE, tuning_cents=0.0)
    assert y.shape == x.shape
    cents = cents_from_220(y)
    assert abs(np.median(cents)) <= 5
    assert np.mean(np.abs(cents) <= 10) > 0.85


def test_hard_tune_keeps_a_voice_on_its_own_tuning():
    x = sung(220 * 2 ** (20 / 1200), vibrato_cents=25)
    assert abs(np.median(cents_from_220(hard_tune(x, RATE, tuning_cents=30.0))) - 30) <= 5


def test_hard_tune_holds_with_808_bleed_under_the_voice():
    x = sung(220 * 2 ** (20 / 1200), vibrato_cents=25)
    bleed = tone(50, 6, amp=0.03) + 0.003 * make_rng().standard_normal(x.shape)
    cents = cents_from_220(hard_tune((x + bleed).astype(np.float32), RATE, tuning_cents=0.0))
    assert abs(np.median(cents)) <= 5 and np.mean(np.abs(cents) <= 10) > 0.8


def test_praat_effects_keep_the_side_and_silence():
    x = sung(150)
    side = 0.05 * make_rng().standard_normal(len(x)).astype(np.float32)
    x[:, 0] += side
    x[:, 1] -= side
    for y in (formant_shift(x, RATE, 0.85), hard_tune(x, RATE, 0.0)):
        assert np.allclose((y[:, 0] - y[:, 1]) / 2, side, atol=1e-6)
    quiet = np.zeros((RATE * 2, 2), dtype=np.float32)
    assert not formant_shift(quiet, RATE, 1.2).any()
    assert not hard_tune(quiet, RATE, 0.0).any()


def test_a_clip_praat_cannot_take_is_a_value_error():
    # 100 samples are shorter than three periods of the lowest pitch Praat looks for
    clip = (0.1 * make_rng().standard_normal((100, 2))).astype(np.float32)
    for effect in (lambda: formant_shift(clip, RATE, 1.2), lambda: hard_tune(clip, RATE, 0.0)):
        with pytest.raises(ValueError, match="voice effect"):
            effect()
