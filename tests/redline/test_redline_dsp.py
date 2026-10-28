import numpy as np
import pytest
from redline_signals import make_rng, tone
from scipy.signal import sosfreqz

from redline.dsp import (
    EXPANDER_RANGE_DB,
    compress,
    compressor_gain_db,
    drive,
    hard_clip,
    high_shelf,
    low_shelf,
    oversampled,
    peaking,
)

RATE = 44100


def response_db(sos, freq):
    _, h = sosfreqz(sos, worN=[freq], fs=RATE)
    return 20 * np.log10(abs(h[0]))


def test_peaking_reaches_its_gain_at_the_center_only():
    sos = peaking(1000, 6.0, 1.0, RATE)
    assert response_db(sos, 1000) == pytest.approx(6.0, abs=0.1)
    # analog prototype with Q 1: 0.42 dB two octaves either side
    assert response_db(sos, 4000) < 0.5
    assert response_db(sos, 250) < 0.5


def test_low_shelf_boosts_the_lows_and_leaves_the_highs():
    sos = low_shelf(200, 6.0, RATE)
    assert response_db(sos, 20) == pytest.approx(6.0, abs=0.2)
    assert response_db(sos, 20000) == pytest.approx(0.0, abs=0.2)
    # an RBJ shelf is at half its gain at the corner
    assert response_db(sos, 200) == pytest.approx(3.0, abs=0.1)


def test_high_shelf_boosts_the_highs_and_leaves_the_lows():
    sos = high_shelf(8000, -6.0, RATE)
    assert response_db(sos, RATE / 2 - 1) == pytest.approx(-6.0, abs=0.2)
    assert response_db(sos, 800) == pytest.approx(0.0, abs=0.2)
    assert response_db(sos, 8000) == pytest.approx(-3.0, abs=0.1)


def level(dbfs):
    return 10 ** (dbfs / 20)


