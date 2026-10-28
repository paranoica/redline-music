import numpy as np
import pytest
from redline_signals import make_rng, tone

from redline.timbre import bitcrush, double, octave_layer, pitch_shift

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


@pytest.mark.parametrize(("octaves", "layer_hz"), [(-1, 220), (1, 880)])
def test_octave_layer_adds_the_octave_under_the_voice(octaves, layer_hz):
    x = tone(440, 2, amp=0.5)
    y = octave_layer(x, RATE, octaves, level_db=-6.0)
    spec = np.abs(np.fft.rfft(y[:, 0] * np.hanning(len(y))))
    freqs = np.fft.rfftfreq(len(y), 1 / RATE)

    def at(freq):
        return spec[np.abs(freqs - freq) <= 3].max()

    assert at(440) > at(layer_hz) > 0.1 * at(440)


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
