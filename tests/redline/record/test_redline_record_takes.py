import json

import pytest
from redline.record.latency import RoundTrip
from redline.record.protocol import Dropout, TakeReport
from redline.record.takes import discard, next_take, write_sidecar
from redline.takes.rank import find_takes, read_dropouts

RATE = 48000


def touch(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"")


def test_the_first_take_of_a_new_song_is_take_01(tmp_path):
    folder = tmp_path / "input" / "takes" / "song"
    assert next_take(folder) == folder / "take-01.wav"
    assert folder.is_dir()


def test_the_next_take_follows_the_highest_not_the_count(tmp_path):
    touch(tmp_path, "take-01.wav", "take-07.wav", "take-03.wav")
    assert next_take(tmp_path).name == "take-08.wav"


def test_other_files_do_not_count(tmp_path):
    touch(tmp_path, "take-02.json", "take-99.txt", "beat.wav", "takeover-50.wav", "take-x.wav")
    assert next_take(tmp_path).name == "take-01.wav"


def test_past_99_the_numbers_grow_a_digit_and_still_sort_for_takes(tmp_path):
    touch(tmp_path, "take-99.wav")
    path = next_take(tmp_path)
    assert path.name == "take-100.wav"
    path.write_bytes(b"")
    assert [p.name for p in find_takes(tmp_path)][-1] == "take-100.wav"


def report(path, dropouts):
    return TakeReport(path, RATE, 10 * RATE, 300, dropouts)


def test_the_sidecar_carries_the_engine_dropouts_as_takes_reads_them(tmp_path):
    take = tmp_path / "take-01.wav"
    dropouts = [
        Dropout(4800, 480, ("xrun",)),
        Dropout(96000, 0, ("xrun",)),
        Dropout(240000, 960, ("overflow", "counter_gap")),
    ]
    sidecar = write_sidecar(report(take, dropouts), RoundTrip(300, "calibrated"), 64)
    assert sidecar == tmp_path / "take-01.json"
    # the xrun that lost nothing is no dropout
    assert read_dropouts(take, RATE) == (2, pytest.approx(1000 * 1440 / RATE), None)
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert data["dropouts"] == [[4800, 5280], [240000, 240960]]
    assert data["xruns_without_loss"] == 1
    assert data["offset"] == 300 and data["offset_source"] == "calibrated"
    assert data["rate"] == RATE and data["quantum"] == 64
    assert data["frames"] == 10 * RATE


def test_a_take_without_dropouts_reads_as_clean(tmp_path):
    take = tmp_path / "take-02.wav"
    write_sidecar(report(take, []), RoundTrip(None, "unknown"), 128)
    assert read_dropouts(take, RATE) == (0, 0.0, None)
    data = json.loads(take.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["offset_source"] == "unknown"


def test_discard_removes_the_take_and_its_sidecar_only(tmp_path):
    touch(tmp_path, "take-01.wav", "take-01.json", "take-02.wav", "take-02.json")
    discard(tmp_path / "take-02.wav")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["take-01.json", "take-01.wav"]
    # a take the engine never finished has no sidecar
    touch(tmp_path, "take-03.wav")
    discard(tmp_path / "take-03.wav")
    assert not (tmp_path / "take-03.wav").exists()
