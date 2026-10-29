import itertools
import math
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest
from redline.core.audio import Audio, Decoded
from redline.engine.render import Engine, Rendered
from redline.engine.settings import SPECS, Settings
from redline.master.clip import MasterResult
from redline.measure.run import FileResult, _measure
from redline.measure.separate import Stems
from redline.vocal.auto import (
    AXES,
    FLOOR,
    METRICS,
    MISSING,
    AutoResult,
    after_master,
    auto_settings,
    distance,
    measure_processed,
    reference_targets,
    spread_windows,
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


def test_a_lost_metric_costs_more_than_one_far_off_and_one_no_reference_has_nothing():
    targets = {PAIR: (0.0, 1.0), CREST: (9.0, 1.0)}
    # nine spreads off on one metric is already further than the references ever sit
    far = distance({PAIR: 9.0, CREST: 9.0}, targets)
    assert far == pytest.approx(81.0)
    assert distance({PAIR: None, CREST: 9.0}, targets) == MISSING
    assert far < MISSING
    assert distance({PAIR: 0.0, CREST: 9.0, "other.metric": None}, targets) == 0.0


def test_the_grids_are_the_planned_ones_and_inside_their_settings_ranges():
    assert AXES == {
        "vocal_db": (-12, -9, -6, -3, 0, 3, 6),
        "comp_threshold_db": (-56, -50, -44, -38, -32, -26, -20, -14),
        "drive_db": (0, 10, 20, 30, 40),
    }
    for key, grid in AXES.items():
        assert list(grid) == sorted(grid), key
        assert SPECS[key].low <= grid[0] and grid[-1] <= SPECS[key].high, key
    defaults = Settings()
    assert all(getattr(defaults, key) in grid for key, grid in AXES.items())


def level_at(seconds: float, loud: list[tuple[float, float, float]]) -> np.ndarray:
    """Noise at -80 dB, raised to the given amplitude over each (from, to, amplitude)."""
    noise = make_rng().standard_normal((int(seconds * RATE), 2))
    scale = np.full(len(noise), 1e-4)
    for start, end, amp in loud:
        scale[int(start * RATE) : int(end * RATE)] = amp
    return (noise * scale[:, None]).astype(np.float32)


def at(seconds: float) -> int:
    return round(seconds * RATE)


def test_windows_spread_evenly_from_the_first_to_the_last_active_second():
    vocal = level_at(60.0, [(0.0, 60.0, 0.3)])
    # 4 windows of 8 s over 60 s: starts 52/3 s apart, the last ending at the end
    assert spread_windows(vocal, RATE, 4, 8.0) == [
        (0, at(8.0)),
        (at(52 / 3), at(52 / 3) + at(8.0)),
        (at(104 / 3), at(104 / 3) + at(8.0)),
        (at(52.0), at(60.0)),
    ]


def test_windows_skip_a_quiet_head_and_tail_but_keep_a_quieter_middle():
    # 40 dB down is out, 20 dB down is still singing
    vocal = level_at(60.0, [(0.0, 5.0, 0.003), (5.0, 20.0, 0.3), (20.0, 30.0, 0.03)])
    vocal[at(30.0) : at(45.0)] = 0.3 * make_rng().standard_normal((at(15.0), 2))
    windows = spread_windows(vocal, RATE, 4, 8.0)
    assert len(windows) == 4
    assert windows[0] == (at(5.0), at(13.0))
    assert windows[-1] == (at(37.0), at(45.0))
    gaps = [b[0] - a[0] for a, b in itertools.pairwise(windows)]
    assert max(gaps) - min(gaps) <= 1


def test_a_short_track_gets_fewer_windows_and_a_very_short_one_a_single_window():
    twenty = level_at(20.0, [(0.0, 20.0, 0.3)])
    assert spread_windows(twenty, RATE, 4, 8.0) == [(0, at(8.0)), (at(12.0), at(20.0))]
    six = level_at(6.0, [(0.0, 6.0, 0.3)])
    assert spread_windows(six, RATE, 4, 8.0) == [(0, len(six))]
    exact = level_at(8.0, [(0.0, 8.0, 0.3)])
    assert spread_windows(exact, RATE, 4, 8.0) == [(0, len(exact))]


def test_a_short_active_stretch_gets_one_full_window_around_it():
    vocal = level_at(10.0, [(4.0, 7.0, 0.3)])
    ((start, end),) = spread_windows(vocal, RATE, 4, 8.0)
    assert end - start == at(8.0)
    assert start <= at(4.0) and end >= at(7.0)
    assert start >= 0 and end <= len(vocal)


def test_a_silent_vocal_spreads_over_the_whole_track():
    silent = np.zeros((at(20.0), 2), np.float32)
    assert spread_windows(silent, RATE, 2, 8.0) == [(0, at(8.0)), (at(12.0), at(20.0))]


def test_a_single_window_sits_in_the_middle_of_the_active_part():
    vocal = level_at(30.0, [(0.0, 30.0, 0.3)])
    assert spread_windows(vocal, RATE, 1, 8.0) == [(at(11.0), at(19.0))]


@pytest.mark.parametrize(("count", "seconds"), [(0, 8.0), (4, 0.0), (4, -1.0)])
def test_no_windows_or_empty_ones_are_refused(count, seconds):
    with pytest.raises(ValueError):
        spread_windows(level_at(10.0, [(0.0, 10.0, 0.3)]), RATE, count, seconds)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def rendered_with(vocal, beat, final, master):
    return Rendered(vocal, beat, final, None, master)


def master_of(gain_db):
    return MasterResult(-9.0, gain_db, True, 0.1, 0.0)


def test_the_masters_change_of_each_sample_goes_onto_both_stems():
    vocal, beat = engine_stems(2.0)
    vocal[: RATE // 4] = 0.0
    beat[: RATE // 4] = 0.0
    gain = 10 ** (12 / 20)
    final = np.clip((vocal + beat) * np.float32(gain), -0.9, 0.9)
    vocal_m, beat_m = after_master(rendered_with(vocal, beat, final, master_of(12.0)))
    sounding = np.abs(vocal + beat) > 1e-6
    np.testing.assert_allclose((vocal_m + beat_m)[sounding], final[sounding], atol=1e-5)
    # the clipper flattens the loud samples, so the vocal is not just made louder
    assert not np.allclose(vocal_m, vocal * gain, atol=1e-3)
    assert np.all(np.abs(vocal_m) <= np.abs(vocal) * gain + 1e-6)
    # where the mix is silent the stems take the master's plain gain
    np.testing.assert_array_equal(vocal_m[: RATE // 4], vocal[: RATE // 4] * np.float32(gain))


def test_where_the_stems_cancel_the_plain_gain_is_used():
    vocal = np.full((4, 2), 0.25, np.float32)
    beat = np.array([[-0.25, -0.25], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]], np.float32)
    final = np.array([[0.0, 0.0], [0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], np.float32)
    vocal_m, beat_m = after_master(rendered_with(vocal, beat, final, master_of(20 * math.log10(2))))
    np.testing.assert_allclose(vocal_m[0], [0.5, 0.5], rtol=1e-6)
    np.testing.assert_allclose(beat_m[0], [-0.5, -0.5], rtol=1e-6)
    np.testing.assert_allclose(vocal_m[1:], 0.5, rtol=1e-6)


def test_a_change_no_clipper_makes_is_held_to_the_masters_gain():
    # a band-limited final near a zero crossing of the mix: the ratio would flip and blow up
    vocal = np.array([[0.4, 0.4], [0.4, 0.4], [0.4, 0.4]], np.float32)
    beat = np.array([[-0.39999, -0.39999], [-0.399, -0.399], [0.0, 0.0]], np.float32)
    final = np.array([[-0.002, -0.002], [0.05, 0.05], [0.8, 0.8]], np.float32)
    vocal_m, _ = after_master(rendered_with(vocal, beat, final, master_of(20 * math.log10(2))))
    np.testing.assert_allclose(vocal_m[0], 0.0, atol=1e-7)
    np.testing.assert_allclose(vocal_m[1], 0.8, rtol=1e-6)
    np.testing.assert_allclose(vocal_m[2], 0.8, rtol=1e-6)


def test_without_the_master_the_stems_are_measured_as_rendered():
    vocal, beat = engine_stems(1.0)
    vocal_m, beat_m = after_master(rendered_with(vocal, beat, vocal + beat, None))
    np.testing.assert_array_equal(vocal_m, vocal)
    np.testing.assert_array_equal(beat_m, beat)


def test_a_real_master_never_raises_a_stem_past_its_gain(track):
    vocal, beat, ctx = track
    rendered = Engine(vocal, beat, ctx).render(Settings())
    assert rendered.master is not None and rendered.master.clipped_share > 0
    gain = 10 ** (rendered.master.gain_db / 20)
    vocal_m, beat_m = after_master(rendered)
    assert np.abs(vocal_m).max() <= np.abs(rendered.vocal).max() * gain * (1 + 1e-6)
    assert np.abs(beat_m).max() <= np.abs(rendered.beat).max() * gain * (1 + 1e-6)
    plain = measure_processed(rendered.vocal * gain, rendered.beat * gain, RATE)
    after = measure_processed(vocal_m, beat_m, RATE)
    assert after[CREST] <= plain[CREST] + 0.1
    assert after[PAIR] == pytest.approx(plain[PAIR], abs=1.0)


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
    return auto_settings(
        Engine(vocal, beat, ctx), LOUDER_REFS, BASE, count=2, seconds=2.0, passes=2
    )


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
    again = auto_settings(
        Engine(vocal, beat, ctx), LOUDER_REFS, BASE, count=2, seconds=2.0, passes=2
    )
    assert again == result


def test_no_passes_or_no_targets_keep_the_base(track):
    vocal, beat, ctx = track
    engine = Engine(vocal, beat, ctx)
    still = auto_settings(engine, LOUDER_REFS, BASE, count=2, seconds=2.0, passes=0)
    assert still.settings == BASE
    assert still.distance_after == still.distance_before
    blind = auto_settings(engine, [], replace(BASE, vocal_db=-9.0), count=2, seconds=2.0, passes=1)
    assert blind.settings == replace(BASE, vocal_db=-9.0)
    assert blind.targets == {} and blind.distance_after == 0.0


def test_the_score_is_taken_after_the_master_on_the_pooled_windows(track, monkeypatch):
    from redline.vocal import auto as auto_module

    vocal, beat, ctx = track
    seen = []
    measure = auto_module.measure_processed

    def recorded(v, b, rate):
        seen.append((v, b))
        return measure(v, b, rate)

    monkeypatch.setattr(auto_module, "measure_processed", recorded)
    auto_settings(Engine(vocal, beat, ctx), LOUDER_REFS, BASE, count=2, seconds=1.5, passes=0)
    ((vocal_m, beat_m),) = seen
    windows = spread_windows(vocal, RATE, 2, 1.5)
    assert len(windows) == 2
    renders = [Engine(vocal[a:b], beat[a:b], ctx).render(BASE) for a, b in windows]
    assert all(r.master is not None and r.master.clipped_share > 0 for r in renders)
    stems = [after_master(r) for r in renders]
    np.testing.assert_array_equal(vocal_m, np.concatenate([v for v, _ in stems]))
    np.testing.assert_array_equal(beat_m, np.concatenate([b for _, b in stems]))
    # what is measured is the mastered sound, not the mix the master was given
    final = np.concatenate([r.final for r in renders])
    mix = np.concatenate([r.vocal + r.beat for r in renders])
    assert rms(vocal_m + beat_m - final) < 0.01 * rms(final)
    assert rms(mix - final) > 0.2 * rms(final)


def test_one_engine_per_window_serves_every_candidate(track, monkeypatch):
    from redline.vocal import auto as auto_module

    vocal, beat, ctx = track
    built = []

    class Counted(Engine):
        def __init__(self, *args, **kwargs):
            built.append(len(args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(auto_module, "Engine", Counted)
    auto_settings(Engine(vocal, beat, ctx), LOUDER_REFS, BASE, count=2, seconds=1.5, passes=1)
    assert built == [b - a for a, b in spread_windows(vocal, RATE, 2, 1.5)]


def test_edge_keys_are_the_ones_on_the_first_or_last_grid_value(track):
    vocal, beat, ctx = track
    engine = Engine(vocal, beat, ctx)
    edges = replace(BASE, vocal_db=-12.0, comp_threshold_db=-38.0, drive_db=40.0)
    kept = auto_settings(engine, LOUDER_REFS, edges, count=1, seconds=2.0, passes=0)
    assert kept.at_edge == ("vocal_db", "drive_db")
    inside = replace(BASE, vocal_db=-7.0, comp_threshold_db=-14.0, drive_db=10.0)
    kept = auto_settings(engine, LOUDER_REFS, inside, count=1, seconds=2.0, passes=0)
    assert kept.at_edge == ("comp_threshold_db",)


def test_the_found_result_reports_its_edges(result):
    expected = tuple(
        key for key, grid in AXES.items() if getattr(result.settings, key) in (grid[0], grid[-1])
    )
    assert result.at_edge == expected


SPREAD = "vocal.short_term_spread_lu"
KURTOSIS = "vocal.kurtosis"
ALL_REFS = [
    ref("a", {PAIR: 2.0, CREST: 10.0, SPREAD: 3.0, KURTOSIS: 4.0}),
    ref("b", {PAIR: 3.0, CREST: 11.0, SPREAD: 4.0, KURTOSIS: 6.0}),
    ref("c", {PAIR: 5.0, CREST: 13.0, SPREAD: 4.5, KURTOSIS: 9.0}),
]


def clean_windows(track, settings, count, seconds):
    """Each window's final and its after-master stems, rendered apart from the search."""
    vocal, beat, ctx = track
    out = []
    for a, b in spread_windows(vocal, RATE, count, seconds):
        rendered = Engine(vocal[a:b], beat[a:b], ctx).render(settings)
        out.append((rendered.final, *after_master(rendered)))
    return out


class KnownSeparator:
    """Gives back each window's own clean stems, changed by `split`, and counts its calls."""

    id = "fake:known"

    def __init__(self, track, settings, count, seconds, split):
        self.stems = {
            final.tobytes(): (index, v, b)
            for index, (final, v, b) in enumerate(clean_windows(track, settings, count, seconds))
        }
        self.split = split
        self.calls = []

    def __call__(self, samples, rate):
        assert rate == RATE
        index, vocal, beat = self.stems[np.asarray(samples).tobytes()]
        self.calls.append(index)
        return self.split(index, vocal, beat)


def expected_bias(track, settings, count, seconds, split):
    """Median over windows of separated minus clean, per metric both sides have."""
    diffs: dict[str, list[float]] = {}
    for index, (_, vocal, beat) in enumerate(clean_windows(track, settings, count, seconds)):
        clean = measure_processed(vocal, beat, RATE)
        separated = measure_processed(*split(index, vocal, beat), RATE)
        for metric in METRICS:
            if clean[metric] is not None and separated[metric] is not None:
                diffs.setdefault(metric, []).append(separated[metric] - clean[metric])
    return {metric: float(np.median(values)) for metric, values in diffs.items()}


def bleed(index, vocal, beat):
    # Roformer-like: some beat stays in the vocal, and the beat loses it
    return vocal + 0.4 * beat, 0.6 * beat


def test_without_a_separator_the_targets_are_the_references_and_no_bias(track, result):
    assert result.bias == {}
    assert result.targets == reference_targets(LOUDER_REFS)
    vocal, beat, ctx = track
    explicit = auto_settings(
        Engine(vocal, beat, ctx), LOUDER_REFS, BASE, count=2, seconds=2.0, passes=2, separator=None
    )
    assert explicit == result


def test_the_bias_is_the_median_over_windows_of_separated_minus_clean(track):
    vocal, beat, ctx = track
    # three windows, so the median is one window's difference and not a mean of two
    separator = KnownSeparator(track, BASE, 3, 1.2, bleed)
    found = auto_settings(
        Engine(vocal, beat, ctx),
        ALL_REFS,
        BASE,
        count=3,
        seconds=1.2,
        passes=0,
        separator=separator,
    )
    bias = expected_bias(track, BASE, 3, 1.2, bleed)
    assert set(bias) == set(METRICS)
    # the bleed is far from nothing on every metric, so a zero bias would not pass
    assert all(abs(value) > 0.05 for value in bias.values()), bias
    assert found.bias == pytest.approx(bias, rel=1e-9, abs=1e-12)
    refs = reference_targets(ALL_REFS)
    for metric in METRICS:
        median, spread = refs[metric]
        assert found.targets[metric] == pytest.approx((median - bias[metric], spread))
    assert found.distance_before == pytest.approx(distance(found.values, found.targets))


def test_a_window_missing_a_metric_on_one_side_is_left_out_of_its_median(track):
    vocal, beat, ctx = track

    def first_silent(index, vocal, beat):
        return (np.zeros_like(vocal), beat) if index == 0 else bleed(index, vocal, beat)

    separator = KnownSeparator(track, BASE, 3, 1.2, first_silent)
    found = auto_settings(
        Engine(vocal, beat, ctx),
        ALL_REFS,
        BASE,
        count=3,
        seconds=1.2,
        passes=0,
        separator=separator,
    )
    bias = expected_bias(track, BASE, 3, 1.2, first_silent)
    # two windows left: their mean, which differs from the three-window median
    assert found.bias == pytest.approx(bias, rel=1e-9, abs=1e-12)
    assert bias != pytest.approx(expected_bias(track, BASE, 3, 1.2, bleed))


def test_a_metric_no_window_has_on_the_separated_side_gets_no_bias(track):
    vocal, beat, ctx = track

    def silent(index, vocal, beat):
        return np.zeros_like(vocal), beat

    separator = KnownSeparator(track, BASE, 2, 1.5, silent)
    found = auto_settings(
        Engine(vocal, beat, ctx),
        ALL_REFS,
        BASE,
        count=2,
        seconds=1.5,
        passes=0,
        separator=separator,
    )
    assert found.bias == {}
    assert found.targets == reference_targets(ALL_REFS)


def test_a_perfect_separator_finds_no_bias_and_changes_nothing(track, result):
    vocal, beat, ctx = track
    separator = KnownSeparator(track, BASE, 2, 2.0, lambda index, v, b: (v, b))
    found = auto_settings(
        Engine(vocal, beat, ctx),
        LOUDER_REFS,
        BASE,
        count=2,
        seconds=2.0,
        passes=2,
        separator=separator,
    )
    assert set(found.bias) == set(METRICS)
    assert all(value == pytest.approx(0.0, abs=1e-9) for value in found.bias.values())
    assert found.settings == result.settings
    assert found.distance_after == pytest.approx(result.distance_after)


def test_a_separator_that_reads_the_vocal_louder_moves_the_target_back(track, result):
    vocal, beat, ctx = track
    louder = 20 * math.log10(2)
    # references that read the vocal 6 dB louder only because they were separated
    shifted = [ref(r.name, {PAIR: r.values[PAIR] + louder}) for r in LOUDER_REFS]
    naive = auto_settings(Engine(vocal, beat, ctx), shifted, BASE, count=2, seconds=2.0, passes=2)
    separator = KnownSeparator(track, BASE, 2, 2.0, lambda index, v, b: (2 * v, b))
    found = auto_settings(
        Engine(vocal, beat, ctx),
        shifted,
        BASE,
        count=2,
        seconds=2.0,
        passes=2,
        separator=separator,
    )
    assert found.bias[PAIR] == pytest.approx(louder, abs=1e-6)
    assert found.targets[PAIR] == pytest.approx(result.targets[PAIR])
    # without the bias the search chases the separated reading and lands louder
    assert naive.settings.vocal_db > result.settings.vocal_db
    assert found.settings == result.settings


def test_the_separator_runs_once_per_window_on_its_final_not_per_candidate(track):
    vocal, beat, ctx = track
    separator = KnownSeparator(track, BASE, 2, 1.5, bleed)
    found = auto_settings(
        Engine(vocal, beat, ctx),
        LOUDER_REFS,
        BASE,
        count=2,
        seconds=1.5,
        passes=2,
        separator=separator,
    )
    assert found.settings != BASE
    assert separator.calls == [0, 1]


def test_by_default_three_windows_of_six_seconds_are_rendered(monkeypatch):
    from redline.vocal import auto as auto_module

    vocal, beat = engine_stems(20.0)
    ctx = engine_context(vocal)
    built = []

    class Counted(Engine):
        def __init__(self, *args, **kwargs):
            built.append(len(args[0]))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(auto_module, "Engine", Counted)
    auto_settings(Engine(vocal, beat, ctx), LOUDER_REFS, BASE, passes=0)
    assert built == [at(6.0)] * 3
