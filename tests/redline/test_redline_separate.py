from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng

from redline.audio import ANALYSIS_RATE, Audio
from redline.separate import separate


class FakeSeparator:
    """Splits by a fixed ratio and records what it was given."""

    def __init__(self, ident: str = "fake:half", vocal_share: float = 0.5) -> None:
        self.id = ident
        self.vocal_share = vocal_share
        self.calls: list[np.ndarray] = []

    def __call__(self, samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
        self.calls.append(samples.copy())
        return samples * self.vocal_share, samples * (1 - self.vocal_share)


def audio(seconds: float = 2.0, scale: float = 0.3, seed_shift: int = 0) -> Audio:
    rng = make_rng()
    rng.standard_normal(seed_shift)
    x = (rng.standard_normal((int(seconds * ANALYSIS_RATE), 2)) * scale).astype(np.float32)
    return Audio(x, ANALYSIS_RATE, None, None)


def test_same_content_is_separated_once(tmp_path):
    fake = FakeSeparator()
    first = separate(audio(), fake, tmp_path)
    second = separate(audio(), fake, tmp_path)
    assert len(fake.calls) == 1
    assert not first.cached and second.cached
    assert np.array_equal(first.vocal, second.vocal)


def test_changed_content_is_separated_again(tmp_path):
    fake = FakeSeparator()
    separate(audio(), fake, tmp_path)
    separate(audio(seed_shift=1), fake, tmp_path)
    assert len(fake.calls) == 2


def test_changed_model_or_parameters_miss_the_cache(tmp_path):
    separate(audio(), FakeSeparator("fake:half"), tmp_path)
    other = FakeSeparator("fake:half:overlap=8")
    assert not separate(audio(), other, tmp_path).cached
    assert len(other.calls) == 1


def test_values_above_full_scale_survive_the_cache(tmp_path):
    source = audio(scale=0.3)
    np.clip(source.samples, -0.5, 0.5, out=source.samples)
    source.samples[100] = 0.9
    fake = FakeSeparator(vocal_share=1.0)
    hot = Audio(source.samples * 2, ANALYSIS_RATE, None, None)
    separate(hot, fake, tmp_path)
    cached = separate(hot, fake, tmp_path)
    assert cached.cached
    assert float(np.abs(cached.vocal).max()) == pytest.approx(1.8, abs=1e-5)


def test_failed_separation_leaves_no_cache_entry(tmp_path):
    class Broken(FakeSeparator):
        def __call__(self, samples, rate):
            raise RuntimeError("model crashed")

    with pytest.raises(RuntimeError, match="model crashed"):
        separate(audio(), Broken(), tmp_path)
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == []
    assert not separate(audio(), FakeSeparator(), tmp_path).cached


def test_stems_keep_length_and_rate(tmp_path):
    source = audio(seconds=1.5)
    stems = separate(source, FakeSeparator(), tmp_path)
    assert stems.rate == ANALYSIS_RATE
    assert stems.vocal.shape == stems.beat.shape == source.samples.shape


def test_wrong_stem_length_is_an_error(tmp_path):
    class Short(FakeSeparator):
        def __call__(self, samples, rate):
            return samples[:-10], samples[:-10]

    with pytest.raises(ValueError, match="length"):
        separate(audio(), Short(), tmp_path)


def test_hot_input_is_scaled_down_and_stems_scaled_back(tmp_path):
    source = audio(scale=0.3)
    source.samples[500] = (1.0, -1.0)
    hot = Audio(source.samples * 1.8, ANALYSIS_RATE, None, None)
    fake = FakeSeparator()
    stems = separate(hot, fake, tmp_path)
    assert float(np.abs(fake.calls[0]).max()) <= 1.0
    assert np.allclose(stems.vocal + stems.beat, hot.samples, atol=1e-5)


def test_quiet_input_is_not_scaled(tmp_path):
    source = audio(scale=0.1)
    fake = FakeSeparator()
    separate(source, fake, tmp_path)
    assert np.array_equal(fake.calls[0], source.samples)


def test_crash_while_writing_leaves_no_half_entry(tmp_path, monkeypatch):
    real_write = sf.write

    def fail_on_beat(path, *args, **kwargs):
        # the file lands on disk, then the process dies before the entry is complete
        real_write(path, *args, **kwargs)
        if Path(path).name == "beat.wav":
            raise OSError("disk full")

    monkeypatch.setattr(sf, "write", fail_on_beat)
    with pytest.raises(OSError, match="disk full"):
        separate(audio(), FakeSeparator(), tmp_path)
    monkeypatch.setattr(sf, "write", real_write)
    fake = FakeSeparator()
    assert not separate(audio(), fake, tmp_path).cached
    assert len(fake.calls) == 1
