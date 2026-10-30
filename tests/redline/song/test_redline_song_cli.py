import json

import pytest
from redline.cli import main
from redline.song import model


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    return base


def test_new_writes_the_song_and_show_prints_it(root, capsys):
    assert main(["song", "new", "demo", "--beat-seed", "5", "--ref", "input/refs/a.wav"]) == 0
    capsys.readouterr()
    assert main(["song", "show", "demo"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["beat"] == {"own": 5}
    assert shown["voice"] == {"takes": "input/takes/demo", "chosen": None}
    assert shown["references"] == ["input/refs/a.wav"]
    assert shown["text"] is None and shown["rev"] == 1
    assert (root / "private" / "songs" / "demo" / "song.json").is_file()


def test_new_with_a_beat_file_and_suno_stores_relative_paths(root, capsys):
    absolute = root / "input" / "beat.wav"
    assert main(["song", "new", "t", "--beat", str(absolute), "--suno", "input/s.wav"]) == 0
    data = model.load("t").to_json()
    assert data["beat"] == {"file": "input/beat.wav"}
    assert data["voice"] == {"suno": "input/s.wav"}


def test_list_prints_names_sorted_and_nothing_when_empty(root, capsys):
    assert main(["song", "list"]) == 0
    assert capsys.readouterr().out == ""
    for name in ("b", "a"):
        assert main(["song", "new", name, "--beat-seed", "1"]) == 0
    capsys.readouterr()
    assert main(["song", "list"]) == 0
    assert capsys.readouterr().out.split() == ["a", "b"]


def test_both_beat_kinds_or_neither_are_refused(root, capsys):
    with pytest.raises(SystemExit) as both:
        main(["song", "new", "x", "--beat-seed", "1", "--beat", "input/b.wav"])
    assert both.value.code == 2
    with pytest.raises(SystemExit) as neither:
        main(["song", "new", "x"])
    assert neither.value.code == 2
    assert model.list_songs() == []


@pytest.mark.parametrize(
    "extra",
    [
        ["--beat", "../b.wav"],
        ["--beat", "/etc/passwd"],
        ["--beat", "apps/x.wav"],
        ["--beat-seed", "1", "--suno", "../s.wav"],
        ["--beat-seed", "1", "--ref", "src/a.wav"],
    ],
)
def test_new_refuses_paths_outside_the_folders(root, capsys, extra):
    assert main(["song", "new", "x", *extra]) == 2
    assert "redline:" in capsys.readouterr().err
    assert model.list_songs() == []


def test_new_refuses_a_bad_name_and_an_existing_song(root, capsys):
    assert main(["song", "new", "a/b", "--beat-seed", "1"]) == 2
    assert main(["song", "new", "x", "--beat-seed", "1"]) == 0
    assert main(["song", "new", "x", "--beat-seed", "2"]) == 2
    assert "exists already" in capsys.readouterr().err
    assert model.load("x").beat == {"own": 1}


def test_show_of_a_missing_song_is_an_error(root, capsys):
    assert main(["song", "show", "nope"]) == 2
    assert "no such song" in capsys.readouterr().err


def test_song_alone_prints_help(root, capsys):
    assert main(["song"]) == 0
    assert "song" in capsys.readouterr().out
