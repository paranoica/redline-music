import math
from pathlib import Path

import numpy as np
import pytest
from redline.core.audio import Audio, Decoded
from redline.core.dynamics import waveform_kurtosis
from redline.core.loudness import gated_crest_db, integrated_lufs, short_term_spread
from redline.measure.metrics import METRICS, finite, vocal_metrics
from redline.measure.run import _measure
from redline.measure.separate import Stems
from redline.vocal.auto import measure_processed
from redline_signals import RATE, engine_stems


def measured(vocal, beat):
    mix = vocal + beat
    return _measure(
        Decoded(Path("synthetic.wav"), mix, RATE, "pcm_16"),
        Audio(mix, RATE, None, None),
        Stems(vocal, beat, RATE, True),
        16000.0,
        16000.0,
    )["values"]


def test_the_shared_metrics_are_the_four_the_search_scores():
    assert METRICS == (
        "pair.vocal_minus_beat_lu",
        "vocal.crest_db",
        "vocal.short_term_spread_lu",
        "vocal.kurtosis",
    )


def test_measure_and_the_search_give_equal_values_on_the_same_stems():
    vocal, beat = engine_stems(4.0)
    measure = measured(vocal, beat)
    search = measure_processed(vocal, beat, RATE)
    shared = vocal_metrics(vocal, beat, RATE)
    assert set(search) == set(shared) == set(METRICS)
    for metric in METRICS:
        assert search[metric] is not None
        assert measure[metric] == search[metric] == shared[metric], metric


def test_the_values_are_the_core_measures_of_the_stems():
    vocal, beat = engine_stems(4.0)
    shared = vocal_metrics(vocal, beat, RATE)
    assert shared == {
        "pair.vocal_minus_beat_lu": integrated_lufs(vocal, RATE) - integrated_lufs(beat, RATE),
        "vocal.crest_db": gated_crest_db(vocal, RATE),
        "vocal.short_term_spread_lu": short_term_spread(vocal, RATE),
        "vocal.kurtosis": waveform_kurtosis(vocal, RATE),
    }


def test_a_silent_vocal_is_missing_in_both_not_infinite():
    vocal, beat = engine_stems(4.0)
    silent = np.zeros_like(vocal)
    measure = measured(silent, beat)
    search = measure_processed(silent, beat, RATE)
    assert search["pair.vocal_minus_beat_lu"] is None
    for metric in METRICS:
        assert measure[metric] == search[metric] == vocal_metrics(silent, beat, RATE)[metric]
        assert search[metric] is None or math.isfinite(search[metric])


@pytest.mark.parametrize(
    ("value", "kept"),
    [(None, None), (math.inf, None), (-math.inf, None), (math.nan, None), (0.0, 0.0), (-3.5, -3.5)],
)
def test_finite_keeps_only_finite_numbers(value, kept):
    assert finite(value) == kept
