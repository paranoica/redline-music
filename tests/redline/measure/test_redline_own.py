import math

import numpy as np
import pytest
from redline.core.loudness import integrated_lufs
from redline.measure import run
from redline.measure.run import measure_inputs, measure_own
from redline_signals import make_rng, tone, write

RATE = 44100


class CountingSeparator:
    id = "fake:counting"

    def __init__(self):
        self.lengths = []

    def __call__(self, samples, rate):
        self.lengths.append(len(samples))
        return samples * 0.5, samples * 0.5


def voice(seconds, rate=RATE, amp=0.3):
    t = np.arange(int(seconds * rate)) / rate
    mono = amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    return np.column_stack([mono, mono]).astype(np.float32)


def kick(seconds, rate=RATE, amp=0.5):
    rng = make_rng()
    n = int(seconds * rate)
    t = np.arange(n) / rate
    mono = amp * np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    mono = mono + 0.0005 * rng.standard_normal(n)
    return np.column_stack([mono, mono]).astype(np.float32)


@pytest.fixture
def refs(tmp_path):
    folder = tmp_path / "refs"
    folder.mkdir()
    return [
        write(folder, "a.wav", voice(3, amp=0.2) + kick(3), RATE, subtype="FLOAT"),
        write(folder, "b.wav", voice(4, amp=0.4) + kick(4), RATE, subtype="FLOAT"),
    ]


def own(tmp_path, vocal, beat, vocal_rate=RATE, beat_rate=RATE):
    folder = tmp_path / "own"
    folder.mkdir(exist_ok=True)
    return (
        write(folder, "vocal.wav", vocal, vocal_rate, subtype="FLOAT"),
        write(folder, "beat.wav", beat, beat_rate, subtype="FLOAT"),
    )


def test_separator_runs_only_on_the_references(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(4), kick(4))
    separator = CountingSeparator()
    measure_own(vocal, beat, refs, tmp_path / "work", separator)
    assert sorted(separator.lengths) == [3 * RATE, 4 * RATE]
    measure_own(vocal, beat, refs, tmp_path / "work", separator)
    assert len(separator.lengths) == 2


def test_track_stems_are_the_given_files_at_the_analysis_rate(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(3, rate=48000), kick(3), vocal_rate=48000)
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    stems = inputs.track_stems
    assert stems.rate == RATE
    assert not stems.cached
    assert stems.vocal.shape == stems.beat.shape == (3 * RATE, 2)
    np.testing.assert_array_equal(stems.beat, kick(3))
    # the polyphase filter rings at the edges; the inside is the same sine at 44.1 kHz
    inner = slice(RATE // 10, -RATE // 10)
    np.testing.assert_allclose(stems.vocal[inner], voice(3)[inner], atol=2e-3)


def test_track_is_named_after_the_vocal_with_its_codec_and_rate(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(3, rate=48000), kick(3), vocal_rate=48000)
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert (inputs.track.name, inputs.track.codec, inputs.track.native_rate) == (
        "vocal.wav",
        "wav",
        48000,
    )
    assert [r.name for r in inputs.refs] == ["a.wav", "b.wav"]


def hot(seconds):
    # a beat mastered into the ceiling: a share of its samples sits above -1 dBFS
    return np.clip(kick(seconds, amp=1.5), -0.99, 0.99)


def test_a_lossy_beat_keeps_its_hot_share_out_of_the_own_mix(tmp_path, refs):
    folder = tmp_path / "own"
    folder.mkdir()
    vocal = write(folder, "vocal.wav", voice(4), RATE, subtype="FLOAT")
    beat = write(folder, "beat.mp3", hot(4), RATE)
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert inputs.track.values["mix.hot_share"] is None
    assert inputs.track.codec == "mp3"
    assert inputs.track.native_rate == RATE
    assert inputs.track.values["mix.lufs"] is not None


def test_a_lossless_beat_and_vocal_report_the_hot_share(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(4), hot(4))
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert inputs.track.values["mix.hot_share"] > 0
    assert inputs.track.codec == "wav"


def test_a_lossy_vocal_over_a_lossless_beat_still_leaves_the_hot_share_out(tmp_path, refs):
    folder = tmp_path / "own"
    folder.mkdir()
    vocal = write(folder, "vocal.ogg", voice(4), RATE)
    beat = write(folder, "beat.wav", hot(4), RATE, subtype="FLOAT")
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert inputs.track.values["mix.hot_share"] is None
    assert inputs.track.codec == "ogg"


def test_the_same_pair_with_the_beat_now_lossy_is_measured_again(tmp_path, refs):
    folder = tmp_path / "own"
    folder.mkdir()
    vocal = write(folder, "vocal.wav", voice(4), RATE, subtype="FLOAT")
    wav = write(folder, "beat.wav", hot(4), RATE, subtype="FLOAT")
    first = measure_own(vocal, wav, refs, tmp_path / "work", CountingSeparator())
    assert first.track.values["mix.hot_share"] is not None
    lossy = write(folder, "beat.mp3", hot(4), RATE)
    second = measure_own(vocal, lossy, refs, tmp_path / "work", CountingSeparator())
    assert second.track.values["mix.hot_share"] is None


def test_positive_offset_delays_the_vocal(tmp_path, refs):
    given = voice(2)
    vocal, beat = own(tmp_path, given, kick(3))
    stems = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator(), 0.5).track_stems
    half = RATE // 2
    assert stems.vocal.shape == stems.beat.shape == (3 * RATE, 2)
    assert not np.any(stems.vocal[:half])
    np.testing.assert_array_equal(stems.vocal[half : half + 2 * RATE], given)
    assert not np.any(stems.vocal[half + 2 * RATE :])
    np.testing.assert_array_equal(stems.beat, kick(3))


