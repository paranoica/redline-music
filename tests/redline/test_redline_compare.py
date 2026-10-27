import itertools

import pytest

from redline.compare import (
    CONFIRMED,
    DIFFERENT,
    FEW_REFS,
    IN_RANGE,
    NO_DATA,
    NOT_MEASURED,
    REFS_HIGHER,
    REFS_LOWER,
    REFUTED,
    UNCONFIRMED,
    disagreement,
    metric_verdict,
    row_verdicts,
)


def test_suno_far_below_the_references():
    assert metric_verdict(-16.0, [-10.0, -10.5, -6.0], floor=1.0).status == REFS_HIGHER


def test_suno_far_above_the_references():
    assert metric_verdict(20.0, [1.0, 2.0, 3.0], floor=1.0).status == REFS_LOWER


def test_suno_inside_the_reference_range():
    assert metric_verdict(4.0, [1.0, 5.0, 9.0], floor=1.0).status == IN_RANGE


def test_boundary_is_inside_and_a_quarter_past_is_outside():
    # refs [0, 4]: spread 4, margin 0.5·4 + floor 1 = 3, edge at 4 + 3 = 7
    assert metric_verdict(7.0, [0.0, 4.0], floor=1.0).status == IN_RANGE
    assert metric_verdict(7.25, [0.0, 4.0], floor=1.0).status == REFS_LOWER
    assert metric_verdict(-3.0, [0.0, 4.0], floor=1.0).status == IN_RANGE
    assert metric_verdict(-3.25, [0.0, 4.0], floor=1.0).status == REFS_HIGHER


def test_one_reference_is_not_enough():
    assert metric_verdict(-16.0, [-6.0], floor=1.0).status == FEW_REFS
    assert metric_verdict(-16.0, [-6.0, None], floor=1.0).status == FEW_REFS


def test_missing_values():
    assert metric_verdict(None, [1.0, 2.0], floor=1.0).status == NO_DATA
    assert metric_verdict(1.0, [None, None], floor=1.0).status == NO_DATA


def test_reference_order_does_not_matter():
    refs = [-10.0, -10.5, -6.0]
    statuses = {metric_verdict(-16.0, list(p), 1.0).status for p in itertools.permutations(refs)}
    assert statuses == {REFS_HIGHER}


def row(rows, label):
    return next(r for r in rows if r.label == label)


VOCAL = "Обработка вокала"


def vocal_values(stereo, crest, tilt, kurt):
    return {
        "vocal.side_mid_mid_db": stereo,
        "vocal.crest_db": crest,
        "vocal.tilt_db_per_oct": tilt,
        "vocal.kurtosis": kurt,
    }


def test_vocal_processing_confirmed_when_most_metrics_point_the_expected_way():
    # refs expected narrower, more compressed, darker, more saturated: all lower than Suno
    suno = vocal_values(-10.0, 12.0, -3.0, 3.0)
    refs = [vocal_values(-25.0, 6.0, -5.0, 0.5), vocal_values(-24.0, 6.5, -5.2, 0.6)]
    assert row(row_verdicts(suno, refs), VOCAL).status == CONFIRMED


def test_vocal_processing_refuted_when_most_metrics_point_the_other_way():
    suno = vocal_values(-25.0, 6.0, -5.0, 0.5)
    refs = [vocal_values(-10.0, 12.0, -3.0, 3.0), vocal_values(-10.5, 12.5, -3.1, 3.1)]
    assert row(row_verdicts(suno, refs), VOCAL).status == REFUTED


def test_vocal_processing_mixed_evidence_is_unconfirmed():
    # two metrics in the expected direction, one opposite, one in range
    suno = vocal_values(-10.0, 12.0, -5.0, 1.0)
    refs = [vocal_values(-25.0, 6.0, -3.0, 1.0), vocal_values(-24.0, 6.5, -3.1, 1.1)]
    assert row(row_verdicts(suno, refs), VOCAL).status == UNCONFIRMED


