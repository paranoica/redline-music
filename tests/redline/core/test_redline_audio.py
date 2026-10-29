import math

import numpy as np
import pytest
from redline.core.audio import (
    ANALYSIS_RATE,
    AudioError,
    band_limit_hz,
    decode,
    hot_master,
    to_analysis,
)
from redline_signals import lowpass_brickwall, make_rng, tone, write


def peak_freq(x: np.ndarray, rate: int) -> float:
    spec = np.abs(np.fft.rfft(x[:, 0] * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spec)])


def test_resample_keeps_pitch_and_length(tmp_path):
    path = write(tmp_path, "a.wav", tone(1000, 3.0, rate=48000, amp=0.5), 48000, subtype="FLOAT")
    audio = to_analysis(decode(path))
    assert audio.rate == ANALYSIS_RATE == 44100
    assert abs(len(audio.samples) - round(3.0 * 44100)) <= 1
    assert abs(peak_freq(audio.samples, 44100) - 1000) <= 5


def test_mono_becomes_two_equal_channels(tmp_path):
    path = write(tmp_path, "m.wav", tone(440, 1.0, amp=0.3)[:, 0], 44100)
    decoded = decode(path)
    assert decoded.samples.shape[1] == 2
    assert np.array_equal(decoded.samples[:, 0], decoded.samples[:, 1])


def test_mp3_decodes_to_original_length_and_pitch(tmp_path):
    original = tone(1000, 3.0, amp=0.5)
    path = write(tmp_path, "t.mp3", original, 44100, format="MP3")
    decoded = decode(path)
    assert decoded.codec == "mp3"
    assert abs(len(decoded.samples) - len(original)) <= 2 * 1152
    assert abs(peak_freq(decoded.samples, 44100) - 1000) <= 5


@pytest.mark.parametrize("content", [None, b"", b"not audio at all, just text\n" * 50])
def test_decode_errors_name_the_path(tmp_path, content):
    path = tmp_path / "bad.wav"
    if content is not None:
        path.write_bytes(content)
    with pytest.raises(AudioError, match=r"bad\.wav"):
        decode(path)


def test_hot_share_of_clipped_sine_on_native_rate(tmp_path):
    clipped = 2.0 * np.clip(tone(997, 4.0, rate=48000), -0.5, 0.5)
    path = write(tmp_path, "hot.wav", clipped, 48000, subtype="FLOAT")
    hot = hot_master(decode(path))
    # time share where |2·clip(sin, 0.5)| exceeds -1 dBFS: |sin| > 10^(-1/20) / 2
    expected = 1 - (2 / math.pi) * math.asin(10 ** (-1 / 20) / 2)
    assert hot.hot_share == pytest.approx(expected, abs=0.01)
    assert hot.sample_peak_db == pytest.approx(0.0, abs=0.01)
    assert hot.lossy is False


def test_quiet_sine_has_no_hot_samples(tmp_path):
    path = write(tmp_path, "q.wav", tone(997, 2.0, amp=0.5), 44100, subtype="FLOAT")
    hot = hot_master(decode(path))
    assert hot.hot_share == 0.0
    assert hot.sample_peak_db == pytest.approx(20 * math.log10(0.5), abs=0.01)


def test_true_peak_finds_the_peak_between_samples(tmp_path):
    rate = 48000
    x = tone(rate / 4, 2.0, rate=rate, phase=math.pi / 4)
    path = write(tmp_path, "tp.wav", x, rate, subtype="FLOAT")
    hot = hot_master(decode(path))
    assert hot.sample_peak_db == pytest.approx(20 * math.log10(math.sqrt(0.5)), abs=0.05)
    assert hot.true_peak_db == pytest.approx(0.0, abs=0.2)


def test_mp3_input_is_flagged_lossy(tmp_path):
    path = write(tmp_path, "l.mp3", tone(997, 2.0, amp=0.5), 44100, format="MP3")
    assert hot_master(decode(path)).lossy is True


def test_silence_is_not_hot(tmp_path):
    path = write(tmp_path, "s.wav", np.zeros((44100, 2), dtype=np.float32), 44100, subtype="FLOAT")
    hot = hot_master(decode(path))
    assert hot.hot_share == 0.0
    assert hot.sample_peak_db == -math.inf


def test_band_limit_finds_a_codec_style_cutoff():
    noise = make_rng().standard_normal((44100 * 10, 2)).astype(np.float32) * 0.1
    cut = lowpass_brickwall(noise, 44100, 16000)
    assert band_limit_hz(cut[:, 0], 44100) == pytest.approx(16000, abs=500)


def test_band_limit_of_full_band_noise_is_nyquist():
    noise = make_rng().standard_normal(44100 * 10).astype(np.float32) * 0.1
    assert band_limit_hz(noise, 44100) >= 20000


def test_band_limit_of_silence_is_none():
    assert band_limit_hz(np.zeros(44100 * 2, dtype=np.float32), 44100) is None


def test_analysis_audio_carries_band_limits(tmp_path):
    noise = make_rng().standard_normal((44100 * 10, 2)).astype(np.float32) * 0.1
    path = write(tmp_path, "n.wav", lowpass_brickwall(noise, 44100, 16000), 44100, subtype="FLOAT")
    audio = to_analysis(decode(path))
    assert audio.band_limit_hz == pytest.approx(16000, abs=500)
    assert audio.side_band_limit_hz == pytest.approx(16000, abs=500)


def test_mono_input_has_no_side_band_limit(tmp_path):
    path = write(tmp_path, "mono.wav", tone(440, 3.0, amp=0.3)[:, 0], 44100)
    assert to_analysis(decode(path)).side_band_limit_hz is None
