import pytest

from redline.lyrics.form import check_draft, is_verb
from redline.lyrics.recipe import Recipe, SectionPlan


def recipe(*sections):
    return Recipe(
        seed=1,
        topic="ночь",
        band="middle",
        sections=tuple(sections),
        words=(),
        latin=(),
        adlibs=(),
        adlib_share=0.0,
        hook_repeat_share=0.0,
        en_share=0.0,
    )


VERSE = SectionPlan("verse", 4, (5, 7), "AABB")
HOOK = SectionPlan("hook", 2, (3, 5), "AABB")
# 7 syllables a line, ends дом/лом, сыр/мир
GOOD_VERSE = (
    "[Куплет]\nя иду в мой старый дом\nи беру тяжелый лом\n"
    "на столе лежит мой сыр\nи вокруг огромный мир\n"
)
GOOD_HOOK = "[Припев]\nэй гори огонь\nэй гори огонь\n"


def problems(text, *sections, **kwargs):
    found, _ = check_draft(text, recipe(*sections), **kwargs)
    return {p.kind for p in found}


def test_a_draft_that_follows_the_recipe_passes():
    assert problems(GOOD_VERSE + GOOD_HOOK, VERSE, HOOK) == set()


@pytest.mark.parametrize(
    "text",
    ["Вот ваш текст:\n" + GOOD_VERSE, "```\n" + GOOD_VERSE + "```\n"],
)
def test_anything_but_lyrics_is_refused(text):
    assert "not lyrics" in problems(text, VERSE)


def test_sections_out_of_order_or_missing_are_named():
    found, _ = check_draft(GOOD_HOOK + GOOD_VERSE, recipe(VERSE, HOOK))
    assert [p.kind for p in found] == ["sections"]
    assert "verse-hook" in found[0].detail and "hook-verse" in found[0].detail
    assert "sections" in problems(GOOD_VERSE, VERSE, HOOK)


def test_a_section_off_by_more_than_one_line_is_named():
    two_lines = "[Куплет]\nя иду в мой старый дом\nи беру тяжелый лом\n"
    found, _ = check_draft(two_lines, recipe(VERSE))
    assert [p.kind for p in found] == ["lines"]
    three_lines = two_lines + "на столе лежит мой сыр\n"
    assert "lines" not in problems(three_lines, VERSE)


def test_a_line_one_syllable_off_the_range_still_counts_as_in_it():
    # the range is 5-7; 8 is in with the tolerance of 1
    near = GOOD_VERSE.replace("и вокруг огромный мир", "и вокруг огромный злой мир")
    _, stats = check_draft(near, recipe(VERSE))
    assert stats["syllables_in_range"] == 1.0


def test_the_syllable_share_names_the_lines_furthest_out():
    # three lines of 7 in the range 5-7, the fourth has 12
    far = GOOD_VERSE.replace("и вокруг огромный мир", "и вокруг огромный и очень тихий мир")
    found, stats = check_draft(far, recipe(VERSE))
    assert stats["syllables_in_range"] == 0.75
    assert "syllables" not in {p.kind for p in found}
    found, _ = check_draft(far, recipe(VERSE), syllable_share=0.8)
    assert [(p.kind, p.where) for p in found] == [
        ("syllables", "the draft"),
        ("syllables", "line 4"),
    ]


def test_rhymes_are_counted_over_the_whole_text():
    flat = GOOD_VERSE.replace("тяжелый лом", "тяжелый нож")
    found, stats = check_draft(flat, recipe(VERSE), rhyme_share=0.5)
    assert stats["rhymed_pairs"] == 0.5
    assert "rhymes" not in {p.kind for p in found}
    assert "rhymes" in problems(flat, VERSE, rhyme_share=0.75)


def test_repeated_lines_do_not_count_as_rhymes():
    _, stats = check_draft(GOOD_HOOK, recipe(HOOK))
    assert stats["rhyme_pairs"] == 0


