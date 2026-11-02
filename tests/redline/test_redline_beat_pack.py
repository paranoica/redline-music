import os

import numpy as np
import pytest
import soundfile as sf
from redline.beat import pack
from redline.beat.pack import PackError

RATE = 48000


def tone(hz, seconds, level=0.5):
    t = np.arange(int(RATE * seconds)) / RATE
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def hiss(seconds, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.uniform(-1, 1, int(RATE * seconds))
    # a first difference tilts white noise to the highs, like a hat
    return (0.4 * np.diff(x, prepend=0.0)).astype(np.float32)


def put(root, relative, samples):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, samples, RATE)
    return path


@pytest.fixture
def made(tmp_path):
    root = tmp_path / "sounds"
    put(root, "Drums/Kicks/Hard Kick 01.wav", tone(55, 0.3))
    put(root, "808s/808 Boom (F#).wav", tone(46.25, 1.5))
    put(root, "808s/Dark 808.wav", tone(49.0, 1.2))
    put(root, "808s/808 High (F#).wav", tone(92.5, 1.2))
    put(root, "Hats/HH_closed_03.wav", hiss(0.08))
    put(root, "Hats/Open Hat 2.wav", hiss(0.6, seed=1))
    put(root, "808 Kit/Kick 7.wav", tone(60, 0.25))
    put(root, "Loops/Dark Melody 140bpm C#min.wav", tone(277, 3.0))
    put(root, "misc/thing.wav", hiss(0.1, seed=2))
    put(root, "misc/low.wav", tone(49.0, 1.0))
    put(root, "misc/long pad thing.wav", tone(220, 6.0))
    put(root, "__MACOSX/Drums/._Kick.wav", tone(55, 0.3))
    put(root, ".trash/Kick.wav", tone(55, 0.3))
    (root / "misc" / "broken.wav").write_bytes(b"RIFF....not audio at all")
    (root / "readme.txt").write_text("not a sound", encoding="utf-8")
    return root, tmp_path / "work" / "catalog.json"


def by_path(sounds):
    return {s.path: s for s in sounds}


def test_a_scan_sorts_files_by_their_words_first(made):
    root, catalog = made
    sounds, problems = pack.scan(root, catalog)
    found = by_path(sounds)
    assert found["Drums/Kicks/Hard Kick 01.wav"].category == "kick"
    assert found["Hats/HH_closed_03.wav"].category == "hat_closed"
    assert found["Hats/Open Hat 2.wav"].category == "hat_open"
    assert found["808s/808 Boom (F#).wav"].category == "808"
    # the file's own name wins over its folder's
    assert found["808 Kit/Kick 7.wav"].category == "kick"
    assert all(found[p].by == "name" for p in ("Hats/HH_closed_03.wav", "808 Kit/Kick 7.wav"))
    assert len(problems) == 1 and "broken.wav" in problems[0]


def test_hidden_and_mac_junk_and_non_audio_are_left_out(made):
    root, catalog = made
    sounds, _ = pack.scan(root, catalog)
    paths = set(by_path(sounds))
    assert not any(p.startswith(("__MACOSX", ".trash")) for p in paths)
    assert "readme.txt" not in paths
    assert len(paths) == 11


def test_an_808_takes_its_root_from_its_name_or_else_from_its_pitch(made):
    root, catalog = made
    found = by_path(pack.scan(root, catalog)[0])
    # the name gives F#, the sound gives the octave: F#1 and F#2
    assert found["808s/808 Boom (F#).wav"].note == 30
    assert found["808s/808 High (F#).wav"].note == 42
    # a 49 Hz sine is G1
    assert found["808s/Dark 808.wav"].note == 31
    assert found["Drums/Kicks/Hard Kick 01.wav"].note is None


def test_a_file_with_no_words_is_judged_by_its_sound(made):
    root, catalog = made
    found = by_path(pack.scan(root, catalog)[0])
    assert (found["misc/thing.wav"].category, found["misc/thing.wav"].by) == ("hat_closed", "sound")
    low = found["misc/low.wav"]
    assert (low.category, low.by, low.note) == ("808", "sound", 31)
    assert found["misc/long pad thing.wav"].category == "melodic"


