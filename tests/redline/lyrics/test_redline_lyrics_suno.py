import pytest
from redline.lyrics.suno import MAX_CHARACTERS, card, convert, respell, title_of
from redline.lyrics.text.parse import parse_text
from redline.lyrics.write.recipe import Recipe, SectionPlan

TEXT = """[Интро]
скрр скрр (эй)

[Припев]
лёд на мне и лёд в стакане
лёд на мне и лёд в стакане
я в GTA на 100 000
кэш в кармане (кармане)

[Куплет]
первая строка куплета
вторая строка куплета

[Бридж]
ДАЙ МНЕ ГАЗ

[Припев]
лёд на мне и лёд в стакане
лёд на мне и лёд в стакане
я в GTA на 100 000
кэш в кармане (кармане)

[Куплет]
третья строка
четвёртая строка

[Аутро]
тихо
"""


def lines_of(text):
    return text.strip("\n").split("\n")


def test_sections_get_english_tags_on_their_own_lines():
    out = lines_of(convert(parse_text(TEXT)))
    tags = [line for line in out if line.startswith("[")]
    assert tags == [
        "[Intro]",
        "[Chorus]",
        "[Verse 1]",
        "[Bridge]",
        "[Chorus]",
        "[Verse 2]",
        "[Outro]",
        "[End]",
    ]


def test_blank_lines_only_between_sections():
    out = convert(parse_text(TEXT))
    for block in out.strip("\n").split("\n\n"):
        assert block.startswith("[") and "\n\n" not in block
    assert out.count("\n\n") == 6


def test_numbers_and_latin_acronyms_are_spelled_out():
    assert respell("я в GTA на 100 000") == "я в джи-ти-эй на сто тысяч"
    assert respell("VVS на руке, AP на запястье") == "ви-ви-эс на руке, эй-пи на запястье"
    assert respell("ДАЙ МНЕ ГАЗ") == "ДАЙ МНЕ ГАЗ"
    assert respell("Rick Owens") == "Rick Owens"
    assert "100" not in convert(parse_text(TEXT))


def test_stress_marks_the_line_end_words_only_when_asked():
    marks = {"лёд на мне и лёд в стакане": "лёд на мне и лёд в стак+ане"}
    out = convert(parse_text(TEXT), mark=lambda line: marks.get(line, line))
    assert "стакАне" in out and "стакАне" not in convert(parse_text(TEXT))
    assert "кармане (кармане)" in out


def test_the_title_comes_from_the_chorus():
    assert title_of(parse_text(TEXT)) == "Лёд на мне"


def test_the_card_holds_every_field_and_sliders_in_range():
    text = card(parse_text(TEXT))
    for field in ("Title", "Style", "Exclude", "Weirdness", "Style Influence", "Variety", "Lyrics"):
        assert field in text
    assert "Variety: 0" in text


def field(text, name):
    return text.split(f"{name}: ")[1].split("\n")[0]


def test_style_tags_are_few_and_name_no_one():
    style = field(card(parse_text(TEXT)), "Style")
    assert 3 <= len(style.split(", ")) <= 7 and len(style) < 1000


def test_the_style_is_fast_trap_and_slow_low_vocals_are_excluded():
    text = card(parse_text(TEXT))
    style, exclude = field(text, "Style").split(", "), field(text, "Exclude").split(", ")
    assert style[0] == "trap"
    assert not {"slow", "deep", "underground", "laid-back", "hazy"} & set(" ".join(style).split())
    assert {"slow", "deep vocals", "underground rap"} <= set(exclude)


def test_an_own_style_replaces_the_default_and_keeps_the_rest():
    text = card(parse_text(TEXT), style="мой стиль, 150 bpm")
    assert field(text, "Style") == "мой стиль, 150 bpm"
    assert field(text, "Exclude") == field(card(parse_text(TEXT)), "Exclude")


def test_too_long_lyrics_are_refused():
    long = "[Куплет]\n" + "\n".join(["очень длинная строчка текста про кэш"] * 200) + "\n"
    with pytest.raises(ValueError, match=str(MAX_CHARACTERS)):
        convert(parse_text(long))


def beat_recipe(*flows):
    sections = tuple(SectionPlan("verse", 2, (6, 8), "AABB", flow) for flow in flows)
    return Recipe(1, "лёд", "short", sections, (), (), (), 0, 0, 0, bpm=66.0)


def test_a_beat_seed_gets_a_vocal_style_with_its_flows_and_no_beat_tags():
    style = field(card(parse_text(TEXT), recipe=beat_recipe("triplets", "eighths")), "Style")
    terms = style.split(", ")
    assert "triplet flow" in terms and "bouncy flow" in terms
    assert not {"bpm", "808", "hi-hats", "trap"} & set(style.split())
    assert 3 <= len(terms) <= 7


def test_a_flow_used_twice_is_named_once_and_a_plain_recipe_keeps_the_trap_style():
    style = field(card(parse_text(TEXT), recipe=beat_recipe("triplets", "triplets")), "Style")
    assert style.split(", ").count("triplet flow") == 1
    plain = Recipe(
        1, "лёд", "short", (SectionPlan("verse", 2, (6, 8), "AABB"),), (), (), (), 0, 0, 0
    )
    assert field(card(parse_text(TEXT), recipe=plain), "Style") == field(
        card(parse_text(TEXT)), "Style"
    )


def test_the_owners_style_wins_on_a_beat_seed_too():
    text = card(parse_text(TEXT), style="свой стиль", recipe=beat_recipe("triplets"))
    assert field(text, "Style") == "свой стиль"


def test_a_title_cut_at_a_comma_or_dash_loses_it():
    text = "[Припев]\nдым в клубе, и дым в голове\nдым в клубе, и дым в голове\n"
    assert title_of(parse_text(text)) == "Дым в клубе"
    assert title_of(parse_text("[Припев]\nночь — и я снова тут\n")) == "Ночь — и"


def test_a_hard_beat_gets_an_aggressive_delivery_in_the_style():
    from dataclasses import replace

    hard = field(
        card(parse_text(TEXT), recipe=replace(beat_recipe("triplets", "eighths"), delivery="hard")),
        "Style",
    )
    calm = field(
        card(parse_text(TEXT), recipe=replace(beat_recipe("triplets", "eighths"), delivery="calm")),
        "Style",
    )
    assert "aggressive" in hard and "aggressive" not in calm
    assert calm == field(card(parse_text(TEXT), recipe=beat_recipe("triplets", "eighths")), "Style")
    assert len(hard.split(", ")) <= 7
