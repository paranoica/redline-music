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
        ("домИно", "кино"),
        ("ми-и-ис", "лис"),
        ("деньгИ", "мозгИ"),
    ],
)
def test_words_that_rhyme(a, b):
    assert rhymes(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("дом", "сыр"),
        ("ветер", "город"),
        ("утро", "свет"),
        ("ветер", "вечер"),
        ("порядке", "банке"),
        ("дом", "кот"),
    ],
)
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
    path.write_text("[Куплет]\nя иду в дом\nи несу свой лом\nкрасный шар (эй)\n", encoding="utf-8")
    assert main(["lyrics", "stats", str(path)]) == 0
    rows = [r.split() for r in capsys.readouterr().out.splitlines() if r.strip()[:1].isdigit()]
    assert [(r[0], r[1]) for r in rows] == [("4", "A"), ("5", "A"), ("3", "B")]


def test_cli_stats_without_the_file_exits_2(tmp_path):
    assert main(["lyrics", "stats", str(tmp_path / "none.txt")]) == 2


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("д+ом", "л+ом", True),
        ("д+ом", "к+от", False),
        ("д+ом", "с+ыр", False),
        ("в+етер", "в+ечер", False),
        ("г+ород", "х+олод", False),
        ("п+альчик", "м+альчик", True),
        ("з+емлю", "вн+емлю", True),
        ("+акции", "симул+яции", True),
        ("р+анке", "л+апы", False),
        ("кур+ил", "спал+ил", True),
        ("сапф+ир", "насып+ал", False),
        ("+эй", "карть+е", False),
        ("мук+а", "рек+а", True),
        ("мук+а", "м+ука", False),
        ("р+ука", "мук+а", False),
        ("д+ом", "д+ома", False),
        ("в+ыл", "в+ил", True),
        ("моё", "чё", True),
    ],
)
def test_rhymes_with_known_stress(a, b, expected):
    assert rhymes(a, b) is expected


def test_last_word_keeps_its_stress_mark():
    from redline.lyrics.rhyme import last_word

    assert last_word("я ид+у дом+ой (эй)") == "дом+ой"
    assert last_word("...") == ""


def test_double_vowels_are_kept_and_stretches_collapse():
    assert rhymes("+акции", "симул+яции")
    assert not rhymes("+акции", "п+арка")
    assert rhymes("мозг+и", "ми-и-ис") is False
    assert rhymes("пр+ис", "ми-и-ис")


def test_the_owners_capital_stress_wins_over_a_model_mark():
    assert rhymes("м+агиЮ", "фр+анциЮ")


def test_a_capital_first_letter_is_not_a_stress():
    # with the capital taken as the stress these two would rhyme
    assert not rhymes("Улица", "п+улями")
    assert rhymes("ул+ица", "т+упица") is False
    assert rhymes("ул+ица", "дев+ица")


def stats_rows(capsys):
    return [r.split() for r in capsys.readouterr().out.splitlines() if r.strip()[:1].isdigit()]


def test_cli_stats_uses_stress_marks_when_asked(tmp_path, capsys, monkeypatch):
    path = tmp_path / "text.txt"
    path.write_text("[Куплет]\nу меня деньги\nв голове мозги\n", encoding="utf-8")
    assert main(["lyrics", "stats", str(path)]) == 0
    assert [r[1] for r in stats_rows(capsys)] == ["A", "B"]
    marks = {"у меня деньги": "у мен+я деньг+и", "в голове мозги": "в голов+е мозг+и"}
    monkeypatch.setattr("redline.lyrics.stress.load_stresser", lambda: marks.__getitem__)
    assert main(["lyrics", "stats", str(path), "--stress"]) == 0
    assert [r[1] for r in stats_rows(capsys)] == ["A", "A"]


def test_cli_stats_without_the_stress_extra_exits_2(tmp_path, monkeypatch):
    from redline.lyrics.parse import LyricsError

    def missing():
        raise LyricsError("stress marks need the stress extra")

    path = tmp_path / "text.txt"
    path.write_text("строка\n", encoding="utf-8")
    monkeypatch.setattr("redline.lyrics.stress.load_stresser", missing)
    assert main(["lyrics", "stats", str(path), "--stress"]) == 2
