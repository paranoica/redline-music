import gc
import json
import weakref
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import numpy as np
import pytest
import soundfile as sf
from redline.cli import main
from redline.core.listen import write_listen_set
from redline.core.loudness import integrated_lufs
from redline.engine.render import Engine
from redline.engine.settings import Settings, from_dict
from redline.engine.track import reference_target
from redline.measure.run import FileResult
from redline.vocal.chain import ALL_LEVELS, process_vocal
from redline.vocal.rounds import blind_round, level_settings, vocal_run
from redline_signals import RATE, engine_context, engine_stems, make_rng, tone, write

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
    variants = chain["variants"]
    assert {name: s["vocal_db"] for name, s in variants.items()} == {
        "vocal 0": 0.0,
        "vocal -3": -3.0,
        "vocal -6": -6.0,
        "vocal -9": -9.0,
    }
    # extra: compressor -34 dB 20:1 1/50 ms, drive 24 dB, clip 9 dB under the peak
    for s in variants.values():
        chain_part = (s["comp_threshold_db"], s["comp_ratio"], s["comp_attack_ms"])
        assert chain_part == (-34.0, 20.0, 1.0) and s["comp_release_ms"] == 50.0
        assert (s["drive_db"], s["clip_on"], s["clip_below_peak_db"]) == (24.0, True, 9.0)


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


@pytest.fixture
def engine():
    vocal, beat = engine_stems()
    return Engine(vocal, beat, engine_context(vocal))


def test_blind_round_writes_every_variant_at_one_level_with_the_key_and_settings(engine, tmp_path):
    plain = from_dict({"master_on": False, "doubles_on": False})
    variants = {"original": engine.vocal + engine.beat, "plain": plain, "mastered": Settings()}
    result, renders = blind_round(
        engine,
        variants,
        tmp_path / "w",
        tmp_path / "o",
        "test",
        seed=9,
        record=lambda rendered: {"rendered": sorted(rendered)},
    )
    key = json.loads((result.run_dir / "key.json").read_text())
    assert (key["axis"], key["seed"]) == ("test", 9)
    assert sorted(key["key"].values()) == ["mastered", "original", "plain"]
    assert key["listen_dir"] == str(result.listen_dir)
    files = sorted(result.listen_dir.iterdir())
    assert [f.name for f in files] == ["A.wav", "B.wav", "C.wav"]
    levels = [integrated_lufs(sf.read(f, always_2d=True)[0], RATE) for f in files]
    assert max(levels) - min(levels) <= 0.1
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert chain["rendered"] == ["mastered", "plain"]
    assert chain["variants"] == {"plain": asdict(plain), "mastered": asdict(Settings())}
    assert sorted(renders) == ["mastered", "plain"]
    letters = {name: letter for letter, name in key["key"].items()}
    heard, _ = sf.read(result.listen_dir / f"{letters['plain']}.wav", always_2d=True)
    assert np.corrcoef(heard[:, 0], renders["plain"].final[:, 0])[0, 1] > 0.9999
    heard, _ = sf.read(result.listen_dir / f"{letters['original']}.wav", always_2d=True)
    assert np.corrcoef(heard[:, 0], (engine.vocal + engine.beat)[:, 0])[0, 1] > 0.9999
    out_files = sorted(p.name for p in (tmp_path / "o").rglob("*") if p.is_file())
    assert out_files == [".gitignore", "A.wav", "B.wav", "C.wav"]
    assert result.run_dir.is_relative_to(tmp_path / "w")


def test_a_round_drops_each_variants_stems_once_it_is_rendered(engine, tmp_path):
    alive = []

    class Watched(Engine):
        def render(self, settings, cancelled=lambda: False):
            gc.collect()
            # the variants before this one are down to their finals already
            assert all(ref() is None for ref in alive)
            rendered = super().render(settings, cancelled)
            # copies, so the engine's own stage cache does not keep them alive
            vocal, beat = rendered.vocal.copy(), rendered.beat.copy()
            alive.extend([weakref.ref(vocal), weakref.ref(beat)])
            return replace(rendered, vocal=vocal, beat=beat)

    watched = Watched(engine.vocal, engine.beat, engine.context)
    plain = from_dict({"master_on": False, "doubles_on": False})

    def record(kept):
        gc.collect()
        assert all(ref() is None for ref in alive)
        return {"masters": [asdict(k.master) for k in kept.values() if k.master is not None]}

    variants = {"plain": plain, "mastered": Settings(), "quiet": replace(plain, vocal_db=-12.0)}
    result, kept = blind_round(watched, variants, tmp_path / "w", tmp_path / "o", "t", 3, record)
    assert len(alive) == 6
    assert sorted(kept) == ["mastered", "plain", "quiet"]
    assert all(k.vocal is None for k in kept.values())
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert len(chain["masters"]) == 1