def test_voice_and_beat_is_bimodal():
    label = "Голос и бит"
    either_way = [{"pair.vocal_minus_beat_lu": -8.0}, {"pair.vocal_minus_beat_lu": 8.0}]
    suno = {"pair.vocal_minus_beat_lu": 0.0}
    assert row(row_verdicts(suno, either_way), label).status == CONFIRMED
    close = [{"pair.vocal_minus_beat_lu": 0.5}, {"pair.vocal_minus_beat_lu": -0.5}]
    assert row(row_verdicts(suno, close), label).status == REFUTED
    one_far = [{"pair.vocal_minus_beat_lu": 8.0}, {"pair.vocal_minus_beat_lu": 0.2}]
    assert row(row_verdicts(suno, one_far), label).status == UNCONFIRMED


def test_effects_row_reports_difference_only():
    label = "Эффекты на вокале"
    refs = [{"vocal.tail_decay_db_per_s": 80.0}, {"vocal.tail_decay_db_per_s": 85.0}]
    rows = row_verdicts({"vocal.tail_decay_db_per_s": 25.0}, refs)
    assert row(rows, label).status == DIFFERENT


def test_rows_without_metrics_are_not_measured():
    rows = row_verdicts({}, [{}, {}])
    assert row(rows, "Структура").status == NOT_MEASURED
    assert row(rows, "Лексика и образы").status == NOT_MEASURED


def test_rows_with_no_values_have_no_data():
    assert row(row_verdicts({}, [{}, {}]), "Длина трека").status == NO_DATA


def test_disagreeing_references_raise_a_warning_naming_the_outlier():
    keys = ["mix.lufs", "mix.duration_s", "mix.tilt_db_per_oct"]
    suno = dict.fromkeys(keys, 0.0)
    refs = [dict.fromkeys(keys, 1.0), dict.fromkeys(keys, 1.2), dict.fromkeys(keys, 30.0)]
    warning = disagreement(suno, refs)
    assert warning is not None
    assert warning.outlier == 2


def test_close_references_raise_no_warning():
    keys = ["mix.lufs", "mix.duration_s", "mix.tilt_db_per_oct"]
    suno = dict.fromkeys(keys, 0.0)
    refs = [dict.fromkeys(keys, 20.0), dict.fromkeys(keys, 20.5), dict.fromkeys(keys, 21.0)]
    assert disagreement(suno, refs) is None


@pytest.mark.parametrize("value", [None])
def test_missing_metric_does_not_break_rows(value):
    suno = vocal_values(value, 12.0, -3.0, 3.0)
    refs = [vocal_values(-25.0, 6.0, -5.0, 0.5), vocal_values(-24.0, 6.5, -5.2, 0.6)]
    assert row(row_verdicts(suno, refs), VOCAL).status == CONFIRMED


def test_one_metric_against_blocks_confirmation():
    # three of four in the expected direction, tilt the other way
    suno = vocal_values(-10.0, 12.0, -5.0, 3.0)
    refs = [vocal_values(-25.0, 6.0, -3.0, 0.5), vocal_values(-24.0, 6.5, -3.1, 0.6)]
    assert row(row_verdicts(suno, refs), VOCAL).status == UNCONFIRMED


@pytest.mark.parametrize(
    ("suno", "refs"),
    [(1.0, [float("-inf"), float("-inf")]), (float("inf"), [1.0, 2.0]), (1.0, [float("nan"), 2.0])],
)
def test_non_finite_values_are_no_data(suno, refs):
    assert metric_verdict(suno, refs, floor=1.0).status in (NO_DATA, FEW_REFS)


def test_disagreement_skips_non_finite_values():
    # an infinite value on one reference must not turn its distance into NaN
    keys = ["mix.lufs", "mix.duration_s", "mix.tilt_db_per_oct", "mix.correlation"]
    suno = dict.fromkeys(keys, 0.0)
    refs = [dict.fromkeys(keys, 1.0), dict.fromkeys(keys, 1.2), dict.fromkeys(keys, 30.0)]
    refs[1]["mix.correlation"] = float("inf")
    warning = disagreement(suno, refs)
    assert warning is not None
    assert warning.outlier == 2
    assert warning.of == 3
