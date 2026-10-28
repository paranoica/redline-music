import json
from datetime import datetime, timedelta

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, tone, write

from redline.cli import main
from redline.run import FileResult
from redline.vocal_run import (
    BALANCE_DB,
    balance_variants,
    dirt_variants,
    reference_target,
    vocal_run,
)

MARKER_HZ = 7300


class HalfSeparator:
    id = "fake:half"

    def __call__(self, samples, rate):
        return samples * 0.5, samples * 0.5


class ResidueSeparator:
    id = "fake:residue"

    def __call__(self, samples, rate):
        return samples * 0.01, samples * 0.99


def voice(seconds, rate=44100):
    # every harmonic and intermodulation product of 400 Hz and 1.2 kHz is a multiple of
    # 400 Hz, so the drive cannot put anything on the marker
    rng = make_rng()
    n = int(seconds * rate)
    # a smooth envelope: a clipped one has sidebands every 3 Hz that reach the marker
    envelope = (0.65 + 0.35 * np.cos(2 * np.pi * 3 * np.arange(n) / rate))[:, None]
    body = tone(400, seconds, rate, amp=0.3) + tone(1200, seconds, rate, amp=0.1)
    return (body * envelope + 0.0005 * rng.standard_normal((n, 2))).astype(np.float32)


