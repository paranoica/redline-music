import pytest

from redline.cli import main
from redline.lyrics.rhyme import rhymes, scheme
from redline.lyrics.syllables import count_syllables


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("мы гуляем по крышам", 7),
        ("в доме", 2),
        ("", 0),
        ("строка (адлиб и слова)", 2),
        ("GTA и LV", 6),
        ("Telegram", 3),
        ("time", 1),
        ("music", 2),
        ("100 причин", 3),
        ("2024", 9),
        ("21 день", 5),
        ("ха-ха-ха-а-а", 3),
        ("даааа", 1),
        ("сине-голубой", 5),
        ("Lamb'ом на районе", 6),
    ],
)
def test_syllables_by_hand(line, expected):
    assert count_syllables(line) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("дом", "ком"),
        ("пальчик", "мальчик"),
        ("дуб", "суп"),
        ("сыр", "мир"),
        ("ветер", "вечер"),
        ("порядке", "банке"),
        ("город", "холод"),
        ("gucci", "тучи"),
    ],
)
def test_words_that_rhyme(a, b):
    assert rhymes(a, b)


@pytest.mark.parametrize(("a", "b"), [("дом", "сыр"), ("ветер", "город"), ("утро", "свет")])
def test_words_that_do_not_rhyme(a, b):
    assert not rhymes(a, b)


@pytest.mark.parametrize(
    ("ends", "expected"),
    [
        (["дом", "ком", "сыр", "мир"], "AABB"),
        (["дом", "сыр", "ком", "мир"], "ABAB"),
        (["дом", "сыр", "мир", "ком"], "ABBA"),
        (["дом", "ком", "лом", "сом"], "AAAA"),
        (["дом", "сыр", "утро", "свет"], "other"),
        (["дом", "ком", "сыр"], None),
    ],
)
def test_four_line_schemes(ends, expected):
    assert scheme(ends) == expected


def test_cli_stats_prints_syllables_and_letters(tmp_path, capsys):
    path = tmp_path / "text.txt"
    path.write_text("[Куплет]\nя иду домой\nи несу с собой\nкрасный шар (эй)\n", encoding="utf-8")
    assert main(["lyrics", "stats", str(path)]) == 0
    rows = [r.split() for r in capsys.readouterr().out.splitlines() if r.strip()[:1].isdigit()]
    assert [(r[0], r[1]) for r in rows] == [("5", "A"), ("5", "A"), ("3", "B")]


def test_cli_stats_without_the_file_exits_2(tmp_path):
    assert main(["lyrics", "stats", str(tmp_path / "none.txt")]) == 2
