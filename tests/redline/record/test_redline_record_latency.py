import json

import pytest
from redline.record.latency import LatencyKey, round_trip, save_calibration
from redline.record.sweep import Delay

KEY = LatencyKey(("system:capture_1", "system:playback_1"), 48000, 64)


def test_a_calibration_for_the_same_key_is_applied(tmp_path):
    path = tmp_path / "work" / "record" / "latency.json"
    save_calibration(path, KEY, Delay(312, 41.0), "cup")
    found = round_trip(path, KEY, estimate=256)
    assert found.frames == 312
    assert found.source == "calibrated"
    assert found.note is None


def test_without_a_calibration_the_port_estimate_is_used_and_labelled(tmp_path):
    found = round_trip(tmp_path / "latency.json", KEY, estimate=256)
    assert (found.frames, found.source) == (256, "estimate")
    assert "estimate" in found.label()
    assert "--calibrate" in found.note


def test_a_calibration_at_another_quantum_is_refused_not_applied(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(312, 41.0), "cup")
    found = round_trip(path, LatencyKey(KEY.devices, 48000, 128), estimate=400)
    assert (found.frames, found.source) == (400, "estimate")
    assert "quantum 64" in found.note and "quantum 128" in found.note


def test_a_calibration_on_other_devices_or_rate_is_refused(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(312, 41.0), "loopback")
    other_devices = LatencyKey(("usb:capture_1", "usb:playback_1"), 48000, 64)
    other_rate = LatencyKey(KEY.devices, 44100, 64)
    for key in (other_devices, other_rate):
        found = round_trip(path, key, estimate=300)
        assert (found.frames, found.source) == (300, "estimate")


def test_calibrations_for_several_keys_live_side_by_side(tmp_path):
    path = tmp_path / "latency.json"
    bigger = LatencyKey(KEY.devices, 48000, 128)
    save_calibration(path, KEY, Delay(312, 41.0), "cup")
    save_calibration(path, bigger, Delay(440, 38.0), "cup")
    save_calibration(path, KEY, Delay(316, 45.0), "cup")
    assert round_trip(path, KEY, None).frames == 316
    assert round_trip(path, bigger, None).frames == 440
    assert len(json.loads(path.read_text(encoding="utf-8"))["calibrations"]) == 2


def test_an_entry_whose_fields_were_edited_is_refused(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(312, 41.0), "cup")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["calibrations"][0]["quantum"] = 128
    path.write_text(json.dumps(data), encoding="utf-8")
    found = round_trip(path, LatencyKey(KEY.devices, 48000, 128), estimate=None)
    assert found.source == "unknown" and found.frames is None
    found = round_trip(path, KEY, estimate=None)
    assert found.source == "unknown" and "does not match" in found.note


def test_an_unreadable_file_is_refused_with_a_note(tmp_path):
    path = tmp_path / "latency.json"
    path.write_text("{not json", encoding="utf-8")
    found = round_trip(path, KEY, estimate=200)
    assert (found.frames, found.source) == (200, "estimate")
    assert "latency.json" in found.note


def test_with_no_calibration_and_no_estimate_the_round_trip_is_unknown(tmp_path):
    found = round_trip(tmp_path / "latency.json", KEY, estimate=None)
    assert found.frames is None and found.source == "unknown"
    assert "not shifted" in found.label()


def test_speakers_are_stored_with_their_mode(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(900, 33.0), "speakers")
    entry = json.loads(path.read_text(encoding="utf-8"))["calibrations"][0]
    assert entry["mode"] == "speakers"
    assert entry["peak_to_noise_db"] == 33.0
    assert round_trip(path, KEY, None).frames == 900


@pytest.mark.parametrize(
    "frames",
    [-1, 48001, 2**64 - 1, 312.5, True, None, "312", float("inf")],
    ids=["negative", "over a second", "huge", "fraction", "bool", "null", "text", "infinity"],
)
def test_a_stored_round_trip_that_cannot_be_one_is_refused(tmp_path, frames):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(312, 41.0), "cup")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["calibrations"][0]["frames"] = frames
    # json.dumps writes inf as Infinity, which json.loads reads back
    path.write_text(json.dumps(data), encoding="utf-8")
    found = round_trip(path, KEY, estimate=200)
    assert (found.frames, found.source) == (200, "estimate")
    assert "latency.json" in found.note


def test_a_round_trip_of_a_whole_second_is_still_applied(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(48000, 41.0), "loopback")
    assert round_trip(path, KEY, estimate=200).frames == 48000


def test_a_speakers_calibration_is_applied_with_a_note_to_recalibrate_with_the_cup(tmp_path):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(900, 33.0), "speakers")
    trip = round_trip(path, KEY, 300)
    assert (trip.frames, trip.source) == (900, "calibrated")
    assert trip.note is not None
    assert "speakers" in trip.note and "air" in trip.note and "cup" in trip.note


@pytest.mark.parametrize("mode", ["cup", "loopback"])
def test_a_cup_or_loopback_calibration_carries_no_note(tmp_path, mode):
    path = tmp_path / "latency.json"
    save_calibration(path, KEY, Delay(900, 33.0), mode)
    assert round_trip(path, KEY, 300).note is None
