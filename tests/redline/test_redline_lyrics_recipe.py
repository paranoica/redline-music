import json
import os
import subprocess
import sys

import pytest

from redline.lyrics.parse import parse_text
from redline.lyrics.profile import count_tracks, to_profile
from redline.lyrics.recipe import ANCHORS, make_recipe, recipe_json

COMMON = (
    "[Куплет]\n"
    + "\n".join(
        f"{w} летит по ночному городу {i} (эй)"
        for i, w in enumerate(["пламя", "ветер", "холод", "голос", "кэш", "дым", "звон", "свет"])
    )
    + "\n[Припев]\nдым и свет\nдым и свет\n"
)
SHORT = "[Куплет]\nкоротко\nснова\n[Припев]\nэй эй\n"
RARE = "[Интро]\nтихо\n[Бридж]\nмост через реку\n[Аутро]\nконец\n"


@pytest.fixture
def profile():
    texts = [COMMON] * 6 + [SHORT] * 3 + [RARE] + ["[Куплет]\nmoney gucci flow\n"]
    own, _ = count_tracks([parse_text(t) for t in texts])
    return json.loads(json.dumps(to_profile(own, "artist", "secretslug")))


def test_the_same_seed_gives_the_same_recipe(profile):
    assert recipe_json(make_recipe(profile, 7)) == recipe_json(make_recipe(profile, 7))


def test_the_recipe_is_the_same_in_another_process(profile, tmp_path):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    code = (
        "import json, sys; from redline.lyrics.recipe import make_recipe, recipe_json; "
        "p = json.load(open(sys.argv[1], encoding='utf-8')); print(recipe_json(make_recipe(p, 7)))"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", code, str(path)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": str(hash_seed)},
        ).stdout
        for hash_seed in (1, 2)
    ]
    assert runs[0] == runs[1] == recipe_json(make_recipe(profile, 7)) + "\n"


def test_seeds_reach_rare_shapes_and_both_line_lengths(profile):
    recipes = [make_recipe(profile, seed) for seed in range(300)]
    shapes = {"-".join(s.kind for s in r.sections) for r in recipes}
    tracks = {
        p
        for p, _ in profile["structure"]["patterns"]
        if "-" in p and {"verse", "hook"} & set(p.split("-"))
    }
    assert tracks == shapes
    assert {r.band for r in recipes} == {"short", "middle", "long"}


def test_line_lengths_stay_inside_the_profile(profile):
    for seed in range(100):
        for section in make_recipe(profile, seed).sections:
            known = profile["line_syllables"].get(section.kind)
            if known:
                low, high = section.syllables
                assert known["p10"] <= low <= high <= known["p90"]


def test_words_come_from_the_profile_only(profile):
    vocabulary = {w["word"] for w in profile["vocabulary"]["top"]}
    latin = {w["word"] for w in profile["latin"]["top"]}
    adlibs = {w for w, _ in profile["adlibs"]["top"]}
    for seed in range(50):
        recipe = make_recipe(profile, seed)
        assert set(recipe.words) <= vocabulary
        assert set(recipe.latin) <= latin
        assert set(recipe.adlibs) <= adlibs


def test_a_given_topic_is_kept_and_a_missing_one_is_drawn(profile):
    assert make_recipe(profile, 1, "ночь и деньги").topic == "ночь и деньги"
    assert set(make_recipe(profile, 1).topic.split(", ")) <= set(ANCHORS)


def test_the_recipe_names_no_profile(profile):
    assert "secretslug" not in recipe_json(make_recipe(profile, 3))


def test_schemes_are_the_four_named_ones(profile):
    for seed in range(50):
        for section in make_recipe(profile, seed).sections:
            assert section.scheme in ("AABB", "ABAB", "ABBA", "AAAA")


def test_a_kind_missing_from_the_profile_still_gets_a_plan(profile):
    profile["structure"]["patterns"] = [["prehook-verse", 1.0]]
    recipe = make_recipe(profile, 0)
    assert [s.kind for s in recipe.sections] == ["prehook", "verse"]
    assert recipe.sections[0].lines > 0 and recipe.sections[0].syllables[0] > 0


def test_short_and_long_bands_give_different_line_lengths(profile):
    by_band = {}
    for seed in range(100):
        recipe = make_recipe(profile, seed)
        verses = [s for s in recipe.sections if s.kind == "verse"]
        if verses:
            by_band.setdefault(recipe.band, verses[0].syllables)
    assert by_band["short"][0] < by_band["long"][0]
    assert by_band["short"][1] < by_band["long"][1]


def test_a_recipe_stays_short_enough_for_suno(profile):
    from redline.lyrics.recipe import MAX_LINES

    profile["structure"]["patterns"] = [["verse-hook-verse-hook-verse-bridge-hook-outro", 1.0]]
    profile["lines_per_section"]["verse"] = {"hist": {"16": 1.0}, "p10": 16, "p50": 16, "p90": 16}
    for seed in range(20):
        assert sum(s.lines for s in make_recipe(profile, seed).sections) <= MAX_LINES


def test_trimming_never_empties_a_section(profile):
    from redline.lyrics.recipe import MAX_LINES

    profile["structure"]["patterns"] = [["-".join(["verse"] * 60), 1.0]]
    recipe = make_recipe(profile, 0)
    assert all(s.lines >= 1 for s in recipe.sections)
    assert sum(s.lines for s in recipe.sections) <= max(MAX_LINES, len(recipe.sections))


def test_each_seed_draws_its_own_set_of_devices(profile):
    from redline.lyrics.recipe import DEVICES

    sets = {make_recipe(profile, seed).devices for seed in range(30)}
    assert len(sets) > 5
    for devices in sets:
        assert devices and all(name in DEVICES and count >= 1 for name, count in devices)


def test_a_drawn_topic_is_a_few_anchor_words_not_a_story(profile):
    for seed in range(20):
        topic = make_recipe(profile, seed).topic
        assert 2 <= len(topic.split(", ")) <= 3
        assert all(len(word.split()) == 1 for word in topic.split(", "))


def test_a_recipe_is_never_a_lone_section_or_a_snippet(profile):
    from redline.lyrics.recipe import MIN_LINES

    profile["structure"]["patterns"] = [["verse", 0.9], ["intro-verse-hook-verse", 0.1]]
    profile["lines_per_section"]["verse"] = {"hist": {"8": 1.0}, "p10": 8, "p50": 8, "p90": 8}
    for seed in range(30):
        recipe = make_recipe(profile, seed)
        assert len(recipe.sections) >= 2
        assert sum(s.lines for s in recipe.sections) >= MIN_LINES


def test_growing_a_short_track_keeps_its_hooks_equal(profile):
    profile["structure"]["patterns"] = [["hook-intro-hook", 1.0]]
    profile["lines_per_section"]["hook"] = {"hist": {"4": 1.0}, "p10": 4, "p50": 4, "p90": 4}
    profile["lines_per_section"]["intro"] = {"hist": {"2": 1.0}, "p10": 2, "p50": 2, "p90": 2}
    for seed in range(10):
        hooks = {s.lines for s in make_recipe(profile, seed).sections if s.kind == "hook"}
        assert len(hooks) == 1


def test_a_track_has_a_verse_or_a_hook(profile):
    profile["structure"]["patterns"] = [["other-other", 0.9], ["intro-verse-hook", 0.1]]
    for seed in range(30):
        kinds = {s.kind for s in make_recipe(profile, seed).sections}
        assert kinds & {"verse", "hook"}
