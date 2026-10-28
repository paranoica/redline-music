import numpy as np
import pytest
from redline_signals import make_rng, tone

from redline.chain import Target
from redline.spectrum import tonal_curve
from redline.vocal_report import (
    CLOSER,
    FARTHER,
    IN_RANGE,
    OUT_OF_RANGE,
    SAME,
    against_refs,
    gap_level_db,
    write_vocal_report,
)

RATE = 44100


@pytest.mark.parametrize(
    ("value", "original", "expected"),
    [
        (5.0, 9.0, IN_RANGE),
        (4.0, 9.0, IN_RANGE),
        (6.0, 4.0, IN_RANGE),
        (7.0, 9.0, CLOSER),
        (9.5, 9.0, FARTHER),
        (3.5, 3.0, CLOSER),
        (2.0, 3.0, FARTHER),
        (9.0, 9.0, SAME),
    ],
)
def test_against_refs_words_a_variant_relative_to_the_original(value, original, expected):
    # references span 4 … 6
    assert against_refs(value, original, [4.0, 6.0, 5.0]) == expected


def test_against_refs_for_the_original_itself():
    assert against_refs(5.0, None, [4.0, 6.0]) == IN_RANGE
    assert against_refs(9.0, None, [4.0, 6.0]) == OUT_OF_RANGE


@pytest.mark.parametrize("refs", [[], [5.0], [5.0, None, float("nan")]])
def test_against_refs_needs_two_references(refs):
    assert against_refs(5.0, 4.0, refs) == "мало референсов"


def test_against_refs_without_a_value():
    assert against_refs(None, 4.0, [4.0, 6.0]) == "нет данных"


def test_gap_level_reads_pauses_against_the_loud_part():
    loud = tone(1000, 1, amp=0.5)
    pause = tone(1000, 1, amp=0.5 * 10 ** (-40 / 20))
    x = np.concatenate([loud, pause, loud, pause, loud])
    # 40 % of the blocks are pauses: the 10th percentile sits in them
    assert gap_level_db(x, RATE) == pytest.approx(-40.0, abs=0.5)


def report_text(tmp_path, native_rate):
    rng = make_rng()
    vocal = (rng.standard_normal((RATE * 5, 2)) * 0.1).astype(np.float32)
    curve = tonal_curve(vocal, RATE, 16000.0)
    refs = [
        {"vocal.tilt_db_per_oct": 0.5, "vocal.crest_db": 12.0, "vocal.kurtosis": 1.0},
        {"vocal.tilt_db_per_oct": 1.5, "vocal.crest_db": 14.0, "vocal.kurtosis": 2.0},
    ]
    path = tmp_path / "report.md"
    write_vocal_report(
        path,
        {"original": vocal, "heavy": vocal * 0.5},
        RATE,
        16000.0,
        Target(curve.centers_hz, curve.curve_db),
        refs,
        native_rate,
    )
    return path.read_text(encoding="utf-8")


def test_report_has_a_row_per_variant_and_the_reference_range(tmp_path):
    text = report_text(tmp_path, RATE)
    for row in ("| original |", "| heavy |", "| диапазон референсов |"):
        assert row in text
    assert "0.50 … 1.50" in text
    assert "лучше" not in text.replace("не «лучше»", "")
    assert "48" not in text


def test_report_notes_a_resampled_input(tmp_path):
    assert "48000 Гц" in report_text(tmp_path, 48000)