def test_leftover_lines_after_full_windows_are_not_judged():
    six = GOOD_VERSE + "совсем без рифмы строчка\nи еще одна такая\n"
    plan = SectionPlan("verse", 6, (5, 9), "AABB")
    _, stats = check_draft(six, recipe(plan))
    assert stats["rhyme_pairs"] == 2


@pytest.mark.parametrize(
    "word", ["сказал", "любила", "знаешь", "делает", "играть", "смеяться", "сидели", "горит"]
)
def test_verbs_by_their_endings(word):
    assert is_verb(word)


@pytest.mark.parametrize(
    "word",
    [
        "свет",
        "бит",
        "рубли",
        "игла",
        "зал",
        "мать",
        "брат",
        "режим",
        "вокал",
        "путь",
        "финал",
        "кит",
    ],
)
def test_nouns_with_verb_like_endings_are_not_verbs(word):
    assert not is_verb(word)


def test_verb_rhymes_are_reported_not_refused():
    verbs = GOOD_VERSE.replace("в мой старый дом", "и вдаль смотрел").replace(
        "и беру тяжелый лом", "и потом опять сидел"
    )
    found, stats = check_draft(verbs, recipe(VERSE))
    assert stats["verb_rhymes"] == 0.5
    assert "verbs" not in {p.kind for p in found}


def test_a_draft_too_long_for_suno_goes_back():
    plan = SectionPlan("verse", 4, (1, 400), "AABB")

    def text_of(width):
        filler = "та " * width
        return "[Куплет]\n" + "\n".join([filler + "дом", filler + "лом"] * 2) + "\n"

    fits = text_of(240)
    too_long = text_of(260)
    assert len(fits) <= 3000 < len(too_long)
    assert "length" not in problems(fits, plan)
    assert "length" in problems(too_long, plan)


def test_verbs_with_stress_marks_are_still_verbs():
    assert is_verb("игр+ает") and is_verb("сгор+ел")


def test_words_after_the_last_section_are_refused():
    signed = GOOD_VERSE + "надеюсь тебе понравится\n"
    assert "lines" in problems(signed, VERSE)


def test_one_rhyme_held_for_more_than_four_lines_goes_back():
    plan = SectionPlan("verse", 6, (5, 9), "AAAA")
    held = (
        "[Куплет]\n"
        + "\n".join(f"та та та {w}" for w in ["дом", "лом", "сом", "ком", "гном", "ром"])
        + "\n"
    )
    found, stats = check_draft(held, recipe(plan))
    assert stats["longest_rhyme_run"] == 6
    assert "rhymes" in {p.kind for p in found}
    four = (
        "[Куплет]\n"
        + "\n".join(f"та та та {w}" for w in ["дом", "лом", "сом", "ком", "сыр", "мир"])
        + "\n"
    )
    found, stats = check_draft(four, recipe(plan))
    assert stats["longest_rhyme_run"] == 4
    assert not [p for p in found if p.kind == "rhymes" and "run" in p.detail]


def test_half_the_lines_off_the_band_is_still_fine():
    loose = GOOD_VERSE.replace("и вокруг огромный мир", "и вокруг огромный и очень тихий мир")
    loose = loose.replace("на столе лежит мой сыр", "на моем столе давно лежит мой сыр")
    _, stats = check_draft(loose, recipe(VERSE))
    assert stats["syllables_in_range"] == 0.5
    assert "syllables" not in problems(loose, VERSE)


def test_a_chanted_hook_line_is_not_a_held_rhyme():
    plan = SectionPlan("hook", 6, (5, 9), "AABB")
    chant = "[Припев]\n" + "я на дрипе снова тут\n" * 5 + "и другая строка\n"
    _, stats = check_draft(chant, recipe(plan))
    assert stats["longest_rhyme_run"] <= 2


def test_a_held_rhyme_is_found_wherever_it_starts():
    from redline.lyrics.form import _longest_run

    assert _longest_run(["рома", "ка", "ка", "ка", "рука", "рука"]) == 5
