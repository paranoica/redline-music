import math
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
from redline.core.audio import Audio, Decoded
from redline.engine.render import Engine
from redline.engine.settings import SPECS, Settings
from redline.measure.run import FileResult, _measure
from redline.measure.separate import Stems
from redline.vocal.auto import (
    AXES,
    FLOOR,
    METRICS,
    AutoResult,
    auto_settings,
    distance,
    loudest_excerpt,
    measure_processed,
    reference_targets,
)
from redline_signals import RATE, engine_context, engine_stems, make_rng

PAIR = "pair.vocal_minus_beat_lu"
CREST = "vocal.crest_db"


def ref(name: str, values: dict[str, float | None]) -> FileResult:
    return FileResult(name, "pcm_16", RATE, False, values=values)


def test_one_reference_gives_its_values_and_the_floor_as_spread():
    spread = "vocal.short_term_spread_lu"
    targets = reference_targets([ref("a", {PAIR: 3.0, CREST: 9.0, spread: 4.0})])
    assert targets == {
        PAIR: (3.0, FLOOR[PAIR]),
        CREST: (9.0, FLOOR[CREST]),
        spread: (4.0, FLOOR[spread]),
    }
    assert FLOOR[spread] != FLOOR[PAIR]


def test_three_references_give_the_median_and_the_interquartile_range():
    refs = [
        ref("a", {PAIR: 0.0, CREST: 9.0}),
        ref("b", {PAIR: 2.0, CREST: 9.1}),
        ref("c", {PAIR: 10.0, CREST: 9.2}),
    ]
    targets = reference_targets(refs)
    # quartiles of 0, 2, 10 are 1 and 6
    assert targets[PAIR] == pytest.approx((2.0, 5.0))
    # identical-ish references: the range 0.1 is under the floor
    assert targets[CREST] == pytest.approx((9.1, FLOOR[CREST]))


def test_references_without_a_metric_are_left_out_of_it():
    refs = [
        ref("a", {PAIR: 1.0, CREST: None}),
        ref("b", {PAIR: 3.0}),
        ref("c", {CREST: 8.0, "other.metric": 5.0}),
    ]
    targets = reference_targets(refs)
    assert targets[PAIR][0] == pytest.approx(2.0)
    assert targets[CREST] == (8.0, FLOOR[CREST])
    assert set(targets) == {PAIR, CREST}
    assert reference_targets([]) == {}


def test_every_metric_has_a_positive_floor():
    assert set(FLOOR) == set(METRICS)
    assert all(FLOOR[m] > 0 for m in METRICS)


def test_distance_is_zero_at_the_medians_and_grows_away_from_them():
    targets = {PAIR: (3.0, 2.0), CREST: (9.0, 1.0)}
    assert distance({PAIR: 3.0, CREST: 9.0}, targets) == 0.0
    assert distance({PAIR: 5.0, CREST: 9.0}, targets) == pytest.approx(1.0)
    assert distance({PAIR: 7.0, CREST: 9.0}, targets) == pytest.approx(4.0)
    assert distance({PAIR: 1.0, CREST: 11.0}, targets) == pytest.approx(5.0)


def test_a_metric_the_render_lost_costs_three_spreads_and_one_no_reference_has_nothing():
    from redline.vocal.auto import MISSING

    targets = {PAIR: (0.0, 1.0)}
    assert MISSING == 9.0
    # a setting that makes a metric unmeasurable must not win by dropping it
    assert distance({PAIR: None, CREST: 9.0}, targets) == MISSING
    assert distance({PAIR: 0.0, CREST: 9.0}, targets) == 0.0


def test_an_empty_excerpt_is_refused():
    with pytest.raises(ValueError):
        loudest_excerpt(np.zeros((RATE, 2), np.float32), RATE, 0.0)


def test_the_vocal_level_grid_reaches_above_unity():
    from redline.vocal.auto import AXES

    assert max(AXES["vocal_db"]) >= 3


def quiet_with_loud(seconds: float, loud_from: float, loud_to: float) -> np.ndarray:
    noise = 0.01 * make_rng().standard_normal((int(seconds * RATE), 2))
    noise[int(loud_from * RATE) : int(loud_to * RATE)] *= 50
    return noise.astype(np.float32)


