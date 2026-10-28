import pytest

from redline.lyrics.parse import parse_text
from redline.lyrics.recipe import Recipe, SectionPlan
from redline.lyrics.suno import MAX_CHARACTERS, card, convert, respell, title_of

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


def recipe(band="short"):
    return Recipe(
        1, "лёд, кэш", band, (SectionPlan("verse", 2, (5, 9), "AABB"),), (), (), (), 0, 0, 0
    )


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
    text = card(parse_text(TEXT), recipe())
    for field in ("Title", "Style", "Exclude", "Weirdness", "Style Influence", "Variety", "Lyrics"):
        assert field in text
    assert "Variety: 0" in text


def test_style_tags_are_few_and_name_no_one():
    style = card(parse_text(TEXT), recipe("long")).split("Style: ")[1].split("\n")[0]
    assert 3 <= len(style.split(", ")) <= 7 and len(style) < 1000


def test_too_long_lyrics_are_refused():
    long = "[Куплет]\n" + "\n".join(["очень длинная строчка текста про кэш"] * 200) + "\n"
    with pytest.raises(ValueError, match=str(MAX_CHARACTERS)):
        convert(parse_text(long))
