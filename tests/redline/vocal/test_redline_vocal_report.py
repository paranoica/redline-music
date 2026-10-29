import numpy as np
import pytest
from redline.core.spectrum import tonal_curve
from redline.measure.compare import FEW_REFS, NO_DATA, metric_verdict
from redline.measure.compare import IN_RANGE as STAGE_1_IN_RANGE
from redline.vocal.chain import Target
from redline.vocal.report import (
    CLOSER,
    FARTHER,
    IN_RANGE,
    OUT_OF_RANGE,
    SAME,
    against_refs,
    gap_level_db,
    write_vocal_report,
)
from redline_signals import make_rng, tone

RATE = 44100


# references 4 and 6, floor 0.5: stage 1 calls a value in range within half the spread plus
# the floor past either end, so 2.5 … 7.5
REFS = [4.0, 6.0]
FLOOR = 0.5


@pytest.mark.parametrize(
    ("value", "original", "expected"),
    [
        (5.0, 9.0, IN_RANGE),
        (7.5, 9.0, IN_RANGE),
        (2.5, 1.0, IN_RANGE),
        (7.6, 9.0, CLOSER),
        (2.4, 1.0, CLOSER),
        (9.5, 9.0, FARTHER),
        (1.0, 2.0, FARTHER),
        (9.2, 9.0, SAME),
        (8.8, 9.0, SAME),
        (7.9, 7.0, OUT_OF_RANGE),
        (7.6, 7.4, OUT_OF_RANGE),
        (9.1, 1.0, FARTHER),
    ],
)
def test_against_refs_words_a_variant_relative_to_the_original(value, original, expected):
    assert against_refs(value, original, REFS, FLOOR) == expected


def test_against_refs_for_the_original_itself():
    assert against_refs(7.5, None, REFS, FLOOR) == IN_RANGE
    assert against_refs(7.6, None, REFS, FLOOR) == OUT_OF_RANGE


@pytest.mark.parametrize("value", [2.0, 2.5, 4.0, 7.5, 7.6, 9.0])
def test_against_refs_agrees_with_stage_1_on_the_range(value):
    stage_1 = metric_verdict(value, REFS, FLOOR).status == STAGE_1_IN_RANGE
    assert (against_refs(value, None, REFS, FLOOR) == IN_RANGE) == stage_1


@pytest.mark.parametrize("refs", [[5.0], [5.0, None, float("nan")]])
def test_against_refs_needs_two_references(refs):
    assert against_refs(5.0, 4.0, refs, FLOOR) == FEW_REFS


@pytest.mark.parametrize("refs", [[], [None, float("nan")]])
def test_against_refs_without_any_reference_value_matches_stage_1(refs):
    assert against_refs(5.0, 4.0, refs, FLOOR) == metric_verdict(5.0, refs, FLOOR).status


def test_against_refs_without_a_value():
    assert against_refs(None, 4.0, REFS, FLOOR) == NO_DATA
    # stage 1 checks the value first
    assert against_refs(None, 4.0, [], FLOOR) == NO_DATA


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
    for row in ("| original |", "| heavy |", "| диапазон референсов с запасом этапа 1 |"):
        assert row in text
    # tilt references 0.5 and 1.5, floor 0.5: the stage 1 range is -0.5 … 2.5
    assert "-0.50 … 2.50" in text
    assert "лучше" not in text.replace("не «лучше»", "")
    assert "чище" not in text
    assert "48" not in text


def test_report_notes_a_resampled_input(tmp_path):
    assert "48000 Гц" in report_text(tmp_path, 48000)