def test_negative_offset_drops_the_vocal_start(tmp_path, refs):
    given = voice(2)
    vocal, beat = own(tmp_path, given, kick(3))
    stems = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator(), -0.5).track_stems
    half = RATE // 2
    assert stems.vocal.shape == stems.beat.shape == (3 * RATE, 2)
    np.testing.assert_array_equal(stems.vocal[: 2 * RATE - half], given[half:])
    assert not np.any(stems.vocal[2 * RATE - half :])


def test_shorter_beat_is_padded_at_the_end(tmp_path, refs):
    given = kick(2)
    vocal, beat = own(tmp_path, voice(2), given)
    stems = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator(), 1.0).track_stems
    assert stems.vocal.shape == stems.beat.shape == (3 * RATE, 2)
    np.testing.assert_array_equal(stems.beat[: 2 * RATE], given)
    assert not np.any(stems.beat[2 * RATE :])
    np.testing.assert_array_equal(stems.vocal[RATE:], voice(2))


def test_offset_dropping_the_whole_vocal_is_refused(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(1), kick(3))
    with pytest.raises(ValueError, match=r"vocal\.wav"):
        measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator(), -1.0)


def test_mono_vocal_file(tmp_path, refs):
    mono = voice(3)[:, 0]
    vocal, beat = own(tmp_path, mono, kick(3))
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert inputs.track_stems.vocal.shape == (3 * RATE, 2)
    np.testing.assert_array_equal(inputs.track_stems.vocal[:, 0], mono)
    np.testing.assert_array_equal(inputs.track_stems.vocal[:, 1], mono)
    assert inputs.track.values["vocal.lufs"] is not None


def test_second_run_reads_the_track_from_the_cache(tmp_path, refs, monkeypatch):
    calls = []
    measure = run._measure
    monkeypatch.setattr(
        run, "_measure", lambda *args: calls.append(len(args[1].samples)) or measure(*args)
    )
    vocal, beat = own(tmp_path, voice(4), kick(4))
    first = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert not first.track.cached
    assert len(calls) == 3
    second = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert second.track.cached
    assert len(calls) == 3
    assert second.track.values == first.track.values


def test_another_offset_is_measured_again(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(4), kick(4))
    measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    shifted = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator(), 0.25)
    assert not shifted.track.cached


def test_swapped_vocal_and_beat_are_measured_again(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(4), kick(4))
    first = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    swapped = measure_own(beat, vocal, refs, tmp_path / "work", CountingSeparator())
    assert not swapped.track.cached
    assert swapped.track.values["pair.vocal_minus_beat_lu"] == pytest.approx(
        -first.track.values["pair.vocal_minus_beat_lu"]
    )


def test_vocal_minus_beat_is_the_level_of_the_given_stems(tmp_path, refs):
    loud, quiet = tone(1000, 4, amp=0.4), tone(1000, 4, amp=0.1)
    vocal, beat = own(tmp_path, loud, quiet)
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    gap = inputs.track.values["pair.vocal_minus_beat_lu"]
    assert gap == pytest.approx(20 * math.log10(4), abs=0.01)
    assert gap == pytest.approx(integrated_lufs(loud, RATE) - integrated_lufs(quiet, RATE))


def test_track_mix_is_vocal_plus_beat(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(3), kick(3))
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    mix = voice(3) + kick(3)
    assert inputs.track.values["mix.lufs"] == pytest.approx(integrated_lufs(mix, RATE))
    assert inputs.track.values["mix.duration_s"] == pytest.approx(3.0)


def test_references_are_measured_as_measure_inputs_does(tmp_path, refs):
    vocal, beat = own(tmp_path, voice(3), kick(3))
    own_inputs = measure_own(vocal, beat, refs, tmp_path / "own-work", CountingSeparator())
    track = write(tmp_path, "track.wav", voice(3) + kick(3), RATE, subtype="FLOAT")
    separated = measure_inputs(track, refs, tmp_path / "work", CountingSeparator())
    assert own_inputs.upper == separated.upper
    assert [r.values for r in own_inputs.refs] == [r.values for r in separated.refs]


def noise(seconds, amp, cutoff=None):
    rng = make_rng()
    data = (amp * rng.standard_normal((int(seconds * RATE), 2))).astype(np.float32)
    if cutoff is None:
        return data
    spec = np.fft.rfft(data, axis=0)
    spec[np.fft.rfftfreq(len(data), 1 / RATE) > cutoff] = 0
    return np.fft.irfft(spec, n=len(data), axis=0).astype(np.float32)


def test_band_limit_of_the_vocal_alone_bounds_the_comparison(tmp_path, refs):
    # the beat's wideband noise hides the vocal's cliff in the mix
    vocal, beat = own(tmp_path, voice(3) + noise(3, 0.02, 12000), kick(3) + noise(3, 0.1))
    inputs = measure_own(vocal, beat, refs, tmp_path / "work", CountingSeparator())
    assert inputs.upper == pytest.approx(12000, abs=200)
