import pytest
from redline.lyrics.write.title import TitlePlan, check_title, split_title, title_request
from redline.lyrics.write.worn import worn_problems, worn_words

TEXT = "[Куплет]\nсиний свет на мне\nи снова ночь\n"


def test_the_title_line_is_split_off_the_text():
    assert split_title(f"Title: ICE AGE\n{TEXT}") == ("ICE AGE", TEXT)
    assert split_title(f"  title:   лёд  \n\n{TEXT}") == ("лёд", TEXT)
    assert split_title(TEXT) == (None, TEXT)
    assert split_title(f"{TEXT}Title: late\n") == (None, f"{TEXT}Title: late\n")
    assert split_title("Title: \n" + TEXT) == (None, TEXT)


@pytest.mark.parametrize(
    ("title", "plan"),
    [
        ("ICE AGE", TitlePlan(2, "latin", "caps", False)),
        ("cold winter", TitlePlan(2, "latin", "lower", False)),
        ("Лёд", TitlePlan(1, "cyrillic", "any", False)),
        ("6:66 AM", TitlePlan(2, "latin", "caps", True)),
        ("4SEASONS", TitlePlan(1, "latin", "caps", True)),
        ("ALL FINE", TitlePlan(1, "latin", "any", False)),
    ],
)
def test_a_title_of_the_drawn_shape_passes(title, plan):
    assert check_title(title, plan, TEXT) == []


@pytest.mark.parametrize(
    ("title", "plan", "kind"),
    [
        (None, TitlePlan(1, "latin", "any", False), "missing"),
        ("ICE AGE FOREVER NOW", TitlePlan(1, "latin", "any", False), "words"),
        ("лёд", TitlePlan(1, "latin", "any", False), "script"),
        ("ICE", TitlePlan(1, "cyrillic", "any", False), "script"),
        ("Ice Age", TitlePlan(2, "latin", "caps", False), "case"),
        ("ICE AGE", TitlePlan(2, "latin", "lower", False), "case"),
        ("ICE AGE", TitlePlan(2, "latin", "caps", True), "digits"),
        ("Синий свет", TitlePlan(2, "cyrillic", "any", False), "hook"),
    ],
)
def test_a_title_off_the_drawn_shape_is_named(title, plan, kind):
    found = check_title(title, plan, TEXT)
    assert found and found[0].kind == "title" and kind in found[0].where


def test_the_request_spells_the_drawn_shape_out():
    asked = title_request(TitlePlan(2, "latin", "caps", True))
    assert "2 words" in asked and "Latin" in asked and "capitals" in asked and "digit" in asked
    assert "Title:" in asked
    one = title_request(TitlePlan(1, "cyrillic", "lower", False))
    assert "1 word" in one and "Cyrillic" in one and "lower case" in one and "digit" not in one


def saved(folder, seed, text, age):
    import os

    path = folder / str(seed) / "text.txt"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (age, age))


def test_words_in_four_of_the_last_texts_are_worn_plain_words_never(tmp_path):
    for seed, text in enumerate(
        [
            "[Куплет]\nстакан полный, синий свет, снова\n",
            "[Куплет]\nв стакане лёд, синий свет, снова\n",
            "[Куплет]\nстаканы бьются, синий свет, снова\n",
            "[Куплет]\nстакан и синий, снова\n",
            "[Куплет]\nпросто ночь\n",
        ]
    ):
        saved(tmp_path, seed, text, 1000 + seed)
    # свет is in three texts only; снова is grammar, not a habit
    assert worn_words(tmp_path) == ("синий", "стакан")
    # the seed being written does not count against itself
    assert worn_words(tmp_path, skip=3) == ()


def test_only_the_most_recent_texts_count(tmp_path):
    for seed in range(3):
        saved(tmp_path, seed, "[Куплет]\nстарый мотив\n", 100 + seed)
    for seed in range(3, 18):
        saved(tmp_path, seed, f"[Куплет]\nновый текст {seed}\n", 1000 + seed)
    assert "мотив" not in worn_words(tmp_path)
    assert worn_words(tmp_path / "none") == ()


def test_a_draft_leaning_on_worn_words_is_sent_back_naming_them():
    worn = ("стакан", "синий", "заны", "мама", "мент")
    three = "[Куплет]\nсиний стакан и заны\n"
    four = "[Куплет]\nсиний стакане и заны, мама\n"
    assert worn_problems(three, worn) == []
    found = worn_problems(four, worn)
    assert len(found) == 1 and found[0].kind == "worn"
    assert all(w in found[0].detail for w in ("синий", "стакан", "заны", "мама"))
    assert worn_problems(four, ()) == []