def test_a_loop_gets_its_tempo_and_key_from_its_name(made):
    root, catalog = made
    loop = by_path(pack.scan(root, catalog)[0])["Loops/Dark Melody 140bpm C#min.wav"]
    assert (loop.category, loop.bpm, loop.key) == ("loop", 140.0, "C# minor")
    assert loop.note is None


@pytest.mark.parametrize(
    ("name", "note"),
    [
        ("808 Boom (F#).wav", (6, None)),
        ("Bell C4.wav", (0, 4)),
        ("Sub Bb.wav", (10, None)),
        ("Glide A-1.wav", (9, -1)),
        ("Pluck B#.wav", (0, None)),
        ("Boom.wav", None),
        ("Dark 808.wav", None),
        ("", None),
    ],
)
def test_a_note_in_a_name(name, note):
    assert pack.note_by_name(name) == note


@pytest.mark.parametrize(
    ("named", "heard", "fallback", "root"),
    [
        ((0, 4), 30, 1, 60),
        ((6, None), 42, 1, 42),
        ((6, None), 44, 1, 42),
        # 36 is as far from F#1 as from F#2: the lower
        ((6, None), 36, 1, 30),
        ((6, None), None, 1, 30),
        ((0, None), None, 4, 60),
        (None, 31, 1, 31),
        (None, None, 1, None),
    ],
)
def test_the_root_keeps_the_names_note_in_the_octave_heard(named, heard, fallback, root):
    assert pack.root_of(named, heard, fallback) == root


@pytest.mark.parametrize(
    ("name", "bpm", "key"),
    [
        ("Loop 140bpm Fmin.wav", 140.0, "F minor"),
        ("bpm 92 Ebmaj loop.wav", 92.0, "Eb major"),
        ("Loop 300bpm.wav", None, None),
        ("Loop 39 bpm A minor.wav", None, "A minor"),
        ("Drum Loop.wav", None, None),
    ],
)
def test_a_tempo_and_key_in_a_name(name, bpm, key):
    assert pack.bpm_by_name(name) == bpm
    assert pack.key_by_name(name) == key


def test_a_rescan_reads_only_new_or_changed_files(made, monkeypatch):
    root, catalog = made
    first, _ = pack.scan(root, catalog)
    seen = []
    real = pack.describe

    def counting(r, path):
        seen.append(path.relative_to(r).as_posix())
        return real(r, path)

    monkeypatch.setattr(pack, "describe", counting)
    changed = root / "Hats" / "HH_closed_03.wav"
    sf.write(changed, hiss(0.09, seed=5), RATE)
    put(root, "Drums/Snares/Snare 1.wav", hiss(0.2, seed=6))
    again, _ = pack.scan(root, catalog)
    # the broken file is read again too: a problem is not catalogued
    assert sorted(seen) == ["Drums/Snares/Snare 1.wav", "Hats/HH_closed_03.wav", "misc/broken.wav"]
    assert len(again) == len(first) + 1
    assert pack.load(catalog) == {s.id: s for s in again}


def test_a_pack_sound_is_found_by_id_and_refused_once_it_changed(made):
    root, catalog = made
    kick = by_path(pack.scan(root, catalog)[0])["Drums/Kicks/Hard Kick 01.wav"]
    path, entry = pack.file_of(f"pack:{kick.id}", root, catalog)
    assert path == root / "Drums/Kicks/Hard Kick 01.wav" and entry == kick
    with pytest.raises(PackError, match="not in the pack"):
        pack.file_of("pack:0000000000", root, catalog)
    with pytest.raises(PackError, match="not a pack sound"):
        pack.file_of("vsco-harp", root, catalog)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(PackError, match="changed since the scan"):
        pack.file_of(f"pack:{kick.id}", root, catalog)
    path.unlink()
    with pytest.raises(PackError, match="is gone"):
        pack.file_of(f"pack:{kick.id}", root, catalog)


