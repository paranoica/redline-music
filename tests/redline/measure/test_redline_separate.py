import hashlib
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.core.audio import ANALYSIS_RATE, Audio, decode, to_analysis
from redline.measure.separate import (
    RoformerSeparator,
    SeparationError,
    cache_key,
    python_command,
    run_model,
    separate,
    separator_id,
    verify_files,
)
from redline_signals import make_rng, write


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


def stem_writer(calls: list[str], name: str):
    """A model runner that writes half-and-half stems and records its use."""

    def run(source: Path, out: Path) -> tuple[Path, Path]:
        calls.append(name)
        data, rate = sf.read(source, always_2d=True, dtype="float32")
        sf.write(out / "v.wav", data * 0.5, rate, subtype="FLOAT")
        sf.write(out / "b.wav", data * 0.5, rate, subtype="FLOAT")
        return out / "v.wav", out / "b.wav"

    return run


def failing(error: Exception, calls: list[str], name: str):
    def run(source: Path, out: Path) -> tuple[Path, Path]:
        calls.append(name)
        raise error

    return run


def test_gpu_failure_falls_back_to_the_cpu(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(RuntimeError("CUDA out of memory"), calls, "gpu"),
        run_on_cpu=stem_writer(calls, "cpu"),
    )
    stems = separate(audio(), separator, tmp_path)
    assert calls == ["gpu", "cpu"]
    assert separator.fallback_used
    assert stems.vocal.shape == audio().samples.shape


def test_other_model_errors_do_not_retry_on_the_cpu(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(RuntimeError("checkpoint is corrupt"), calls, "gpu"),
        run_on_cpu=stem_writer(calls, "cpu"),
    )
    with pytest.raises(SeparationError, match="corrupt"):
        separate(audio(), separator, tmp_path)
    assert calls == ["gpu"]


def test_missing_separation_extra_says_how_to_install(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(ModuleNotFoundError("No module named 'audio_separator'"), calls, "gpu"),
        run_on_cpu=stem_writer(calls, "cpu"),
    )
    with pytest.raises(SeparationError, match="--extra separation"):
        separate(audio(), separator, tmp_path)


def test_failure_on_both_devices_is_one_clear_error(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(RuntimeError("CUDA error: device-side assert"), calls, "gpu"),
        run_on_cpu=failing(subprocess.CalledProcessError(1, ["python"]), calls, "cpu"),
    )
    with pytest.raises(SeparationError, match="CPU"):
        separate(audio(), separator, tmp_path)
    assert calls == ["gpu", "cpu"]


def test_child_python_ignores_modules_in_the_current_folder(tmp_path):
    (tmp_path / "numpy.py").write_text("raise SystemExit('hijacked')\n", encoding="utf-8")
    result = subprocess.run(
        [*python_command(), "-c", "import numpy; print(numpy.__name__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_model_files_are_checked_against_their_hashes(tmp_path):
    good = tmp_path / "model.ckpt"
    good.write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    verify_files({good: digest})
    good.write_bytes(b"tampered")
    with pytest.raises(SeparationError, match=r"model\.ckpt"):
        verify_files({good: digest})


def test_library_version_is_part_of_the_cache_key():
    assert separator_id("0.47.1") != separator_id("0.48.0")


def test_cache_key_ignores_the_file_name(tmp_path):
    data = audio().samples
    first = write(tmp_path, "one.wav", data, ANALYSIS_RATE, subtype="FLOAT")
    second = write(tmp_path, "two.wav", data, ANALYSIS_RATE, subtype="FLOAT")
    fake = FakeSeparator()
    separate(to_analysis(decode(first)), fake, tmp_path / "work")
    assert separate(to_analysis(decode(second)), fake, tmp_path / "work").cached
    assert len(fake.calls) == 1


def test_another_process_finishing_first_is_not_an_error(tmp_path):
    source = audio()

    class Racing(FakeSeparator):
        def __call__(self, samples, rate):
            vocal, beat = super().__call__(samples, rate)
            done = tmp_path / "stems" / cache_key(source, self.id)
            done.mkdir(parents=True)
            sf.write(done / "vocal.wav", vocal, rate, subtype="FLOAT")
            sf.write(done / "beat.wav", beat, rate, subtype="FLOAT")
            return vocal, beat

    stems = separate(source, Racing(), tmp_path)
    assert stems.vocal.shape == source.samples.shape
    assert not [p for p in (tmp_path / "stems").iterdir() if p.name.startswith(".tmp")]


def test_download_failure_is_a_clear_error(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(ConnectionError("no route to host"), calls, "gpu"),
        run_on_cpu=stem_writer(calls, "cpu"),
    )
    with pytest.raises(SeparationError, match="download"):
        separate(audio(), separator, tmp_path)
    assert calls == ["gpu"]


def test_cudnn_failure_falls_back_to_the_cpu(tmp_path):
    calls: list[str] = []
    separator = RoformerSeparator(
        run=failing(RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED"), calls, "gpu"),
        run_on_cpu=stem_writer(calls, "cpu"),
    )
    separate(audio(), separator, tmp_path)
    assert calls == ["gpu", "cpu"]


def test_missing_pinned_file_is_a_clear_error(tmp_path):
    with pytest.raises(SeparationError, match=r"absent\.ckpt was not downloaded"):
        verify_files({tmp_path / "absent.ckpt": "0" * 64})


def test_stem_cache_key_follows_the_separation_code():
    source = audio()
    assert cache_key(source, "fake", code="a") != cache_key(source, "fake", code="b")


class FakeEngine:
    """Stands in for audio-separator's Separator and records the order of calls."""

    def __init__(self, model_dir: Path, content: bytes) -> None:
        self.model_dir, self.content, self.calls = model_dir, content, []

    def download_model_files(self, name):
        self.calls.append("download")
        (self.model_dir / name).write_bytes(self.content)

    def load_model(self, model_filename):
        self.calls.append("load")

    def separate(self, source, names):
        self.calls.append("separate")
        return ["vocal.wav", "beat.wav"]


def test_weights_are_verified_before_they_are_loaded(tmp_path):
    pins = {"m.ckpt": hashlib.sha256(b"good").hexdigest()}
    engine = FakeEngine(tmp_path, b"tampered")
    with pytest.raises(SeparationError, match=r"m\.ckpt"):
        run_model(
            tmp_path / "in.wav",
            tmp_path,
            engine=engine,
            model_dir=tmp_path,
            pins=pins,
            model="m.ckpt",
        )
    assert engine.calls == ["download"]
    engine = FakeEngine(tmp_path, b"good")
    run_model(
        tmp_path / "in.wav", tmp_path, engine=engine, model_dir=tmp_path, pins=pins, model="m.ckpt"
    )
    assert engine.calls == ["download", "load", "separate"]


def test_corrupt_cache_entry_is_recomputed(tmp_path):
    source = audio()
    fake = FakeSeparator()
    separate(source, fake, tmp_path)
    entry = tmp_path / "stems" / cache_key(source, fake.id)
    (entry / "vocal.wav").write_bytes(b"")
    stems = separate(source, fake, tmp_path)
    assert len(fake.calls) == 2
    assert not stems.cached
    assert stems.vocal.shape == source.samples.shape


def test_model_writing_no_stems_is_a_clear_error(tmp_path):
    def writes_nothing(source: Path, out: Path) -> tuple[Path, Path]:
        return out / "vocal.wav", out / "beat.wav"

    separator = RoformerSeparator(run=writes_nothing, run_on_cpu=writes_nothing)
    with pytest.raises(SeparationError, match="no readable stems"):
        separate(audio(), separator, tmp_path)
