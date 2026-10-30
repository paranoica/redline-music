import json
import os
import stat

import pytest
from redline.core.private import make_private_dir
from redline.song import model


@pytest.fixture
def root(tmp_path, monkeypatch):
    base = tmp_path / "repo"
    for top in model.ALLOWED:
        (base / top).mkdir(parents=True)
    monkeypatch.setattr(model, "ROOT", base)
    return base


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def sample(name="demo"):
    return model.new_song(name, {"own": 7}, references=("input/refs/a.wav",))


def doc(**changes):
    data = sample().to_json()
    data.update(changes)
    return data


def test_round_trip_keeps_every_field():
    song = model.parse(
        doc(
            beat={"file": "work/beats/b.wav"},
            text={"seed": 3, "version": 2},
            voice={"takes": "input/takes/demo", "chosen": "take-02.wav"},
            mix={"settings": "private/songs/demo/mix.json", "variant": 1},
            rev=4,
        )
    )
    assert model.loads(json.dumps(song.to_json())) == song
    assert song.beat == {"file": "work/beats/b.wav"}
    assert song.text == {"seed": 3, "version": 2}
    assert song.voice["chosen"] == "take-02.wav"
    assert song.rev == 4


def test_new_song_defaults_to_the_takes_folder_of_its_name():
    song = sample("night")
    assert song.voice == {"takes": "input/takes/night", "chosen": None}
    assert song.text is None and song.rev == 0
    assert model.new_song("n", {"own": 1}, suno="input/s.wav").voice == {"suno": "input/s.wav"}


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        ({"extra": 1}, "song.extra"),
        ({"name": "a/b"}, "name"),
        ({"name": ".."}, "name"),
        ({"name": " "}, "name"),
        ({"name": "x\\y"}, "name"),
        ({"beat": {"own": 1, "file": "input/a.wav"}}, "beat"),
        ({"beat": {}}, "beat"),
        ({"beat": {"own": -1}}, "beat.own"),
        ({"beat": {"own": True}}, "beat.own"),
        ({"beat": {"file": "../a.wav"}}, "beat.file"),
        ({"beat": {"file": "/etc/passwd"}}, "beat.file"),
        ({"beat": {"file": "apps/redline/x.py"}}, "beat.file"),
        ({"text": {"seed": 1}}, "text.version"),
        ({"text": {"seed": 1, "version": 1, "x": 0}}, "text.x"),
        ({"voice": {"takes": "input/t", "chosen": "../x.wav"}}, "voice.chosen"),
        ({"voice": {"takes": "input/t", "chosen": None, "suno": "input/s"}}, "voice.takes"),
        ({"voice": {"takes": "/abs", "chosen": None}}, "voice.takes"),
        ({"references": ["input/a", "work/../../b"]}, "references[1]"),
        ({"references": "input/a"}, "references"),
        ({"mix": {"settings": None}}, "mix.variant"),
        ({"mix": {"settings": None, "variant": "1"}}, "mix.variant"),
        ({"rev": -1}, "rev"),
        ({"format": 2}, "format"),
    ],
)
def test_parse_refuses_with_the_path_of_the_problem(changes, where):
    with pytest.raises(model.SongError) as caught:
        model.parse(doc(**changes))
    assert str(caught.value).startswith(where)


def test_a_missing_field_and_non_json_are_refused():
    data = doc()
    del data["mix"]
    with pytest.raises(model.SongError, match=r"song\.mix: missing"):
        model.parse(data)
    with pytest.raises(model.SongError, match="not JSON"):
        model.loads("{")
    with pytest.raises(model.SongError, match="song: must be an object"):
        model.loads("[]")


def test_resolve_inside_accepts_the_four_folders(root):
    for top in model.ALLOWED:
        assert model.resolve_inside(f"{top}/a/b.wav") == root.resolve() / top / "a" / "b.wav"


