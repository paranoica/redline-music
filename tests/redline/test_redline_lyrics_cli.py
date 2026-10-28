import json

import pytest

from redline.cli import main
from redline.lyrics.fingerprint import FingerprintIndex

COPIED = "пустые улицы молчат и падает вода на крыши"
OWN = "часы на кухне не стучат и нет уже следа"


def write_track(path, artist, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nartist: "{artist}"\ntrack: "{path.stem}"\n---\n{body}', encoding="utf-8")


@pytest.fixture
def lyrics(tmp_path):
    root = tmp_path / "lyrics"
    others = root / "others"
    others.mkdir(parents=True)
    (others / "artists.yaml").write_text(
        "a: {name: Alpha, profile: A}\nb: {name: Beta, profile: A}\n", encoding="utf-8"
    )
    for i in range(5):
        write_track(others / "a" / f"t{i}.txt", "Alpha", f"[Куплет]\n{COPIED} номер {i}\n")
        write_track(others / "b" / f"t{i}.txt", "Beta", f"[Припев]\nсвет и тень {i}\n")
    write_track(root / "mine" / "own.txt", "me", f"[Hook]\n{OWN}\n")
    return root


def index(lyrics, build, *extra):
    return main(["lyrics", "index", "--lyrics", str(lyrics), "--build", str(build), *extra])


def check(tmp_path, build, text):
    path = tmp_path / "candidate.txt"
    path.write_text(text, encoding="utf-8")
    return main(["lyrics", "check", str(path), "--build", str(build)])


def test_index_writes_the_digests_and_the_profiles(lyrics, tmp_path):
    build = tmp_path / "build"
    assert index(lyrics, build) == 0
    assert (build / "fingerprints.npz").exists() and (build / "key").exists()
    names = sorted(p.stem for p in (build / "profiles").glob("*.json"))
    assert names == ["artist-a", "artist-b", "group-A", "mine", "scene", "working"]
    scene = json.loads((build / "profiles" / "scene.json").read_text(encoding="utf-8"))
    assert scene["meta"]["artists"] == 2
    assert FingerprintIndex.load(build).n_texts == 11


def test_an_indexed_text_and_the_owners_own_are_caught(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    assert check(tmp_path, build, f"новое начало {COPIED}\n") == 1
    assert check(tmp_path, build, f"{OWN}\n") == 1
    assert check(tmp_path, build, "совсем другие слова в этой строке живут\n") == 0


def test_the_owners_texts_can_be_left_out_of_the_check(lyrics, tmp_path):
    build = tmp_path / "build"
    assert index(lyrics, build, "--without-mine") == 0
    assert check(tmp_path, build, f"{OWN}\n") == 0


def test_a_rebuild_keeps_the_key(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    key = (build / "key").read_bytes()
    assert index(lyrics, build) == 0
    assert (build / "key").read_bytes() == key


def test_a_broken_file_stops_the_index_naming_it(lyrics, tmp_path, capsys):
    (lyrics / "others" / "a" / "bad.txt").write_text("[Куплет]\nбез шапки\n", encoding="utf-8")
    assert index(lyrics, tmp_path / "build") == 2
    assert "bad.txt" in capsys.readouterr().err
    assert not (tmp_path / "build" / "fingerprints.npz").exists()


def test_a_smaller_rebuild_needs_force(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    for path in (lyrics / "others" / "b").glob("*.txt"):
        path.unlink()
    assert index(lyrics, build) == 2
    assert index(lyrics, build, "--force") == 0


def test_a_missing_lyrics_folder_exits_2(tmp_path):
    assert index(tmp_path / "none", tmp_path / "build") == 2


def test_index_with_stress_marks_line_final_words_once_each(lyrics, tmp_path, monkeypatch):
    calls = []

    def mark(text):
        calls.append(text)
        return text

    monkeypatch.setattr("redline.lyrics.stress.load_stresser", lambda: mark)
    assert index(lyrics, tmp_path / "build", "--stress") == 0
    assert calls and all(" " not in c for c in calls)
    assert len(calls) == len(set(calls))


def test_index_refuses_a_build_folder_git_would_track(lyrics, tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("/private\n", encoding="utf-8")
    assert index(lyrics, repo / "build") == 2
    assert not (repo / "build").exists()
    assert index(lyrics, repo / "private" / "build") == 0


def test_a_key_file_of_the_wrong_length_stops_the_index(lyrics, tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "key").write_bytes(b"")
    assert index(lyrics, build) == 2


def test_profiles_of_removed_artists_are_deleted(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    for path in (lyrics / "others" / "b").glob("*.txt"):
        path.unlink()
    assert index(lyrics, build, "--force") == 0
    assert not (build / "profiles" / "artist-b.json").exists()


def test_index_builds_the_target_mix_from_a_private_file(lyrics, tmp_path):
    (lyrics / "mix.yaml").write_text("artist-a: 0.5\nmine: 0.25\nscene: 0.25\n", encoding="utf-8")
    build = tmp_path / "build"
    assert index(lyrics, build) == 0
    target = json.loads((build / "profiles" / "target.json").read_text(encoding="utf-8"))
    assert target["meta"]["weights"] == {"artist-a": 0.5, "mine": 0.25, "scene": 0.25}
    assert "пустые" in {w["word"] for w in target["vocabulary"]["top"]}


def test_a_mix_naming_an_unknown_profile_stops_the_index(lyrics, tmp_path):
    (lyrics / "mix.yaml").write_text("artist-zzz: 1.0\n", encoding="utf-8")
    assert index(lyrics, tmp_path / "build") == 2


def test_without_a_mix_file_there_is_no_target(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    assert not (build / "profiles" / "target.json").exists()


def test_generated_texts_join_the_copy_check_but_not_the_profiles(lyrics, tmp_path):
    build = tmp_path / "build"
    index(lyrics, build)
    before = {p.name: p.read_bytes() for p in (build / "profiles").glob("*.json")}
    made = "новый текст про ветер который дует сквозь пустые окна ночью"
    path = lyrics / "generated" / "1" / "text.txt"
    path.parent.mkdir(parents=True)
    path.write_text(f"[Куплет]\n{made}\n", encoding="utf-8")
    assert index(lyrics, build) == 0
    assert check(tmp_path, build, f"{made}\n") == 1
    after = {p.name: p.read_bytes() for p in (build / "profiles").glob("*.json")}
    assert after == before


def test_profiles_record_whether_stress_marks_were_used(lyrics, tmp_path, monkeypatch):
    build = tmp_path / "build"
    index(lyrics, build)
    plain = json.loads((build / "profiles" / "scene.json").read_text(encoding="utf-8"))
    monkeypatch.setattr("redline.lyrics.stress.load_stresser", lambda: str)
    index(lyrics, build, "--stress")
    marked = json.loads((build / "profiles" / "scene.json").read_text(encoding="utf-8"))
    assert (plain["meta"]["stress"], marked["meta"]["stress"]) == (False, True)


def test_a_private_names_file_keeps_more_names_out_of_profiles(lyrics, tmp_path):
    for path in (lyrics / "others" / "a").glob("*.txt"):
        path.write_text(path.read_text(encoding="utf-8") + "(producertag)\n", encoding="utf-8")
    build = tmp_path / "build"
    index(lyrics, build)
    scene = (build / "profiles" / "artist-a.json").read_text(encoding="utf-8")
    assert "producertag" in scene
    (lyrics / "names.txt").write_text("# producers\nProducerTag\n", encoding="utf-8")
    index(lyrics, build)
    assert "producertag" not in (build / "profiles" / "artist-a.json").read_text(encoding="utf-8")