def steady_gain(x, **settings):
    gain = compressor_gain_db(x, RATE, **settings)
    return float(np.median(gain[len(gain) // 2 :]))


SETTINGS = {"threshold_db": -20.0, "ratio": 4.0, "attack_ms": 5.0, "release_ms": 50.0}


def test_compressor_follows_the_static_curve_above_threshold():
    x = tone(1000, 2, amp=level(-10))
    # (level - threshold) * (1 - 1/ratio) = 10 * 0.75
    assert steady_gain(x, gate_db=-60.0, **SETTINGS) == pytest.approx(-7.5, abs=0.2)


def test_compressor_leaves_signal_between_gate_and_threshold():
    x = tone(1000, 2, amp=level(-25))
    assert steady_gain(x, gate_db=-60.0, **SETTINGS) == pytest.approx(0.0, abs=0.1)


def test_expander_halves_the_level_below_the_gate():
    x = tone(1000, 2, amp=level(-50))
    # 1:2 below the gate: 10 dB under it comes out 20 dB under it
    assert steady_gain(x, gate_db=-40.0, **SETTINGS) == pytest.approx(-10.0, abs=0.5)


def test_expander_opens_at_attack_speed_when_a_phrase_starts():
    gap = tone(1000, 1, amp=level(-70))
    phrase = tone(1000, 1, amp=level(-25))
    x = np.concatenate([gap, phrase])
    gain = compressor_gain_db(x, RATE, gate_db=-40.0, **SETTINGS)
    # one-pole: five attack times leave e^-5 of the 30 dB step, about 0.2 dB
    after = len(gap) + int(5 * SETTINGS["attack_ms"] / 1000 * RATE) + int(RATE / 1000)
    assert gain[after] > -1.0
    assert gain[len(gap) - 3 * int(RATE / 1000)] == pytest.approx(-30.0, abs=1.0)


def test_expander_turns_a_gap_down_by_its_range_at_most():
    x = tone(1000, 2, amp=level(-100))
    assert steady_gain(x, gate_db=-40.0, **SETTINGS) == pytest.approx(-EXPANDER_RANGE_DB, abs=0.5)


@pytest.mark.parametrize("width", [1, 6, 60])
def test_one_click_does_not_duck_the_compressor(width):
    x = tone(400, 2, amp=level(-20))
    # RATE is not a multiple of the 44-sample block, so wider clicks cross a block edge
    x[RATE : RATE + width] = 0.99
    gain = compressor_gain_db(
        x, RATE, threshold_db=-30.0, ratio=10.0, attack_ms=2.0, release_ms=60.0, gate_db=-60.0
    )
    steady = float(np.median(gain[RATE // 2 : RATE - 1000]))
    assert gain[RATE - 1000 : RATE + 10000].min() > steady - 1.0


def test_compressor_is_stereo_linked():
    rng = make_rng()
    n = RATE * 3
    envelope = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * np.arange(n) / RATE)
    left = rng.standard_normal(n) * envelope * 0.3
    right = rng.standard_normal(n) * envelope * 0.03
    x = np.column_stack([left, right]).astype(np.float32)
    y = compress(x, RATE, gate_db=-80.0, **SETTINGS)
    before = 10 * np.log10(np.sum(x[:, 0] ** 2) / np.sum(x[:, 1] ** 2))
    after = 10 * np.log10(np.sum(y[:, 0] ** 2) / np.sum(y[:, 1] ** 2))
    assert after == pytest.approx(before, abs=0.2)
    assert np.mean(y[:, 0] ** 2) < np.mean(x[:, 0] ** 2) * 0.8


def test_attack_reaches_63_percent_after_one_attack_time():
    quiet = tone(1000, 1, amp=level(-40))
    loud = tone(1000, 1, amp=level(-10))
    x = np.concatenate([quiet, loud])
    attack_ms = 20.0
    gain = compressor_gain_db(
        x, RATE, threshold_db=-20.0, ratio=4.0, attack_ms=attack_ms, release_ms=50.0, gate_db=-80.0
    )
    step = len(quiet)
    after = step + int(attack_ms / 1000 * RATE)
    block = int(RATE / 1000)
    target = -7.5 * (1 - np.exp(-1))
    assert gain[after - block] > target > gain[after + block]


def test_drive_saturates_and_is_odd():
    x = np.array([[-1.0, 1.0], [0.0, 0.0], [0.01, -0.01]], dtype=np.float32)
    y = drive(x, 20.0)
    assert np.all(np.abs(y) <= 1.0)
    assert y[0, 1] == pytest.approx(np.tanh(10.0))
    assert y[2, 0] == pytest.approx(np.tanh(0.1), rel=1e-5)
    assert np.allclose(y[:, 0], -y[:, 1])


def test_hard_clip_cuts_at_the_level_only():
    x = np.array([[0.9, -0.9], [0.3, -0.3]], dtype=np.float32)
    y = hard_clip(x, 0.5)
    assert np.allclose(y, [[0.5, -0.5], [0.3, -0.3]])


def faded(x):
    return (x * np.hanning(len(x))[:, None]).astype(np.float32)


def test_oversampled_identity_is_transparent_and_undelayed():
    x = faded(tone(1000, 1, amp=0.5) + tone(5000, 1, amp=0.2))
    y = oversampled(lambda v: v, x, 8)
    assert y.shape == x.shape
    error = 10 * np.log10(np.sum((y - x) ** 2) / np.sum(x**2))
    assert error < -80
    lag = np.argmax(np.correlate(y[:2000, 0], x[:2000, 0], mode="full")) - 1999
    assert lag == 0


def inharmonic_db(y, f0):
    seg = y[RATE // 2 : RATE // 2 + RATE, 0]
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    freqs = np.fft.rfftfreq(len(seg), 1 / RATE)
    harmonic = np.zeros_like(freqs, dtype=bool)
    for k in range(int(RATE / 2 / f0) + 1):
        harmonic |= np.abs(freqs - k * f0) <= 50
    fundamental = spec[np.abs(freqs - f0) <= 50].sum()
    return 10 * np.log10(spec[~harmonic].sum() / fundamental)


def dirty(v):
    return hard_clip(drive(v, 18.0), 0.5)


def test_oversampling_keeps_drive_and_clip_aliasing_down():
    x = tone(3000, 2, amp=0.5)
    assert inharmonic_db(oversampled(dirty, x, 8), 3000) < -50
    # the same stages at the base rate fold their harmonics back: the measure sees it
    assert inharmonic_db(dirty(x), 3000) > -50
