import numpy as np
import pytest
from redline.vocal.fx import phrase_ends, reverb, stutter, throws
from redline_signals import tone

RATE = 44100


def bursts(on_s, off_s, count, freq=300.0):
    """Tone bursts with silence between them: phrases and pauses."""
    parts = []
    for _ in range(count):
        parts += [tone(freq, on_s, amp=0.5), np.zeros((int(off_s * RATE), 2), dtype=np.float32)]
    return np.concatenate(parts).astype(np.float32)


def rms_db(x):
    return 10 * np.log10(np.mean(np.square(x, dtype=np.float64)) + 1e-20)


def test_phrase_ends_sit_where_the_voice_stops():
    x = bursts(1.0, 0.6, 3)
    ends = phrase_ends(x, RATE)
    expected = [int(1.0 * RATE), int(2.6 * RATE), int(4.2 * RATE)]
    assert len(ends) == 3
    for got, want in zip(ends, expected, strict=True):
        assert abs(got - want) <= 0.03 * RATE


def test_a_breath_is_not_a_phrase_end():
    x = bursts(1.0, 0.1, 3)
    assert phrase_ends(x, RATE) == [
        pytest.approx(int(3.3 * RATE) - int(0.1 * RATE), abs=0.03 * RATE)
    ]


def test_silence_has_no_phrase_ends():
    assert phrase_ends(np.zeros((RATE * 2, 2), dtype=np.float32), RATE) == []


def test_a_throw_echoes_the_phrase_end_at_the_delay_time():
    x = bursts(1.0, 1.5, 1)
    y = throws(x, RATE, level_db=-6.0, time_ms=400.0, feedback=0.0, tone_hz=12000.0)
    assert y.shape == x.shape
    end = int(1.0 * RATE)
    echo = y[end + int(0.4 * RATE) - int(0.3 * RATE) + 500 : end + int(0.4 * RATE) - 500]
    before = y[end + 2000 : end + int(0.1 * RATE)]
    # the first echo sits on one side, at the level asked, against the same side of the phrase
    tail = x[end - int(0.3 * RATE) : end]
    assert rms_db(echo[:, 0]) - rms_db(tail[:, 0]) == pytest.approx(-6.0, abs=1.5)
    assert rms_db(before) < rms_db(echo) - 30
    assert np.array_equal(y[:end], x[:end])


def test_echoes_alternate_sides_and_fade_with_feedback():
    x = bursts(1.0, 2.0, 1)
    y = throws(x, RATE, level_db=0.0, time_ms=300.0, feedback=0.5, tone_hz=12000.0)
    end = int(1.0 * RATE)
    first = y[end : end + int(0.3 * RATE)] - x[end : end + int(0.3 * RATE)]
    second = y[end + int(0.3 * RATE) : end + int(0.6 * RATE)]
    assert rms_db(first[:, 0]) > rms_db(first[:, 1]) + 10
    assert rms_db(second[:, 1]) > rms_db(second[:, 0]) + 10
    assert rms_db(second) == pytest.approx(rms_db(first) - 6.0, abs=1.5)


def test_a_reverb_tail_falls_60_db_in_its_decay_time_after_its_predelay():
    x = np.zeros((RATE * 5, 2), dtype=np.float32)
    x[RATE // 2] = 1.0
    y = reverb(x, RATE, mix_db=0.0, decay_s=1.5, predelay_ms=50.0, tone_hz=12000.0)
    wet = y - x
    start = RATE // 2
    assert np.abs(wet[start : start + int(0.045 * RATE)]).max() < 1e-6
    early = rms_db(wet[start + int(0.1 * RATE) : start + int(0.2 * RATE)])
    late = rms_db(wet[start + int(1.55 * RATE) : start + int(1.65 * RATE)])
    assert early - late == pytest.approx(60.0 * 1.45 / 1.5, abs=8.0)
    assert np.array_equal(y, reverb(x, RATE, 0.0, 1.5, 50.0, 12000.0))


def test_a_stutter_repeats_the_last_slice_of_a_phrase_into_the_gap():
    x = bursts(1.0, 1.0, 1, freq=220.0)
    y = stutter(x, RATE, slice_ms=100.0, repeats=3)
    end = int(1.0 * RATE)
    piece = int(0.1 * RATE)
    last = x[end - piece : end]
    for k in range(3):
        copy = y[end + k * piece : end + (k + 1) * piece]
        assert np.corrcoef(copy[:, 0], last[:, 0])[0, 1] > 0.95
    assert np.abs(y[end + 3 * piece + 200 :]).max() < 1e-6
    assert np.array_equal(y[:end], x[:end])


def test_a_stutter_never_spills_into_the_next_phrase():
    x = bursts(1.0, 0.25, 2, freq=220.0)
    y = stutter(x, RATE, slice_ms=100.0, repeats=8)
    second, second_end = int(1.25 * RATE), int(2.25 * RATE)
    assert np.array_equal(y[second:second_end], x[second:second_end])
    # two slices fit the 250 ms pause, the third would reach the next phrase
    assert np.abs(y[int(1.0 * RATE) : int(1.2 * RATE)]).max() > 0.1
