import hashlib
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from redline.flip import stems as stems_module
from redline.flip.stems import (
    DRUM_STEMS,
    FOUR_STEMS,
    DrumSep,
    FourStems,
    cache_key,
    run_named_model,
    separate_named,
    stems_at,
    stems_code,
)
from redline.measure.separate import SeparationError
from redline_signals import make_rng

RATE = 44100


class FakeNamed:
    """Gives every name its own share of the input and counts the calls."""

    def __init__(self, names=FOUR_STEMS, ident="fake:named", shape_delta=0):
        self.id = ident
        self.names = names
        self.calls = 0
        self.shape_delta = shape_delta

    def __call__(self, samples, rate):
        self.calls += 1
        share = np.float32(1 / len(self.names))
        out = {name: samples * share * np.float32(i + 1) for i, name in enumerate(self.names)}
        if self.shape_delta:
            out[self.names[-1]] = out[self.names[-1]][: -self.shape_delta]
        return out


def noise(seconds=1.0, scale=0.3):
    rng = make_rng()
    return (rng.standard_normal((int(seconds * RATE), 2)) * scale).astype(np.float32)


def test_named_stems_round_trip_and_hit(tmp_path):
    samples, fake = noise(), FakeNamed()
    first = separate_named(samples, RATE, fake, tmp_path)
    second = separate_named(samples, RATE, fake, tmp_path)
    assert fake.calls == 1
    assert set(second) == set(FOUR_STEMS)
    for i, name in enumerate(FOUR_STEMS):
        expected = samples * np.float32(0.25) * np.float32(i + 1)
        np.testing.assert_allclose(first[name], expected, atol=1e-6)
        np.testing.assert_array_equal(second[name], first[name])
        assert second[name].shape == samples.shape


def test_other_content_or_separator_misses_the_cache(tmp_path):
    samples, fake = noise(), FakeNamed()
    separate_named(samples, RATE, fake, tmp_path)
    separate_named(noise(scale=0.2), RATE, fake, tmp_path)
    separate_named(samples, RATE, FakeNamed(ident="fake:other"), tmp_path)
    assert fake.calls == 2


def test_code_fingerprint_is_part_of_the_key(monkeypatch):
    samples = noise(0.1)
    assert cache_key(samples, RATE, "x", "code-a") != cache_key(samples, RATE, "x", "code-b")
    before = stems_code()
    monkeypatch.setattr(
        stems_module,
        "library_versions",
        lambda names: {name: "other" for name in names},
    )
    assert stems_code() != before


def test_fingerprint_follows_the_stems_source(monkeypatch):
    before = stems_code()
    real = stems_module.module_sources
    monkeypatch.setattr(stems_module, "module_sources", lambda names: [*real(names), b"edit"])
    assert stems_code() != before


def test_fingerprint_covers_the_separation_libraries(monkeypatch):
    seen: list[list[str]] = []
    real = stems_module.library_versions
    monkeypatch.setattr(
        stems_module, "library_versions", lambda names: seen.append(list(names)) or real(names)
    )
    stems_code()
    assert {"audio-separator", "demucs"} <= set(seen[0])


@pytest.mark.parametrize("victim", FOUR_STEMS)
def test_a_missing_stem_file_is_recomputed(tmp_path, victim):
    samples, fake = noise(), FakeNamed()
    separate_named(samples, RATE, fake, tmp_path)
    entry = tmp_path / "named-stems" / cache_key(samples, RATE, fake.id)
    (entry / f"{victim}.wav").unlink()
    again = separate_named(samples, RATE, fake, tmp_path)
    assert fake.calls == 2
    assert again[victim].shape == samples.shape
    assert (entry / f"{victim}.wav").is_file()


def test_an_unreadable_stem_file_is_recomputed(tmp_path):
    samples, fake = noise(), FakeNamed()
    separate_named(samples, RATE, fake, tmp_path)
    entry = tmp_path / "named-stems" / cache_key(samples, RATE, fake.id)
    (entry / "bass.wav").write_bytes(b"")
    separate_named(samples, RATE, fake, tmp_path)
    assert fake.calls == 2


def test_hot_input_survives_the_cache(tmp_path):
    samples, fake = noise(scale=0.3) * np.float32(5), FakeNamed()
    assert np.abs(samples).max() > 1
    separate_named(samples, RATE, fake, tmp_path)
    again = separate_named(samples, RATE, fake, tmp_path)
    assert fake.calls == 1
    np.testing.assert_allclose(again["vocals"], samples, atol=1e-5)


def test_failed_separation_leaves_no_entry(tmp_path):
    class Boom(FakeNamed):
        def __call__(self, samples, rate):
            raise SeparationError("boom")

    with pytest.raises(SeparationError):
        separate_named(noise(), RATE, Boom(), tmp_path)
    root = tmp_path / "named-stems"
    assert not root.exists() or not list(root.glob("*"))