@pytest.mark.parametrize(
    "bad",
    [
        "../x",
        "input/../../x",
        "input/../apps/redline/x.py",
        "/etc/passwd",
        "apps/redline/x.py",
        "",
        ".",
        "README.md",
    ],
)
def test_resolve_inside_refuses_escapes(root, bad):
    with pytest.raises(model.SongError):
        model.resolve_inside(bad)


def test_resolve_inside_accepts_a_folder_itself(root):
    assert model.resolve_inside("input") == root.resolve() / "input"


def test_resolve_inside_refuses_a_symlink_leading_out(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.wav").write_bytes(b"")
    (root / "input" / "link").symlink_to(outside)
    (root / "input" / "file-link.wav").symlink_to(outside / "a.wav")
    with pytest.raises(model.SongError, match="outside"):
        model.resolve_inside("input/link/a.wav")
    with pytest.raises(model.SongError, match="outside"):
        model.resolve_inside("input/file-link.wav")


def test_resolve_inside_accepts_a_symlink_that_stays_in(root):
    (root / "work" / "real").mkdir()
    (root / "input" / "link").symlink_to(root / "input" / "real")
    (root / "input" / "real").mkdir()
    assert model.resolve_inside("input/link/a.wav") == root.resolve() / "input/real/a.wav"


def test_the_guard_runs_at_use_time_not_only_on_load(root, tmp_path):
    song = model.parse(doc(beat={"file": "input/beat.wav"}))
    assert model.resolve_inside(song.beat["file"]).is_relative_to(root.resolve())
    (root / "input" / "beat.wav").symlink_to(tmp_path)
    with pytest.raises(model.SongError):
        model.resolve_inside(song.beat["file"])


def test_the_default_root_is_the_folder_holding_apps_redline():
    assert (model.ROOT / "apps" / "redline" / "song" / "model.py").is_file()


def test_save_bumps_rev_each_time(root):
    first = model.save(sample(), 0)
    assert first.rev == 1
    second = model.save(first, 1)
    assert second.rev == 2
    assert model.load("demo").rev == 2
    assert model.list_songs() == ["demo"]


def test_a_stale_rev_is_refused_and_leaves_the_file(root):
    first = model.save(sample(), 0)
    model.save(first, 1)
    with pytest.raises(model.StaleRev):
        model.save(first, 1)
    with pytest.raises(model.StaleRev):
        model.save(sample(), 0)
    assert model.load("demo").rev == 2


def test_modes_are_owner_only_and_no_tmp_is_left(root):
    old = os.umask(0o022)
    try:
        model.save(sample(), 0)
    finally:
        os.umask(old)
    folder = root / "private" / "songs" / "demo"
    assert mode(folder / "song.json") == 0o600
    assert mode(folder) == 0o700
    assert mode(root / "private" / "songs") == 0o700
    assert sorted(p.name for p in folder.iterdir()) == [".song.lock", "song.json"]
    assert mode(folder / ".song.lock") == 0o600


def test_load_refuses_a_missing_song_and_a_bad_name(root):
    with pytest.raises(model.SongError, match="no such song"):
        model.load("nope")
    with pytest.raises(model.SongError, match="name"):
        model.load("../x")
    assert model.list_songs() == []


def test_load_refuses_a_file_of_another_name(root):
    model.save(sample("a"), 0)
    (root / "private/songs/b").mkdir()
    (root / "private/songs/a/song.json").rename(root / "private/songs/b/song.json")
    with pytest.raises(model.SongError, match="holds the song"):
        model.load("b")


def test_save_refuses_a_songs_folder_that_is_a_symlink_out(root, tmp_path):
    (root / "private").rmdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "private").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(model.SongError):
        model.save(sample(), 0)
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_make_private_dir_makes_parents_at_0700_and_refuses_outside(tmp_path):
    old = os.umask(0o022)
    try:
        target = make_private_dir(tmp_path / "a" / "b" / "c", tmp_path)
    finally:
        os.umask(old)
    for folder in (tmp_path / "a", tmp_path / "a" / "b", target):
        assert mode(folder) == 0o700
    with pytest.raises(ValueError):
        make_private_dir(tmp_path.parent / "other", tmp_path)