@pytest.fixture
def inputs(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    track = write(folder, "track.wav", voice(8), 44100)
    refs = folder / "refs"
    refs.mkdir()
    for name, amp in (("a.wav", 0.2), ("b.wav", 0.3)):
        write(refs, name, voice(6) + tone(MARKER_HZ, 6, amp=amp), 44100, subtype="FLOAT")
    return track, refs


def run(inputs, tmp_path, **kwargs):
    track, refs = inputs
    return vocal_run(
        track,
        sorted(refs.iterdir()),
        tmp_path / "work",
        tmp_path / "out",
        HalfSeparator(),
        **kwargs,
    )


def line_above_floor_db(data, rate, freq):
    spec = np.abs(np.fft.rfft(data[:, 0] * np.hanning(len(data)))) ** 2
    freqs = np.fft.rfftfreq(len(data), 1 / rate)
    line = spec[np.abs(freqs - freq) <= 3].max()
    floor = np.median(spec[(np.abs(freqs - freq) > 20) & (np.abs(freqs - freq) <= 200)])
    return 10 * np.log10(line / floor)


def test_reference_audio_never_reaches_the_listening_files(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=1)
    files = sorted(result.listen_dir.iterdir())
    assert [f.name for f in files] == ["A.wav", "B.wav", "C.wav", "D.wav", "E.wav"]
    for path in files:
        data, rate = sf.read(path, always_2d=True)
        # the highest noise bin near any frequency reads about 11 dB over the median floor
        assert line_above_floor_db(data, rate, MARKER_HZ) < 20, path.name
    ref, rate = sf.read(sorted(inputs[1].iterdir())[0], always_2d=True)
    assert line_above_floor_db(ref, rate, MARKER_HZ) > 40


def test_key_and_chain_stay_in_the_work_folder(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=2)
    key = json.loads((result.run_dir / "key.json").read_text())
    assert key["seed"] == 2
    assert sorted(key["key"].values()) == ["eq", "heavy", "light", "medium", "original"]
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert [c["level"] for c in chain["chains"]] == ["eq", "light", "medium", "heavy"]
    out_files = [p.name for p in (tmp_path / "out").rglob("*") if p.is_file()]
    assert sorted(out_files) == [".gitignore", "A.wav", "B.wav", "C.wav", "D.wav", "E.wav"]
    assert result.run_dir.is_relative_to(tmp_path / "work")


def test_output_folder_ignores_itself_even_when_out_exists(inputs, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".gitignore").write_text("keep.txt\n")
    result = run(inputs, tmp_path, seed=3)
    marker = result.listen_dir.parent / ".gitignore"
    assert marker.read_text() == "*\n"
    assert (tmp_path / "out" / ".gitignore").read_text() == "keep.txt\n"


def test_original_is_the_untouched_mix(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=4)
    key = json.loads((result.run_dir / "key.json").read_text())["key"]
    letter = next(k for k, v in key.items() if v == "original")
    data, _ = sf.read(result.listen_dir / f"{letter}.wav", always_2d=True)
    source, _ = sf.read(inputs[0], always_2d=True)
    assert np.corrcoef(data[:, 0], source[:, 0])[0, 1] > 0.9999


def test_cli_vocal_prints_the_listening_folder(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["vocal", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o"), "--seed", "5"]
    assert main(args, separator=HalfSeparator()) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.endswith("listen")
    assert len(list((tmp_path / "o").rglob("*.wav"))) == 5


def test_cli_vocal_refuses_an_instrumental(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["vocal", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o")]
    assert main(args, separator=ResidueSeparator()) == 2
    assert "no vocal" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_cli_vocal_without_references_exits_2(inputs, tmp_path, capsys):
    track, _ = inputs
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["vocal", "--track", str(track), "--ref", str(empty), "--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 2


def test_balance_variants_lower_only_the_vocal():
    rng = make_rng()
    vocal = rng.standard_normal((1000, 2)).astype(np.float32)
    beat = rng.standard_normal((1000, 2)).astype(np.float32)
    variants = balance_variants(vocal, beat)
    assert list(variants) == ["vocal 0", "vocal -3", "vocal -6", "vocal -9"]
    for offset, mix in zip(BALANCE_DB, variants.values(), strict=True):
        np.testing.assert_allclose(mix - beat, vocal * 10 ** (-offset / 20), atol=1e-6)


def test_balance_axis_writes_original_and_four_balances(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=6, axis="balance")
    key = json.loads((result.run_dir / "key.json").read_text())
    assert sorted(key["key"].values()) == sorted(
        ["original", "vocal 0", "vocal -3", "vocal -6", "vocal -9"]
    )
    assert key["axis"] == "balance"
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert [c["level"] for c in chain["chains"]] == ["extra"]
    assert chain["balance_db"] == [0, 3, 6, 9]


@pytest.mark.parametrize("out", ["w", "w/runs", "w/runs/x"])
def test_output_inside_the_work_folder_is_refused(inputs, tmp_path, out):
    # the key lives in the work folder: listening files next to it would unblind the test
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        vocal_run(track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / out, HalfSeparator())


def test_work_inside_the_output_folder_is_refused(inputs, tmp_path):
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        vocal_run(
            track, sorted(refs.iterdir()), tmp_path / "o" / "w", tmp_path / "o", HalfSeparator()
        )


def test_a_run_never_writes_into_an_existing_output_folder(inputs, tmp_path):
    out = tmp_path / "out"
    now = datetime.now()
    taken = [out / (now + timedelta(seconds=s)).strftime("%Y%m%d-%H%M%S") for s in range(120)]
    for folder in taken:
        (folder / "listen").mkdir(parents=True)
    result = run(inputs, tmp_path, seed=8)
    assert result.listen_dir.parent not in taken
    assert all(not any((folder / "listen").iterdir()) for folder in taken)


def ref_result(name, curve_db):
    curve = {"centers_hz": [1000.0 * k for k in range(1, len(curve_db) + 1)], "db": curve_db}
    values = {"vocal.lufs": -20.0, "mix.lufs": -14.0}
    return FileResult(name, "wav", 44100, False, values=values, info={"vocal.tonal_curve": curve})


def test_reference_target_is_the_median_curve():
    target = reference_target(
        [ref_result("a", [0.0, -3.0]), ref_result("b", [-2.0, -1.0]), ref_result("c", [-1.0, -9.0])]
    )
    assert target.curve_db == (-1.0, -3.0)


def test_a_reference_without_vocal_is_named():
    with pytest.raises(ValueError, match=r"b\.wav: no vocal"):
        reference_target([ref_result("a.wav", [0.0, -3.0]), ref_result("b.wav", [])])


def test_dirt_variants_lower_the_vocal_only():
    rng = make_rng()
    beat = rng.standard_normal((1000, 2)).astype(np.float32)
    vocal = rng.standard_normal((1000, 2)).astype(np.float32)
    mix = dirt_variants({"heavy": vocal}, beat, vocal_db=-6.0)["heavy"]
    np.testing.assert_allclose(mix - beat, vocal * 10 ** (-6 / 20), atol=1e-6)


def test_dirt_axis_takes_chosen_levels_at_a_chosen_balance(inputs, tmp_path):
    levels = ("medium", "heavy", "extra", "loudest")
    result = run(inputs, tmp_path, seed=10, levels=levels, vocal_db=-6.0)
    key = json.loads((result.run_dir / "key.json").read_text())
    assert sorted(key["key"].values()) == sorted(["original", *levels])
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert [c["level"] for c in chain["chains"]] == list(levels)
    assert chain["vocal_db"] == -6.0


def test_an_unknown_level_is_refused(inputs, tmp_path):
    with pytest.raises(ValueError, match="unknown dirt level"):
        run(inputs, tmp_path, levels=("heavy", "nuclear"))


def test_dirt_variants_add_the_beat_untouched():
    rng = make_rng()
    beat = rng.standard_normal((1000, 2)).astype(np.float32)
    vocals = {name: rng.standard_normal((1000, 2)).astype(np.float32) for name in ("eq", "heavy")}
    variants = dirt_variants(vocals, beat)
    assert list(variants) == ["eq", "heavy"]
    for name, mix in variants.items():
        np.testing.assert_allclose(mix - vocals[name], beat, atol=1e-6)


class ResidueRefsSeparator:
    """The 8 s track keeps its vocal; the 6 s references give only residue."""

    id = "fake:residue-refs"

    def __call__(self, samples, rate):
        share = 0.5 if len(samples) > 7 * rate else 0.0001
        return samples * share, samples * (1 - share)


def test_a_reference_with_only_residue_is_named(inputs, tmp_path):
    track, refs = inputs
    with pytest.raises(ValueError, match=r"a\.wav: no vocal in the reference") as raised:
        vocal_run(
            track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", ResidueRefsSeparator()
        )
    assert "track" not in str(raised.value)


def test_a_quiet_reference_vocal_is_refused_as_the_reference():
    quiet = ref_result("q.wav", [0.0, -3.0])
    quiet.values.update({"vocal.lufs": -50.0, "mix.lufs": -14.0})
    with pytest.raises(ValueError, match=r"q\.wav: no vocal in the reference: ") as raised:
        reference_target([ref_result("a.wav", [0.0, -3.0]), quiet])
    assert "track" not in str(raised.value)


def test_dirt_axis_applies_the_vocal_offset_to_the_files(inputs, tmp_path):
    # the fake beat is the mix itself at half level: with the vocal 60 dB down every file
    # is that beat, whatever the dirt did to the vocal
    result = run(inputs, tmp_path, seed=11, levels=("loudest",), vocal_db=-60.0)
    source, _ = sf.read(inputs[0], always_2d=True)
    for path in sorted(result.listen_dir.iterdir()):
        data, _ = sf.read(path, always_2d=True)
        assert np.corrcoef(data[:, 0], source[:, 0])[0, 1] > 0.999, path.name


def test_no_levels_is_refused(inputs, tmp_path):
    with pytest.raises(ValueError, match="no dirt level"):
        run(inputs, tmp_path, levels=())


@pytest.mark.parametrize("options", [{"levels": ("heavy",)}, {"vocal_db": -6.0}])
def test_dirt_options_on_the_balance_axis_are_refused(inputs, tmp_path, options):
    with pytest.raises(ValueError, match="dirt axis"):
        run(inputs, tmp_path, axis="balance", **options)
