import numpy as np
import pytest
from redline_signals import make_rng, write

from redline.lyrics.beat import beat_bpm, half_time
from redline.lyrics.parse import LyricsError

RATE = 22050


def clicks(bpm, seconds=30):
    x = np.zeros(RATE * seconds, dtype=np.float32)
    for i in range(int(seconds * bpm / 60)):
        start = int(i * 60 / bpm * RATE)
        x[start : start + 200] = np.hanning(200)
    return np.column_stack([x, x])


def test_the_half_time_value_is_the_one_in_the_octave_from_60():
    assert half_time((75.0, 150.0, 300.0)) == 75.0
    assert half_time((40.0, 80.0, 160.0)) == 80.0
    assert half_time((30.0, 60.0, 120.0)) == 60.0
    assert half_time((59.9, 119.8, 239.6)) == 119.8
    assert half_time((239.95,)) == 60.0


def test_no_tempo_is_an_error():
    with pytest.raises(LyricsError):
        half_time(())


@pytest.mark.parametrize(("played", "heard"), [(150, 75), (80, 80), (170, 85)])
def test_a_click_track_gives_its_half_time_tempo(tmp_path, played, heard):
    path = write(tmp_path, "beat.wav", clicks(played), RATE)
    assert abs(beat_bpm(path) - heard) <= 2


def test_silence_and_a_missing_file_are_errors(tmp_path):
    with pytest.raises(LyricsError):
        beat_bpm(write(tmp_path, "quiet.wav", np.zeros((RATE * 10, 2), dtype=np.float32), RATE))
    with pytest.raises(LyricsError):
        beat_bpm(tmp_path / "none.wav")


def test_a_clip_shorter_than_a_few_bars_is_not_a_beat(tmp_path):
    noise = (make_rng().standard_normal((RATE * 2, 2)) * 0.1).astype(np.float32)
    with pytest.raises(LyricsError, match="seconds"):
        beat_bpm(write(tmp_path, "short.wav", noise, RATE))