@pytest.mark.parametrize(("start", "end"), [(4.0, 6.0), (0.0, 2.0), (8.0, 10.0)])
def test_loudest_excerpt_finds_the_loud_stretch(start, end):
    vocal = quiet_with_loud(10.0, start, end)
    assert loudest_excerpt(vocal, RATE, 2.0) == (int(start * RATE), int(end * RATE))


def test_loudest_excerpt_of_a_short_signal_is_the_whole_signal():
    vocal = quiet_with_loud(1.5, 0.5, 1.0)
    assert loudest_excerpt(vocal, RATE, 2.0) == (0, len(vocal))
    exact = quiet_with_loud(2.0, 0.5, 1.0)
    assert loudest_excerpt(exact, RATE, 2.0) == (0, len(exact))


def test_processed_metrics_are_the_ones_the_references_were_measured_with():
    vocal, beat = engine_stems(4.0)
    mix = vocal + beat
    measured = _measure(
        Decoded(Path("synthetic.wav"), mix, RATE, "pcm_16"),
        Audio(mix, RATE, None, None),
        Stems(vocal, beat, RATE, True),
        16000.0,
        16000.0,
    )["values"]
    values = measure_processed(vocal, beat, RATE)
    assert set(values) == set(METRICS)
    for metric in METRICS:
        assert values[metric] is not None
        assert values[metric] == pytest.approx(measured[metric], rel=1e-9, abs=1e-12)


def test_a_silent_vocal_measures_as_missing_not_infinite():
    vocal, beat = engine_stems(4.0)
    values = measure_processed(np.zeros_like(vocal), beat, RATE)
    assert values[PAIR] is None
    assert all(v is None or math.isfinite(v) for v in values.values())


def test_every_grid_value_is_inside_its_settings_range():
    assert list(AXES) == ["vocal_db", "comp_threshold_db", "drive_db"]
    for key, grid in AXES.items():
        spec = SPECS[key]
        assert all(spec.low <= value <= spec.high for value in grid), key


@pytest.fixture(scope="module")
def track():
    vocal, beat = engine_stems(4.0)
    return vocal, beat, engine_context(vocal)


# beat 3 dB up puts the vocal under the beat at the default vocal_db
BASE = Settings(beat_db=3.0, octave_down_db=-12.0)
LOUDER_REFS = [
    ref("a", {PAIR: 2.5}),
    ref("b", {PAIR: 3.0}),
    ref("c", {PAIR: 3.5}),
]


@pytest.fixture(scope="module")
def result(track):
    vocal, beat, ctx = track
    return auto_settings(Engine(vocal, beat, ctx), LOUDER_REFS, BASE, seconds=2.0, passes=2)


def test_the_base_starts_with_the_vocal_under_the_references(track):
    vocal, beat, ctx = track
    rendered = Engine(vocal, beat, ctx).render(BASE)
    assert measure_processed(rendered.vocal, rendered.beat, RATE)[PAIR] < 1.0


def test_a_louder_vocal_in_the_references_raises_vocal_db(result):
    assert isinstance(result, AutoResult)
    assert result.settings.vocal_db > BASE.vocal_db
    assert result.distance_after < result.distance_before
    assert result.targets == reference_targets(LOUDER_REFS)
    assert result.values[PAIR] == pytest.approx(3.0, abs=2.0)
    assert distance(result.values, result.targets) == pytest.approx(result.distance_after)


def test_settings_outside_the_axes_stay_as_in_the_base(result):
    chosen, base = asdict(result.settings), asdict(BASE)
    for key in AXES:
        chosen.pop(key)
        base.pop(key)
    assert chosen == base


def test_chosen_values_come_from_the_grids(result):
    for key, grid in AXES.items():
        assert getattr(result.settings, key) in grid


def test_the_search_gives_the_same_result_twice(track, result):
    vocal, beat, ctx = track
    again = auto_settings(Engine(vocal, beat, ctx), LOUDER_REFS, BASE, seconds=2.0, passes=2)
    assert again == result


def test_no_passes_or_no_targets_keep_the_base(track):
    vocal, beat, ctx = track
    engine = Engine(vocal, beat, ctx)
    still = auto_settings(engine, LOUDER_REFS, BASE, seconds=2.0, passes=0)
    assert still.settings == BASE
    assert still.distance_after == still.distance_before
    blind = auto_settings(engine, [], replace(BASE, vocal_db=-9.0), seconds=2.0, passes=1)
    assert blind.settings == replace(BASE, vocal_db=-9.0)
    assert blind.targets == {} and blind.distance_after == 0.0
