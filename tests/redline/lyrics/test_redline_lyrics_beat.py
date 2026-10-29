import numpy as np
import pytest
from redline.lyrics.text.parse import LyricsError
from redline.lyrics.write.beat import beat_bars, beat_bpm, half_time
from redline_signals import make_rng, write

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


def test_a_beat_has_as_many_whole_bars_as_its_length_holds(tmp_path):
    path = write(tmp_path, "beat.wav", clicks(150, seconds=30), RATE)
    # 30 s at 75 BPM half time, a bar of 3.2 s: nine whole bars
    assert beat_bars(path, 75.0) == 9
    assert beat_bars(path, 60.0) == 7


def noise(seconds, cutoff, btype):
    from scipy.signal import butter, sosfilt

    x = make_rng().standard_normal(RATE * seconds)
    sos = butter(4, cutoff, btype=btype, fs=RATE, output="sos")
    y = sosfilt(sos, x)
    y = 0.3 * y / np.abs(y).max()
    return np.column_stack([y, y]).astype(np.float32)


def test_a_bright_beat_reads_as_hard_and_a_dark_one_as_calm(tmp_path):
    from redline.lyrics.write.beat import HARD_BRIGHTNESS_HZ, beat_brightness, delivery_of

    dark = beat_brightness(write(tmp_path, "dark.wav", noise(20, 800, "lowpass"), RATE))
    bright = beat_brightness(write(tmp_path, "bright.wav", noise(20, 3000, "highpass"), RATE))
    assert dark < HARD_BRIGHTNESS_HZ < bright
    assert delivery_of(bright) == "hard" and delivery_of(dark) == "calm"
    assert delivery_of(HARD_BRIGHTNESS_HZ) == "hard"
