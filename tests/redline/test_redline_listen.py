import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, tone
from scipy.signal import resample_poly

from redline.listen import CEILING_DB, LOUDEST_LUFS, write_listen_set
from redline.loudness import integrated_lufs

RATE = 44100


def variants():
    rng = make_rng()
    noise = rng.standard_normal((RATE * 4, 2)).astype(np.float32)
    return {
        "original": tone(440, 4, amp=0.1),
        "eq": tone(440, 4, amp=0.5) + 0.05 * noise,
        # a 4x true-peak estimate reads this one about 0.2 dB under its real peak
        "light": tone(15000, 4, amp=0.9, phase=0.7),
        "medium": 0.02 * noise,
        "heavy": np.clip(tone(220, 4, amp=2.0), -0.3, 0.3),
    }


def read(path):
    data, rate = sf.read(path, always_2d=True)
    return data, rate, sf.info(path).subtype


def peak_16x_db(data):
    return 20 * np.log10(np.abs(resample_poly(data, 16, 1, axis=0)).max())


def test_listen_files_are_letters_at_one_loudness(tmp_path):
    key = write_listen_set(variants(), RATE, tmp_path, seed=7)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["A.wav", "B.wav", "C.wav", "D.wav", "E.wav"]
    assert sorted(key) == list("ABCDE")
    assert sorted(key.values()) == sorted(variants())
    levels = []
    for name in files:
        data, rate, subtype = read(tmp_path / name)
        assert rate == RATE
        assert subtype == "PCM_24"
        assert peak_16x_db(data) <= CEILING_DB + 0.05
        levels.append(integrated_lufs(data, RATE))
    assert max(levels) - min(levels) <= 0.1
    assert max(levels) <= LOUDEST_LUFS + 0.05


def test_quiet_set_reaches_the_loudest_level(tmp_path):
    quiet = {"a": tone(440, 4, amp=0.01), "b": tone(660, 4, amp=0.02)}
    write_listen_set(quiet, RATE, tmp_path, seed=1)
    for name in ("A.wav", "B.wav"):
        data, _, _ = read(tmp_path / name)
        assert integrated_lufs(data, RATE) == pytest.approx(LOUDEST_LUFS, abs=0.1)


def test_each_file_is_its_variant_scaled(tmp_path):
    source = variants()
    key = write_listen_set(source, RATE, tmp_path, seed=3)
    for letter, name in key.items():
        data, _, _ = read(tmp_path / f"{letter}.wav")
        corr = np.corrcoef(data[:, 0], source[name][:, 0])[0, 1]
        assert corr > 0.9999


def test_same_seed_same_letters_and_seeds_do_shuffle(tmp_path):
    first = write_listen_set(variants(), RATE, tmp_path / "a", seed=11)
    again = write_listen_set(variants(), RATE, tmp_path / "b", seed=11)
    assert first == again
    keys = {
        tuple(sorted(write_listen_set(variants(), RATE, tmp_path / f"s{s}", seed=s).items()))
        for s in range(20)
    }
    assert len(keys) > 1


def test_files_are_written_in_letter_order(tmp_path):
    write_listen_set(variants(), RATE, tmp_path, seed=5)
    by_time = sorted(tmp_path.iterdir(), key=lambda p: p.stat().st_mtime_ns)
    assert [p.name for p in by_time] == ["A.wav", "B.wav", "C.wav", "D.wav", "E.wav"]


def test_no_file_name_reveals_a_variant(tmp_path):
    write_listen_set(variants(), RATE, tmp_path, seed=5)
    names = " ".join(p.name for p in tmp_path.rglob("*"))
    for variant in variants():
        assert variant not in names
