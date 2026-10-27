import numpy as np
import pytest
from redline_signals import make_rng, tone

from redline.dynamics import low_end_kurtosis, tail_decay, waveform_kurtosis

RATE = 44100


def stereo(mono: np.ndarray) -> np.ndarray:
    return np.column_stack([mono, mono]).astype(np.float32)


def test_kurtosis_of_known_distributions():
    # excess kurtosis: sine -1.5, Gaussian 0, Laplacian 3
    rng = make_rng()
    assert waveform_kurtosis(tone(997, 10, amp=0.3), RATE) == pytest.approx(-1.5, abs=0.05)
    gauss = stereo(rng.standard_normal(RATE * 20) * 0.1)
    assert waveform_kurtosis(gauss, RATE) == pytest.approx(0.0, abs=0.1)
    laplace = stereo(rng.laplace(size=RATE * 20) * 0.05)
    assert waveform_kurtosis(laplace, RATE) == pytest.approx(3.0, abs=0.3)


def test_saturation_lowers_kurtosis():
    laplace = make_rng().laplace(size=RATE * 20) * 0.05
    clean = waveform_kurtosis(stereo(laplace), RATE)
    driven = waveform_kurtosis(stereo(np.tanh(4 * laplace / np.abs(laplace).max())), RATE)
    assert clean is not None and driven is not None
    assert driven < clean - 1


def test_silence_has_no_kurtosis():
    assert waveform_kurtosis(np.zeros((RATE * 3, 2), dtype=np.float32), RATE) is None


def test_low_end_kurtosis_reads_the_low_band():
    # a 50 Hz sine under loud full-band Gaussian noise: below 100 Hz the sine dominates by ~20 dB
    rng = make_rng()
    mix = tone(50, 10, amp=0.3)[:, 0] + rng.standard_normal(RATE * 10) * 0.3
    assert low_end_kurtosis(stereo(mix), RATE) == pytest.approx(-1.5, abs=0.15)


def decaying_bursts(rt60: float | None, rng: np.random.Generator) -> np.ndarray:
    """Five 1 s noise bursts, each ending in an exponential tail of the given RT60
    (None: cut dead), separated by 2 s of silence."""
    parts = []
    for _ in range(5):
        parts.append(rng.standard_normal(RATE) * 0.1)
        if rt60 is not None:
            t = np.arange(int(RATE * rt60)) / RATE
            parts.append(rng.standard_normal(len(t)) * 0.1 * 10 ** (-3 * t / rt60))
        parts.append(np.zeros(RATE * 2))
    return stereo(np.concatenate(parts))


@pytest.mark.parametrize("rt60", [0.5, 1.0])
def test_tail_decay_matches_rt60(rt60):
    # an exponential tail loses 60 dB in RT60 seconds: 60 / RT60 dB/s
    rate, tails = tail_decay(decaying_bursts(rt60, make_rng()), RATE)
    assert tails == 5
    assert rate == pytest.approx(60 / rt60, rel=0.15)


def test_dead_cut_decays_faster_than_any_tail():
    rate, tails = tail_decay(decaying_bursts(None, make_rng()), RATE)
    assert tails == 5
    assert rate is not None and rate > 2 * 60 / 0.5


def test_steady_noise_has_no_tails():
    rate, tails = tail_decay(stereo(make_rng().standard_normal(RATE * 10) * 0.1), RATE)
    assert tails == 0
    assert rate is None


def test_silence_has_no_tails():
    assert tail_decay(np.zeros((RATE * 3, 2), dtype=np.float32), RATE) == (None, 0)