def test_no_pack_folder_is_an_error_and_no_catalogue_is_empty(tmp_path):
    with pytest.raises(PackError, match="no pack folder"):
        pack.scan(tmp_path / "missing", tmp_path / "catalog.json")
    assert pack.load(tmp_path / "none.json") == {}


def test_the_catalogue_is_written_private(made):
    root, catalog = made
    pack.scan(root, catalog)
    assert catalog.stat().st_mode & 0o077 == 0


# the pack as a sound library


@pytest.fixture
def placed(tmp_path, monkeypatch):
    """A pack and its catalogue where the defaults look: input/sounds and work/sounds/pack."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "input" / "sounds"
    put(root, "Kicks/Kick 1.wav", tone(55, 0.3))
    put(root, "808s/808 Boom (F#).wav", tone(46.25, 1.5))
    sounds, _ = pack.scan(pack.PACK, pack.CATALOG)
    return {s.path: s for s in sounds}


def test_a_pack_sound_is_a_library_never_marked_for_sale(placed):
    from redline.beat import library

    kick = placed["Kicks/Kick 1.wav"]
    entry = library.source(f"pack:{kick.id}")
    assert (entry.kind, entry.commercial) == ("pack", False)
    assert library.pack_names() == {f"pack:{s.id}" for s in placed.values()}


def test_a_pack_sound_is_one_zone_at_its_root_or_middle_c(placed):
    from redline.beat import library

    boom = placed["808s/808 Boom (F#).wav"]
    (zone,) = library.zones(f"pack:{boom.id}", library.SOUNDS, [30, 40])
    assert (zone.path.name, zone.note, zone.vel) == ("808 Boom (F#).wav", 30, (0, 127))
    (kick,) = library.zones(f"pack:{placed['Kicks/Kick 1.wav'].id}")
    assert kick.note == 60


def test_a_changed_pack_file_is_a_library_error(placed):
    from redline.beat import library

    kick = placed["Kicks/Kick 1.wav"]
    sf.write(pack.PACK / kick.path, tone(70, 0.4), RATE)
    with pytest.raises(library.LibraryError, match="changed since the scan"):
        library.zones(f"pack:{kick.id}")


def test_a_score_may_name_a_catalogued_pack_sound_and_no_other(placed):
    redline_dsp = pytest.importorskip("redline_dsp")
    assert redline_dsp
    from redline.beat.checks import _libraries
    from redline.beat.library import load, pack_names

    known = load().keys() | pack_names()
    good = {
        "sounds": {
            "s": {"instrument": "sampler", "library": f"pack:{placed['Kicks/Kick 1.wav'].id}"}
        }
    }
    bad = {"sounds": {"s": {"instrument": "sampler", "library": "pack:ffffffffff"}}}
    assert _libraries(good, known) == []
    assert _libraries(bad, known) == [
        "sounds.s.library: pack:ffffffffff is not in the sound library"
    ]


def test_the_beat_server_serves_a_pack_zone_and_nothing_beside_it(placed, tmp_path):
    pytest.importorskip("redline_dsp")
    from redline.beat import server as beat_server

    beat = beat_server.BeatStudio(1, tmp_path / "beats", tmp_path / "work")
    boom = placed["808s/808 Boom (F#).wav"]
    pcm, rate, channels, frames, root = beat.zone(f"pack:{boom.id}", "0")
    assert (rate, channels, frames, root) == (RATE, 1, int(RATE * 1.5), 30)
    assert len(pcm) == 2 * frames
    with pytest.raises(beat_server.BadRequest) as wrong:
        beat.zone(f"pack:{boom.id}", "1")
    assert wrong.value.status == 404
    outside = tmp_path / "elsewhere.wav"
    sf.write(outside, tone(100, 0.2), RATE)
    link = pack.PACK / "Kicks" / "Link.wav"
    link.symlink_to(outside)
    linked = {s.path: s for s in pack.scan(pack.PACK, pack.CATALOG)[0]}["Kicks/Link.wav"]
    with pytest.raises(beat_server.BadRequest) as escaped:
        beat.zone(f"pack:{linked.id}", "0")
    assert escaped.value.status == 403