def test_a_stem_of_the_wrong_shape_is_refused_and_not_cached(tmp_path):
    drums = noise()
    with pytest.raises(ValueError, match=r"crash"):
        separate_named(drums, RATE, FakeNamed(DRUM_STEMS, shape_delta=7), tmp_path)
    root = tmp_path / "named-stems"
    assert not root.exists() or not list(root.glob("*"))


def test_a_missing_name_is_refused(tmp_path):
    class Short(FakeNamed):
        def __call__(self, samples, rate):
            out = super().__call__(samples, rate)
            del out["hh"]
            return out

    with pytest.raises(SeparationError, match="hh"):
        separate_named(noise(), RATE, Short(DRUM_STEMS), tmp_path)


def test_drum_stems_keep_the_drums_shape(tmp_path):
    drums = noise(0.5)
    out = separate_named(drums, RATE, FakeNamed(DRUM_STEMS), tmp_path)
    assert list(out) == list(DRUM_STEMS)
    assert all(stem.shape == drums.shape for stem in out.values())


def test_resample_once_to_48k():
    seconds = 2
    stems = {"a": noise(seconds), "b": noise(seconds, 0.1)}
    out = stems_at(stems, RATE, 48000)
    for stem in out.values():
        assert stem.dtype == np.float32
        assert stem.shape == (48000 * seconds, 2)
    assert stems["a"].shape == (RATE * seconds, 2)


def test_resample_keeps_the_pitch():
    t = np.arange(RATE) / RATE
    tone = np.sin(2 * np.pi * 1000 * t).astype(np.float32)[:, None].repeat(2, axis=1)
    out = stems_at({"a": tone}, RATE)["a"]
    peak = np.argmax(np.abs(np.fft.rfft(out[:, 0])))
    assert abs(peak - 1000) <= 1


def test_same_rate_is_a_copy_of_the_values():
    stem = noise(0.1)
    out = stems_at({"a": stem}, 48000)["a"]
    np.testing.assert_array_equal(out, stem)


class FakeEngine:
    """Stands in for the audio-separator engine; the 'download' lands in the model dir."""

    def __init__(self, model_dir: Path, files: dict[str, bytes], out: Path, names):
        self.model_dir, self.files, self.out, self.names = model_dir, files, out, names
        self.calls: list[str] = []

    def download_model_files(self, name):
        self.calls.append("download")
        for file, content in self.files.items():
            (self.model_dir / file).write_bytes(content)

    def load_model(self, model_filename):
        self.calls.append("load")

    def separate(self, source, names):
        self.calls.append("separate")
        written = []
        for name in self.names:
            sf.write(self.out / f"{names[name]}.wav", np.zeros((10, 2), np.float32), RATE)
            written.append(f"{names[name]}.wav")
        return written


def pins_of(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}


@pytest.mark.parametrize("kind,names", [("four", FOUR_STEMS), ("drumsep", DRUM_STEMS)])
def test_pinned_model_files_load_and_name_their_stems(tmp_path, kind, names):
    files = {"a.th": b"one", "b.yaml": b"two"}
    engine = FakeEngine(tmp_path, files, tmp_path, names)
    paths = run_named_model(
        tmp_path / "in.wav", tmp_path, kind, engine=engine, model_dir=tmp_path, pins=pins_of(files)
    )
    assert engine.calls == ["download", "load", "separate"]
    assert list(paths) == list(names)
    assert all(path.is_file() for path in paths.values())


@pytest.mark.parametrize("kind", ["four", "drumsep"])
def test_a_changed_model_file_is_refused_before_loading(tmp_path, kind):
    pins = pins_of({"a.th": b"one", "b.yaml": b"two"})
    engine = FakeEngine(tmp_path, {"a.th": b"one", "b.yaml": b"TAMPERED"}, tmp_path, ())
    with pytest.raises(SeparationError, match=r"b\.yaml"):
        run_named_model(
            tmp_path / "in.wav", tmp_path, kind, engine=engine, model_dir=tmp_path, pins=pins
        )
    assert engine.calls == ["download"]


def test_a_pinned_file_that_never_arrived_is_refused(tmp_path):
    pins = pins_of({"a.th": b"one", "b.yaml": b"two"})
    engine = FakeEngine(tmp_path, {"a.th": b"one"}, tmp_path, ())
    with pytest.raises(SeparationError, match=r"b\.yaml"):
        run_named_model(
            tmp_path / "in.wav", tmp_path, "four", engine=engine, model_dir=tmp_path, pins=pins
        )
    assert engine.calls == ["download"]