def test_a_round_writes_what_its_full_renders_would_have_given(engine, tmp_path):
    plain = from_dict({"master_on": False, "doubles_on": False})
    variants = {"original": engine.vocal + engine.beat, "plain": plain, "mastered": Settings()}

    def record(renders):
        # reads what the stage 3 rounds read: the chain, the master and the final's loudness
        return {
            name: {
                "chain": asdict(r.chain),
                "master": None if r.master is None else asdict(r.master),
                "final_lufs": integrated_lufs(r.final, RATE),
            }
            for name, r in renders.items()
        }

    result, _ = blind_round(engine, variants, tmp_path / "w", tmp_path / "o", "t", 11, record)
    fresh = Engine(engine.vocal, engine.beat, engine.context)
    full = {n: fresh.render(v) for n, v in variants.items() if isinstance(v, Settings)}
    expected = {
        **record(full),
        "variants": {"plain": asdict(plain), "mastered": asdict(Settings())},
    }
    # through JSON, so tuples compare as the lists the file holds
    assert json.loads((result.run_dir / "chain.json").read_text()) == json.loads(
        json.dumps(expected)
    )
    mixes = {n: full[n].final if n in full else v for n, v in variants.items()}
    write_listen_set(mixes, RATE, tmp_path / "expected", 11)
    files = sorted(p.name for p in result.listen_dir.iterdir())
    assert files == sorted(p.name for p in (tmp_path / "expected").iterdir())
    for name in files:
        assert (result.listen_dir / name).read_bytes() == (
            tmp_path / "expected" / name
        ).read_bytes()


@pytest.mark.parametrize("name", ["eq", "light", "heavy", "loudest"])
def test_a_level_renders_its_vocal_lowered_over_the_untouched_beat(engine, name):
    ctx = engine.context
    rendered = engine.render(level_settings(ALL_LEVELS[name], -6.0))
    processed, _ = process_vocal(engine.vocal, RATE, ALL_LEVELS[name], ctx.target, ctx.upper)
    assert rendered.chain.level == name
    assert np.array_equal(rendered.vocal, processed * np.float32(10 ** (-6.0 / 20)))
    assert np.array_equal(rendered.beat, engine.beat)
    assert np.array_equal(rendered.final, rendered.vocal + engine.beat)


def test_dirt_round_records_each_levels_settings(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=12, levels=("eq", "heavy"), vocal_db=-3.0)
    variants = json.loads((result.run_dir / "chain.json").read_text())["variants"]
    assert sorted(variants) == ["eq", "heavy"]
    assert variants["eq"]["chain_on"] is False
    heavy = variants["heavy"]
    assert heavy["chain_on"] is True
    # heavy: compressor -30 dB 10:1 2/60 ms, drive 18 dB, clip 6 dB under the peak
    comp = (heavy["comp_threshold_db"], heavy["comp_ratio"], heavy["comp_attack_ms"])
    assert comp == (-30.0, 10.0, 2.0) and heavy["comp_release_ms"] == 60.0
    assert (heavy["drive_db"], heavy["clip_on"], heavy["clip_below_peak_db"]) == (18.0, True, 6.0)
    for s in variants.values():
        assert s["vocal_db"] == -3.0 and s["eq_fit"] is True
        assert (s["master_on"], s["bass_drive_db"], s["beat_db"]) == (False, 0.0, 0.0)
        assert not any(
            s[k] for k in ("octave_down_on", "octave_up_on", "doubles_on", "bitcrush_on")
        )
        assert (s["hard_tune"], s["formant"], s["voice_octave"], s["nightcore"]) == (0, 1, 0, False)
