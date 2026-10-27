import math

import numpy as np
import pytest
from redline_signals import tone

from redline.loudness import (
    MIN_SECONDS,
    gated_crest_db,
    integrated_lufs,
    loudness_range,
    measure_loudness,
    short_term_spread,
)

RATE = 48000


def dbfs(level_db: float) -> float:
    return 10 ** (level_db / 20)


def segments(*parts: tuple[float, float | None]) -> np.ndarray:
    """Concatenate 1 kHz stereo segments of (seconds, dBFS); None is digital silence."""
    out = []
    for seconds, level in parts:
        if level is None:
            out.append(np.zeros((int(seconds * RATE), 2), dtype=np.float32))
        else:
            out.append(tone(1000, seconds, rate=RATE, amp=dbfs(level)))
    return np.concatenate(out)


@pytest.mark.parametrize("level", [-23.0, -33.0])
def test_integrated_loudness_matches_ebu_tech_3341(level):
    # EBU Tech 3341 cases 1 and 2: stereo 1 kHz sine at L dBFS reads L LUFS
    assert integrated_lufs(segments((20, level)), RATE) == pytest.approx(level, abs=0.1)


def test_loudness_range_matches_ebu_tech_3342_case_1():
    # EBU Tech 3342 case 1: 20 s at -20 dBFS then 20 s at -30 dBFS, LRA 10 ±1 LU
    assert loudness_range(segments((20, -20), (20, -30)), RATE) == pytest.approx(10, abs=1)


def test_steady_sine_has_almost_no_range():
    assert loudness_range(segments((20, -20)), RATE) < 1


def test_too_short_input_is_refused():
    with pytest.raises(ValueError, match="too short"):
        measure_loudness(segments((MIN_SECONDS - 0.01, -20)), RATE)


def test_exactly_minimum_length_is_accepted():
    result = measure_loudness(segments((MIN_SECONDS, -20)), RATE)
    assert result.integrated_lufs == pytest.approx(-20, abs=0.2)


def test_short_term_spread_of_steady_sine_is_zero():
    assert short_term_spread(segments((10, -20)), RATE) == pytest.approx(0, abs=0.1)


def test_short_term_spread_of_two_levels_is_their_distance():
    # 2 s segments: more than a quarter of the 400 ms windows sit wholly in each level,
    # so the quartiles fall on -30 and -20 and the IQR is 10 LU.
    signal = segments(*[(2, -20), (2, -30)] * 6)
    assert short_term_spread(signal, RATE) == pytest.approx(10, abs=0.5)


def test_short_term_spread_ignores_digital_silence():
    signal = segments(*[(5, -20), (5, None)] * 3)
    assert short_term_spread(signal, RATE) == pytest.approx(0, abs=0.1)


def test_crest_of_sine_and_square():
    assert gated_crest_db(segments((5, -6)), RATE) == pytest.approx(10 * math.log10(2), abs=0.1)
    square = np.sign(segments((5, -6))) * dbfs(-6)
    assert gated_crest_db(square.astype(np.float32), RATE) == pytest.approx(0.0, abs=0.1)


def test_crest_ignores_digital_silence():
    signal = segments(*[(1, -6), (1, None)] * 5)
    assert gated_crest_db(signal, RATE) == pytest.approx(10 * math.log10(2), abs=0.1)


def test_silence_gives_no_values():
    result = measure_loudness(segments((5, None)), RATE)
    assert result.integrated_lufs == -math.inf
    assert result.lra_lu is None
    assert result.short_term_spread_lu is None
    assert result.crest_db is None


def test_short_term_spread_gates_quiet_bleed():
    # -65 dBFS sections sit 45 LU under the -20 dBFS sections: below the -20 LU relative gate
    signal = segments(*[(5, -20), (5, -65)] * 3)
    assert short_term_spread(signal, RATE) == pytest.approx(0, abs=0.1)