def test_the_default_pins_cover_every_model_file():
    four = stems_module.FOUR_STEMS_SHA256
    assert set(four) == {
        "htdemucs_ft.yaml",
        "04573f0d-f3cf25b2.th",
        "92cfc3b6-ef3bcb9c.th",
        "d12395a8-e57c48e6.th",
        "f7e0c4bc-ba3fe64a.th",
    }
    drum = stems_module.DRUMSEP_SHA256
    assert set(drum) == {"MDX23C-DrumSep-aufr33-jarredou.ckpt", "config_drumsep_mdx23c.yaml"}
    for digest in (*four.values(), *drum.values()):
        assert len(digest) == 64 and int(digest, 16) >= 0


def test_default_pins_refuse_an_unpinned_fake_dir(tmp_path):
    engine = FakeEngine(tmp_path, {"htdemucs_ft.yaml": b"not the pinned file"}, tmp_path, ())
    with pytest.raises(SeparationError, match=r"htdemucs_ft\.yaml"):
        run_named_model(tmp_path / "in.wav", tmp_path, "four", engine=engine, model_dir=tmp_path)
    assert "load" not in engine.calls


def test_separator_ids_differ_and_name_the_model():
    four, drum = FourStems(), DrumSep()
    assert four.id != drum.id
    assert "htdemucs_ft" in four.id and "DrumSep" in drum.id
    assert four.names == FOUR_STEMS and drum.names == DRUM_STEMS


def write_stems(names, calls):
    def run(source: Path, out: Path) -> dict[str, Path]:
        calls.append(str(out))
        data, rate = sf.read(source, always_2d=True, dtype="float32")
        for name in names:
            sf.write(out / f"{name}.wav", data[:-3], rate, subtype="FLOAT")
        return {name: out / f"{name}.wav" for name in names}

    return run


def test_model_output_is_fitted_to_the_input_length(tmp_path):
    calls: list[str] = []
    separator = DrumSep(run=write_stems(DRUM_STEMS, calls), run_on_cpu=write_stems((), []))
    samples = noise(0.2)
    out = separate_named(samples, RATE, separator, tmp_path)
    assert all(stem.shape == samples.shape for stem in out.values())
    assert set(out) == set(DRUM_STEMS)


def test_stems_the_model_writes_at_its_own_rate_come_back_at_the_inputs(tmp_path):
    from scipy.signal import resample_poly

    def at_44k(source: Path, out: Path) -> dict[str, Path]:
        data, rate = sf.read(source, always_2d=True, dtype="float32")
        model = resample_poly(data, 44100, rate, axis=0).astype(np.float32)
        for name in DRUM_STEMS:
            sf.write(out / f"{name}.wav", model, 44100, subtype="FLOAT")
        return {name: out / f"{name}.wav" for name in DRUM_STEMS}

    rate = 48000
    t = np.arange(rate) / rate
    click = np.zeros(rate, dtype=np.float32)
    click[rate // 2] = 1.0
    samples = np.stack([np.sin(2 * np.pi * 1000 * t).astype(np.float32), click], axis=1)
    separator = DrumSep(run=at_44k, run_on_cpu=write_stems((), []))
    kick = separate_named(samples, rate, separator, tmp_path)["kick"]
    assert kick.shape == samples.shape
    spectrum = np.abs(np.fft.rfft(kick[:, 0]))
    assert abs(np.argmax(spectrum) * rate / len(kick) - 1000) < 5
    # a click half way in stays half way in, not 8.8 % early
    assert abs(int(np.argmax(np.abs(kick[:, 1]))) - rate // 2) < 10


def test_gpu_failure_falls_back_to_the_cpu(tmp_path):
    def fail(source, out):
        raise RuntimeError("CUDA out of memory")

    cpu: list[str] = []
    separator = FourStems(run=fail, run_on_cpu=write_stems(FOUR_STEMS, cpu))
    out = separate_named(noise(0.2), RATE, separator, tmp_path)
    assert separator.fallback_used and len(cpu) == 1
    assert set(out) == set(FOUR_STEMS)


def test_other_model_errors_do_not_retry_on_the_cpu(tmp_path):
    def fail(source, out):
        raise RuntimeError("shape mismatch")

    cpu: list[str] = []
    separator = FourStems(run=fail, run_on_cpu=write_stems(FOUR_STEMS, cpu))
    with pytest.raises(SeparationError, match="shape mismatch"):
        separate_named(noise(0.2), RATE, separator, tmp_path)
    assert cpu == []


def test_a_model_writing_too_few_stems_is_a_clear_error(tmp_path):
    separator = FourStems(run=write_stems(("drums", "bass"), []), run_on_cpu=write_stems((), []))
    with pytest.raises(SeparationError, match="no readable stems"):
        separate_named(noise(0.2), RATE, separator, tmp_path)
