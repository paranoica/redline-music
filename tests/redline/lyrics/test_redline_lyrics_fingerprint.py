import numpy as np
import pytest
from redline.cli import main
from redline.lyrics.base.fingerprint import FingerprintIndex, new_key
from redline.lyrics.text.parse import LyricsError, parse_text

BASE = {
    "a/one.txt": (
        "a",
        "[Куплет]\n"
        "мы гуляем по крышам пока город молчит\n"
        "и холодные фонари горят над рекой\n"
        "(мы горим в огне холодных синих фонарей ночью)\n"
        "эй эй эй эй эй эй эй эй эй\n",
    ),
    "b/two.txt": ("b", "[Припев]\nзолотые цепи звенят на шее у меня опять\n"),
    "c/three.txt": ("c", "[Куплет]\nя на районе снова до утра\n"),
}


def base_tracks():
    return [(artist, parse_text(text)) for artist, text in BASE.values()]


@pytest.fixture
def index():
    return FingerprintIndex.build(base_tracks(), new_key())


def found(index, text, **kwargs):
    return index.check(parse_text(text), **kwargs)


def test_eight_words_in_a_row_from_one_text_are_a_copy(index):
    matches = found(index, "[Куплет]\nвот мы гуляем по крышам пока город молчит и дальше\n")
    assert [m.words for m in matches] == [8]
    assert matches[0].first_line == matches[0].last_line == 1


def test_seven_words_in_a_row_are_not(index):
    assert found(index, "вот мы гуляем по крышам пока город молчит другой\n") == []


def test_a_whole_short_line_of_the_base_is_not_a_copy(index):
    assert found(index, "я на районе снова до утра\n") == []
    assert found(index, "вчера я на районе снова до утра с братвой\n") == []


def test_runs_from_two_texts_back_to_back_are_not_a_copy(index):
    # a run of one text, then a run of another
    text = "гуляем по крышам пока город звенят на шее у меня опять\n"
    assert found(index, text) == []


def test_a_copy_over_a_line_break_and_an_inserted_header_is_found(index):
    text = "мы гуляем по крышам\n[Припев]\nпока город молчит и холодные\n"
    matches = found(index, text)
    assert len(matches) == 1
    assert (matches[0].first_line, matches[0].last_line) == (1, 2)


def test_words_in_parentheses_are_checked_both_ways(index):
    assert found(index, "мы горим в огне холодных синих фонарей ночью\n")
    assert found(index, "привет (золотые цепи звенят на шее у меня опять)\n")


def test_case_punctuation_and_yo_do_not_hide_a_copy(index):
    assert found(index, "МЫ гуляем, по крышам — пока ГОРОД молчит!!! И\n")


def test_interjection_chains_are_never_a_copy(index):
    assert found(index, "эй эй эй эй эй эй эй эй эй эй\n") == []


def test_threshold_is_a_setting_down_to_the_run_length(index):
    text = "вот мы гуляем по крышам пока город молчит другой\n"
    assert found(index, text, min_words=7)
    assert found(index, text, min_words=5)
    with pytest.raises(ValueError, match="5"):
        found(index, text, min_words=4)


def test_save_and_load_keep_the_answers(index, tmp_path):
    index.save(tmp_path)
    loaded = FingerprintIndex.load(tmp_path)
    text = "[Куплет]\nвот мы гуляем по крышам пока город молчит и дальше\n"
    assert [m.words for m in loaded.check(parse_text(text))] == [8]
    assert loaded.n_texts == 3


def test_a_missing_index_is_refused(tmp_path):
    with pytest.raises(LyricsError, match="lyrics index"):
        FingerprintIndex.load(tmp_path)


def test_another_key_is_refused(index, tmp_path):
    index.save(tmp_path)
    (tmp_path / "key").write_bytes(new_key())
    with pytest.raises(LyricsError, match="key"):
        FingerprintIndex.load(tmp_path)


def test_an_index_of_another_version_is_refused(index, tmp_path, monkeypatch):
    index.save(tmp_path)
    monkeypatch.setattr("redline.lyrics.base.fingerprint.VERSION", 99)
    with pytest.raises(LyricsError, match="version"):
        FingerprintIndex.load(tmp_path)


def test_an_empty_index_is_refused(tmp_path):
    with pytest.raises(LyricsError, match="no texts"):
        FingerprintIndex.build([], new_key())


def test_a_smaller_rebuild_does_not_replace_the_index_unless_forced(index, tmp_path):
    index.save(tmp_path)
    key = (tmp_path / "key").read_bytes()
    smaller = FingerprintIndex.build(base_tracks()[:1], key)
    with pytest.raises(LyricsError, match="3 texts"):
        smaller.save(tmp_path)
    assert FingerprintIndex.load(tmp_path).n_texts == 3
    smaller.save(tmp_path, force=True)
    assert FingerprintIndex.load(tmp_path).n_texts == 1


