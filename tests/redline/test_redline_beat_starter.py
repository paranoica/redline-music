import json

import pytest

redline_dsp = pytest.importorskip("redline_dsp")

from redline.beat import handoff  # noqa: E402
from redline.beat.checks import check_score  # noqa: E402
from redline.beat.recipe import GENRES, make_recipe, pack_kit  # noqa: E402
from redline.beat.starter import starter_score  # noqa: E402
from test_redline_beat_pack import KIT, write_catalog  # noqa: E402


@pytest.mark.parametrize("genre", sorted(GENRES))
def test_every_genres_starter_is_a_score_with_no_errors(genre):
    for seed in (1, 2, 3):
        recipe = make_recipe(seed, genre=genre)
        score = starter_score(recipe)
        known = {s.library for s in recipe.sounds if s.library}
        checked = check_score(json.dumps(score), recipe, known)
        assert checked.errors == (), (seed, checked.errors)
        assert sum(s["bars"] for s in score["sections"]) == sum(p.bars for p in recipe.structure)


def test_the_intro_plays_the_808_alone_and_the_808_sits_on_the_root_in_its_octave():
    recipe = make_recipe(4, genre="trap", key="F# minor")
    score = starter_score(recipe)
    assert score["sections"][0]["play"] == ["bass"]
    assert all(s["play"] == ["drums", "bass"] for s in score["sections"][1:])
    notes = {n["note"] for n in score["patterns"]["bass"]["rows"]["bass"]}
    assert notes == {"F#1"}
    assert score["patterns"]["melody"]["rows"] == {"lead": []}


def test_the_808_starts_just_after_each_kick_not_on_it():
    recipe = make_recipe(4, genre="trap")
    score = starter_score(recipe)
    kinds = {w.kind for w in check_score(json.dumps(score), recipe, set()).warnings}
    assert "clash" not in kinds


def test_a_pack_starter_plays_the_packs_drums_and_808(tmp_path):
    recipe = make_recipe(4, genre="trap", pack=pack_kit(KIT))
    score = starter_score(recipe)
    assert score["sounds"]["kick"]["instrument"] == "oneshot"
    assert score["sounds"]["bass"]["instrument"] == "sample808"
    assert "bass" in score["patterns"]["bass"]["rows"]
    known = {s.library for s in recipe.sounds if s.library}
    assert check_score(json.dumps(score), recipe, known).errors == ()


def test_a_new_starter_takes_a_free_seed_and_is_saved(tmp_path):
    root = tmp_path / "beats"
    (root / "12345").mkdir(parents=True)
    seed = handoff.new_starter(root, "uk_drill", "synth", tmp_path / "none.json")
    folder = root / str(seed)
    assert seed != 12345 and 10_000 <= seed < 1_000_000
    state = json.loads((folder / "state.json").read_text())
    assert state["status"] == "saved" and state["score"] == "score-1.json"
    assert json.loads((folder / "recipe.json").read_text())["genre"] == "uk_drill"
    assert folder.stat().st_mode & 0o077 == 0
    # the seed is settled: a write on it now reports the saved score, no request
    assert isinstance(handoff.write_beat(root, seed), handoff.Saved)


def test_a_pack_starter_needs_a_pack_that_makes_a_kit(tmp_path):
    with pytest.raises(handoff.BeatError):
        handoff.new_starter(tmp_path / "beats", None, "pack", tmp_path / "none.json")
    catalog = tmp_path / "catalog.json"
    write_catalog(catalog, KIT)
    seed = handoff.new_starter(tmp_path / "beats", "trap", "pack", catalog)
    recipe = json.loads((tmp_path / "beats" / str(seed) / "recipe.json").read_text())
    assert recipe["kit"] == "pack"
