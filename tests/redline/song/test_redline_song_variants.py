import json
from dataclasses import replace

import pytest
from redline.engine.render import Engine
from redline.engine.settings import SPECS, Settings
from redline.measure.run import FileResult
from redline.song.variants import auto_with_variants, neighbours, variants
from redline.vocal.auto import AXES, auto_settings
from redline_signals import RATE, engine_context, engine_stems

MID = Settings(vocal_db=-6.0, comp_threshold_db=-38.0, drive_db=20.0)


def key(v):
    return (v.axis, v.direction)


def test_a_mid_grid_value_has_a_neighbour_on_each_side_of_every_axis():
    found = {key(v): v.value for v in neighbours(MID)}
    assert found == {
        ("vocal_db", "down"): -9.0,
        ("vocal_db", "up"): -3.0,
        ("comp_threshold_db", "down"): -44.0,
        ("comp_threshold_db", "up"): -32.0,
        ("drive_db", "down"): 10.0,
        ("drive_db", "up"): 30.0,
    }


def test_each_neighbour_changes_only_its_own_axis():
    for v in neighbours(MID):
        for name in AXES:
            same = getattr(v.settings, name) == getattr(MID, name)
            assert same == (name != v.axis)
        assert replace(v.settings, **{v.axis: getattr(MID, v.axis)}) == MID


def test_an_edge_value_yields_only_the_inner_side():
    top = [key(v) for v in neighbours(replace(MID, drive_db=40.0)) if v.axis == "drive_db"]
    bottom = [key(v) for v in neighbours(replace(MID, drive_db=0.0)) if v.axis == "drive_db"]
    assert top == [("drive_db", "down")]
    assert bottom == [("drive_db", "up")]


def test_an_off_grid_level_gets_the_nearest_grid_values_around_it():
    found = {key(v): v.value for v in neighbours(replace(MID, vocal_db=-4.5))}
    assert found[("vocal_db", "down")] == -6.0
    assert found[("vocal_db", "up")] == -3.0


def test_a_level_beyond_the_grid_gets_the_grids_end_on_the_inner_side_only():
    found = {key(v): v.value for v in neighbours(replace(MID, vocal_db=-20.0))}
    assert ("vocal_db", "down") not in found and found[("vocal_db", "up")] == -12.0
    found = {key(v): v.value for v in neighbours(replace(MID, vocal_db=6.0))}
    assert ("vocal_db", "up") not in found and found[("vocal_db", "down")] == 3.0


def test_every_neighbour_is_inside_the_ranges_of_its_setting():
    for start in (MID, replace(MID, vocal_db=-24.0), replace(MID, comp_threshold_db=-14.0)):
        for v in neighbours(start):
            spec = SPECS[v.axis]
            assert spec.low <= getattr(v.settings, v.axis) <= spec.high


def test_the_same_seed_gives_the_same_variants():
    assert variants(MID, 7) == variants(MID, 7)


def test_different_seeds_give_different_variants():
    picks = {seed: [key(v) for v in variants(MID, seed)] for seed in range(6)}
    assert len({tuple(p) for p in picks.values()}) > 1
    assert picks[0] != picks[1]


def test_variants_differ_from_the_result_and_from_each_other():
    for seed in range(10):
        chosen = variants(MID, seed)
        assert len(chosen) == 3
        assert all(v.settings != MID for v in chosen)
        assert len({v.settings for v in chosen}) == 3


def test_more_variants_than_neighbours_gives_all_neighbours():
    assert len(variants(MID, 1, n=50)) == 6
    edge = replace(MID, vocal_db=-12.0, comp_threshold_db=-56.0, drive_db=0.0)
    assert len(variants(edge, 1, n=50)) == 3
    assert variants(MID, 1, n=0) == []


def test_the_score_is_asked_once_for_the_chosen_and_kept_on_them():
    asked = []

    def score(batch):
        asked.append(list(batch))
        return [float(i) for i in range(len(batch))]

    chosen = variants(MID, 3, score=score)
    assert len(asked) == 1 and asked[0] == [v.settings for v in chosen]
    assert [v.distance for v in chosen] == [0.0, 1.0, 2.0]
    assert all(v.distance is None for v in variants(MID, 3))


PAIR = "pair.vocal_minus_beat_lu"
REFS = [FileResult(n, "pcm_16", RATE, False, values={PAIR: p}) for n, p in (("a", 2.5), ("b", 3.5))]


@pytest.fixture(scope="module")
def synthetic():
    vocal, beat = engine_stems(4.0)
    return vocal, beat, engine_context(vocal)


def test_the_auto_job_gives_json_with_the_distances_of_auto_and_variants(synthetic):
    vocal, beat, ctx = synthetic
    base = Settings(beat_db=3.0, octave_down_db=-12.0)
    kwargs = {"base": base, "count": 2, "seconds": 2.0, "passes": 1}
    out = auto_with_variants(Engine(vocal, beat, ctx), REFS, 5, **kwargs)
    found = auto_settings(Engine(vocal, beat, ctx), REFS, **kwargs)
    json.dumps(out)
    assert out["auto"]["vocal_db"] == found.settings.vocal_db
    assert out["auto"]["distance_before"] == found.distance_before
    assert out["auto"]["distance_after"] == found.distance_after
    assert len(out["variants"]) == 3
    for v in out["variants"]:
        assert v["settings"][v["axis"]] == v["value"] != out["auto"][v["axis"]]
        (again,) = found.score([replace(found.settings, **{v["axis"]: v["value"]})])
        assert v["distance"] == pytest.approx(again)
    assert (
        out["variants"]
        == auto_with_variants(Engine(vocal, beat, ctx), REFS, 5, **kwargs)["variants"]
    )