def test_digests_depend_on_the_key():
    first = FingerprintIndex.build(base_tracks(), new_key())
    second = FingerprintIndex.build(base_tracks(), new_key())
    assert not np.intersect1d(first.digests, second.digests).size


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_check_exit_codes(index, tmp_path, capsys):
    build = tmp_path / "build"
    build.mkdir()
    index.save(build)
    copy = write(tmp_path, "copy.txt", "вот мы гуляем по крышам пока город молчит и дальше\n")
    clean = write(tmp_path, "clean.txt", "я на районе снова до утра\n")
    assert main(["lyrics", "check", str(copy), "--build", str(build)]) == 1
    out = capsys.readouterr().out
    assert "вот мы гуляем" in out and "8" in out
    assert main(["lyrics", "check", str(clean), "--build", str(build)]) == 0
    assert main(["lyrics", "check", str(clean), "--build", str(tmp_path / "none")]) == 2
    assert main(["lyrics", "check", str(tmp_path / "nofile.txt"), "--build", str(build)]) == 2


@pytest.mark.parametrize("key", [b"", b"short"])
def test_a_key_of_the_wrong_length_is_refused(key):
    # an empty key turns the keyed hash into a plain one, open to a word-list attack
    with pytest.raises(LyricsError, match="key"):
        FingerprintIndex.build(base_tracks(), key)


def test_the_key_is_readable_by_the_owner_only(index, tmp_path):
    index.save(tmp_path)
    assert (tmp_path / "key").stat().st_mode & 0o077 == 0


def test_runs_from_different_places_of_one_text_do_not_chain():
    base = (
        "мы летим над городом ночью\n"
        "звезды летим над городом ночью одни\n"
        "видим над городом ночью одни сегодня\n"
        "и снова городом ночью одни сегодня тихо\n"
    )
    index = FingerprintIndex.build([("a", parse_text(base))], new_key())
    assert found(index, "мы летим над городом ночью одни сегодня тихо\n") == []


def test_an_interjection_chain_inside_a_copy_does_not_split_it():
    base = "кот идет домой ла ла ла ла по мокрой улице\n"
    index = FingerprintIndex.build([("a", parse_text(base))], new_key())
    assert [m.words for m in found(index, base)] == [10]


@pytest.mark.parametrize("content", [b"", b"not a zip at all"])
def test_a_broken_index_file_is_refused(index, tmp_path, content):
    index.save(tmp_path)
    (tmp_path / "fingerprints.npz").write_bytes(content)
    with pytest.raises(LyricsError, match="lyrics index"):
        FingerprintIndex.load(tmp_path)


def test_an_index_with_unsorted_digests_is_refused(index, tmp_path):
    index.save(tmp_path)
    data = dict(np.load(tmp_path / "fingerprints.npz"))
    data["digests"] = data["digests"][::-1].copy()
    np.savez(tmp_path / "fingerprints.npz", **data)
    with pytest.raises(LyricsError, match="lyrics index"):
        FingerprintIndex.load(tmp_path)


def test_an_unreadable_index_is_not_replaced_without_force(index, tmp_path):
    index.save(tmp_path)
    (tmp_path / "key").write_bytes(new_key())
    smaller = FingerprintIndex.build(base_tracks()[:1], new_key())
    with pytest.raises(LyricsError, match="force"):
        smaller.save(tmp_path)
    smaller.save(tmp_path, force=True)


def test_cli_check_reads_a_leading_dash_block_as_text(index, tmp_path):
    build = tmp_path / "build"
    index.save(build)
    text = "---\nвот мы гуляем по крышам пока город молчит и дальше\n---\nмое\n"
    path = tmp_path / "candidate.txt"
    path.write_text(text, encoding="utf-8")
    assert main(["lyrics", "check", str(path), "--build", str(build)]) == 1


def test_cli_check_with_a_broken_index_exits_2(index, tmp_path):
    build = tmp_path / "build"
    index.save(build)
    (build / "fingerprints.npz").write_bytes(b"")
    path = tmp_path / "candidate.txt"
    path.write_text("строка\n", encoding="utf-8")
    assert main(["lyrics", "check", str(path), "--build", str(build)]) == 2


def test_check_can_leave_out_some_artists(index):
    text = "[Куплет]\nвот мы гуляем по крышам пока город молчит и дальше\n"
    assert found(index, text)
    assert found(index, text, ignore=("a",)) == []
    assert found(index, text, ignore=("b",))
