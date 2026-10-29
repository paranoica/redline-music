import pytest
from redline.lyrics.text.normalize import words
from redline.lyrics.text.parse import LyricsError, parse_text, read_artists, read_track

GENIUS = """---
artist: "Some Artist"
track: "made up"
feat: ["guest one"]
genius_id: 1
---
[Текст песни «made up»]
[Интро]
холодный ветер (эй)

[Куплет 1: Some Artist]
мы гуляем по крышам до утра
(вся толпа)
[Припев: Some Artist & Guest One] луна горит
луна горит над нами
[Куплет 2: Guest One]
мой город спит
[Проигрыш]
тишина
[Пост-припев]
снова
"""

MINE = """---
artist: "me"
track: "invented"
---
[Intro]
[Slow heavy beat]
(раз раз)

[Hook]
[Hypnotic chant]
стекло и дым
стекло и дым

[Verse 1: Fast dark flow]
я рисую мелом на стене
[Bass Drop]
ха-ха

[Hook]

[End]
"""


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def test_front_matter_is_read(tmp_path):
    track = read_track(write(tmp_path, "a.txt", GENIUS))
    assert track.meta["artist"] == "Some Artist"
    assert track.meta["feat"] == ["guest one"]


@pytest.mark.parametrize(
    "text", ["[Куплет]\nстрока\n", "---\ntrack: x\n---\nстрока\n", "---\nartist: x\nстрока\n", ""]
)
def test_strict_reading_refuses_a_file_without_front_matter_naming_it(tmp_path, text):
    path = write(tmp_path, "broken.txt", text)
    with pytest.raises(LyricsError, match=r"broken\.txt"):
        read_track(path)


def test_lenient_parsing_takes_a_bare_text():
    track = parse_text("[Куплет]\nпервая строка\nвторая строка\n")
    assert [line.text for line in track.sections[0].lines] == ["первая строка", "вторая строка"]


def test_genius_headers_map_to_kinds_and_the_title_line_is_dropped(tmp_path):
    track = read_track(write(tmp_path, "a.txt", GENIUS))
    assert [s.kind for s in track.sections] == [
        "intro",
        "verse",
        "hook",
        "verse",
        "other",
        "other",
    ]
    assert track.sections[1].who == "Some Artist"
    assert track.sections[1].name == "Куплет"


def test_text_after_a_header_bracket_is_a_line(tmp_path):
    hook = read_track(write(tmp_path, "a.txt", GENIUS)).sections[2]
    assert [line.text for line in hook.lines] == ["луна горит", "луна горит над нами"]


def test_adlibs_are_split_from_the_line(tmp_path):
    track = read_track(write(tmp_path, "a.txt", GENIUS))
    line = track.sections[0].lines[0]
    assert (line.text, line.plain, line.adlibs) == (
        "холодный ветер (эй)",
        "холодный ветер",
        ("эй",),
    )
    only = track.sections[1].lines[1]
    assert (only.plain, only.adlibs) == ("", ("вся толпа",))


def test_a_file_without_headers_is_one_other_section(tmp_path):
    text = '---\nartist: "a"\ntrack: "b"\n---\nодна строка\nдругая строка\n'
    track = read_track(write(tmp_path, "a.txt", text))
    assert track.headerless
    assert [s.kind for s in track.sections] == ["other"]
    assert len(track.sections[0].lines) == 2


def test_owner_notes_are_not_text_and_a_suffix_is_not_a_performer(tmp_path):
    track = read_track(write(tmp_path, "mine.txt", MINE), mine=True)
    assert [s.kind for s in track.sections] == ["intro", "hook", "verse", "hook"]
    texts = [line.text for s in track.sections for line in s.lines]
    assert not any("beat" in t or "chant" in t or "End" in t for t in texts)
    verse = track.sections[2]
    assert verse.who == ""
    assert [line.text for line in verse.lines] == ["я рисую мелом на стене", "ха-ха"]


def test_owner_empty_header_repeats_the_earlier_section(tmp_path):
    track = read_track(write(tmp_path, "mine.txt", MINE), mine=True)
    assert [line.text for line in track.sections[3].lines] == ["стекло и дым", "стекло и дым"]


def test_an_empty_header_without_an_earlier_section_stays_empty(tmp_path):
    text = '---\nartist: "me"\ntrack: "x"\n---\n[Hook]\n\n[Verse]\nстрока\n'
    track = read_track(write(tmp_path, "mine.txt", text), mine=True)
    assert [len(s.lines) for s in track.sections] == [0, 1]


def test_crlf_and_bom_are_read(tmp_path):
    path = write(tmp_path, "a.txt", "\ufeff" + GENIUS.replace("\n", "\r\n"))
    track = read_track(path)
    assert track.meta["track"] == "made up"
    assert track.sections[0].lines[0].plain == "холодный ветер"


def test_artists_map_reads_flow_mappings(tmp_path):
    path = write(
        tmp_path,
        "artists.yaml",
        "# comment\n"
        "some-artist: {name: Some Artist, aka: [S A], profile: A}\n"
        "other: {name: O, profile: B}\n",
    )
    artists = read_artists(path)
    assert artists["some-artist"].names == ("Some Artist", "S A")
    assert artists["other"].profile == "B"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Луна ГОРИТ!", ["луна", "горит"]),
        ("ёлка и еще", ["елка", "и", "еще"]),
        ("мой гoрод", ["мой", "город"]),  # Latin o inside a Cyrillic word
        ("don't stop", ["dont", "stop"]),
        ("сине-голубой — день", ["сине", "голубой", "день"]),
        ("даааа нееет", ["да", "нет"]),
        ("100 000 причин", ["100000", "причин"]),
        ("  ,,, ", []),
        ("", []),
    ],
)
def test_words_normalize_case_letters_and_punctuation(text, expected):
    assert words(text) == expected


@pytest.mark.parametrize(
    "text", ["---\nartist: [unclosed\n---\nстрока\n", "---\nчужие слова тут\n---\nстрока\n"]
)
def test_a_leading_block_that_is_not_front_matter_is_read_as_text(text):
    track = parse_text(text)
    lines = [line.text for s in track.sections for line in s.lines]
    assert "строка" in lines and len(lines) == 4


def test_owner_note_followed_by_words_keeps_the_words(tmp_path):
    text = '---\nartist: "me"\ntrack: "x"\n---\n[Verse]\n[шепотом] я иду домой один\n'
    track = read_track(write(tmp_path, "mine.txt", text), mine=True)
    assert [line.text for line in track.sections[0].lines] == ["я иду домой один"]


def test_a_part_header_is_a_verse():
    assert parse_text("[Часть 2]\nстрока\n").sections[0].kind == "verse"


def test_a_yaml_error_names_the_file_without_quoting_it(tmp_path):
    path = write(tmp_path, "a.txt", '---\nartist: "Secret Name\ntrack: x\n---\nстрока\n')
    with pytest.raises(LyricsError) as error:
        read_track(path)
    assert "a.txt" in str(error.value) and "Secret" not in str(error.value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("мои\u0306 город", ["мой", "город"]),  # й written as и plus a combining breve
        ("го\u200bрод", ["город"]),
        ("го\u00adрод", ["город"]),
    ],
)
def test_words_fold_unicode_forms_and_invisible_characters(text, expected):
    assert words(text) == expected


def test_accented_latin_letters_lose_their_accents_and_cyrillic_keeps_its_letters():
    assert words("Enfants Riches Déprimés, Garçons") == ["enfants", "riches", "deprimes", "garcons"]
    assert words("мой йод") == ["мой", "йод"]
