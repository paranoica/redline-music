import json
from dataclasses import asdict, replace

import numpy as np
import pytest
import soundfile as sf
from redline.cli import main
from redline.core.loudness import integrated_lufs
from redline.core.spectrum import low_end
from redline.engine.settings import Settings
from redline.master.clip import CEILING_DB
from redline.master.run import (
    BASS_DRIVES_DB,
    TIMBRE_EFFECTS,
    VOICES,
    bass_round,
    master_round,
    master_targets,
    run_track,
    timbre_round,
    timbre_settings,
)
from redline.measure.run import FileResult
from redline_signals import make_rng, tone, write
from scipy.ndimage import maximum_filter1d
from scipy.signal import butter, sosfiltfilt


class HalfSeparator:
    id = "fake:half"

    def __call__(self, samples, rate):
        return samples * 0.5, samples * 0.5


def song(seconds, rate=44100, amp=0.3):
    rng = make_rng()
    n = int(seconds * rate)
    t = np.arange(n) / rate
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    envelope = 0.65 + 0.35 * np.cos(2 * np.pi * 3 * t)
    voice = np.sin(2 * np.pi * 400 * t) * envelope * 0.5
    mono = amp * (kick + voice) + 0.0005 * rng.standard_normal(n)
    return np.column_stack([mono, mono]).astype(np.float32)


@pytest.fixture
def inputs(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    track = write(folder, "track.wav", song(8), 44100)
    refs = folder / "refs"
    refs.mkdir()
    write(refs, "a.wav", song(6, amp=0.6), 44100, subtype="FLOAT")
    write(refs, "b.wav", song(6, amp=0.9), 44100, subtype="FLOAT")
    return track, refs


def ref(name, lufs):
    return FileResult(name, "wav", 44100, False, values={"mix.lufs": lufs})


def test_master_targets_span_the_references():
    targets = master_targets([ref("a", -9.5), ref("b", -8.9), ref("c", -4.8)])
    assert targets == {"master -11.0": -11.0, "master -8.9": -8.9, "master -4.8": -4.8}


def test_master_targets_need_reference_loudness():
    with pytest.raises(ValueError, match="loudness"):
        master_targets([ref("a", None), ref("b", -9.0)])


def run(inputs, tmp_path, **kwargs):
    track, refs = inputs
    return master_round(
        track,
        sorted(refs.iterdir()),
        tmp_path / "work",
        tmp_path / "out",
        HalfSeparator(),
        **kwargs,
    )


def test_master_round_writes_the_original_and_four_masters(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=1)
    key = json.loads((result.run_dir / "key.json").read_text())
    names = sorted(key["key"].values())
    assert names[-1] == "original" and "no master" in names
    assert len([n for n in names if n.startswith("master ")]) == 3
    levels = []
    for path in sorted(result.listen_dir.iterdir()):
        data, rate = sf.read(path, always_2d=True)
        levels.append(integrated_lufs(data, rate))
    assert max(levels) - min(levels) <= 0.1
    chain = json.loads((result.run_dir / "chain.json").read_text())
    masters = chain["masters"]
    assert sorted(m["name"] for m in masters) == sorted(n for n in names if n.startswith("master "))
    for m in masters:
        assert m["reached"] is True
        assert m["final_lufs"] == pytest.approx(m["target_lufs"], abs=0.2)
    variants = chain["variants"]
    assert sorted(variants) == sorted(n for n in names if n != "original")
    assert variants["no master"] == asdict(replace(Settings(), master_on=False))
    for m in masters:
        fixed = replace(Settings(), master_auto=False, master_lufs=m["target_lufs"])
        assert variants[m["name"]] == asdict(fixed)


def test_master_round_masters_the_chosen_sound(inputs, tmp_path):
    track, refs = inputs
    result = master_round(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), seed=3
    )
    letters = {
        v: k for k, v in json.loads((result.run_dir / "key.json").read_text())["key"].items()
    }
    heard, _ = sf.read(result.listen_dir / f"{letters['no master']}.wav", always_2d=True)
    folder = run_track(
        track, sorted(refs.iterdir()), tmp_path / "w2", tmp_path / "o2", HalfSeparator()
    )
    vocal, _ = sf.read(folder / "stems" / "vocal.wav", always_2d=True)
    beat, _ = sf.read(folder / "stems" / "beat.wav", always_2d=True)
    # the listening file is the final's mix before the master, only at another level
    assert np.corrcoef(heard[:, 0], (vocal + beat)[:, 0])[0, 1] > 0.999
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert (chain["timbre"], chain["bass_drive_db"]) == ("octave down + doubles", 12.0)


def test_louder_targets_clip_more(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=2)
    masters = json.loads((result.run_dir / "chain.json").read_text())["masters"]
    shares = [m["clipped_share"] for m in sorted(masters, key=lambda m: m["target_lufs"])]
    assert shares == sorted(shares)
    assert shares[-1] > shares[0]


def test_master_round_refuses_overlapping_folders(inputs, tmp_path):
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        master_round(
            track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "w" / "o", HalfSeparator()
        )


def test_cli_master_prints_the_listening_folder(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["master", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o"), "--seed", "3"]
    assert main(args, separator=HalfSeparator()) == 0
    assert capsys.readouterr().out.strip().endswith("listen")


def test_cli_master_without_references_exits_2(inputs, tmp_path):
    track, _ = inputs
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["master", "--track", str(track), "--ref", str(empty), "--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 2


@pytest.mark.parametrize(
    ("axis", "timbres"),
    [("808", "doubles"), ("master", "doubles"), ("timbre", ","), ("timbre", "")],
)
def test_cli_master_refuses_timbres_it_cannot_use(inputs, tmp_path, axis, timbres):
    track, refs = inputs
    args = ["master", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o"), "--axis", axis, "--timbres", timbres]
    assert main(args, separator=HalfSeparator()) == 2
    assert not (tmp_path / "o").exists()


SPEC_FILES = [
    ".gitignore",
    "chain.json",
    "compare/final.wav",
    "compare/original.wav",
    "final.wav",
    "original.wav",
    "report.md",
    "stems/beat.wav",
    "stems/vocal.wav",
]


MARKER_HZ = 7300


@pytest.fixture
def hot_inputs(tmp_path):
    # references clipped hard and loud, with a marker tone the track lacks: the run has to
    # clip to reach them, and nothing of theirs may reach the result
    folder = tmp_path / "hot"
    folder.mkdir()
    track = write(folder, "track.wav", song(8), 44100)
    refs = folder / "refs"
    refs.mkdir()
    for name, amp in (("a.wav", 2.0), ("b.wav", 3.0)):
        loud = np.clip(song(6, amp=amp) + tone(MARKER_HZ, 6, amp=0.2), -1, 1)
        write(refs, name, loud, 44100, subtype="FLOAT")
    return track, refs


def make_track(hot_inputs, tmp_path):
    track, refs = hot_inputs
    return run_track(track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator())


def test_run_writes_the_spec_folder(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert files == SPEC_FILES
    assert (folder / ".gitignore").read_text() == "*\n"
    assert folder.is_relative_to(tmp_path / "o")


def test_final_is_hot_and_original_matches_its_loudness(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    chain = json.loads((folder / "chain.json").read_text())
    final, rate = sf.read(folder / "final.wav", always_2d=True)
    original, _ = sf.read(folder / "original.wav", always_2d=True)
    target = chain["master"]["target_lufs"]
    assert integrated_lufs(final, rate) == pytest.approx(target, abs=0.5)
    assert 20 * np.log10(np.abs(final).max()) <= CEILING_DB + 0.01
    assert integrated_lufs(original, rate) == pytest.approx(integrated_lufs(final, rate), abs=0.2)
    assert sf.info(folder / "original.wav").subtype == "FLOAT"


def test_compare_pair_is_at_one_safe_loudness(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    levels = []
    for name in ("final", "original"):
        data, rate = sf.read(folder / "compare" / f"{name}.wav", always_2d=True)
        levels.append(integrated_lufs(data, rate))
        assert 20 * np.log10(np.abs(data).max()) < 0
    assert levels[0] == pytest.approx(levels[1], abs=0.1)


def test_stems_rebuild_the_mix_before_the_master(inputs, tmp_path):
    # quiet references: a light master leaves stretches the clip never touches
    folder = make_track(inputs, tmp_path)
    vocal, _ = sf.read(folder / "stems" / "vocal.wav", always_2d=True)
    beat, _ = sf.read(folder / "stems" / "beat.wav", always_2d=True)
    chain = json.loads((folder / "chain.json").read_text())
    final, _ = sf.read(folder / "final.wav", always_2d=True)
    gain = 10 ** (chain["master"]["gain_db"] / 20)
    # away from the clipped stretches, final is the stems' sum times the master gain; the
    # master also band-limits at 20 kHz, so both sides are compared below that
    loud = np.abs((vocal + beat) * gain).max(axis=1)
    calm = maximum_filter1d(loud, size=2001) < 0.5
    assert calm.mean() > 0.05
    band = butter(8, 18000, fs=44100, output="sos")
    expected = sosfiltfilt(band, (vocal + beat) * gain, axis=0)
    error = np.abs(sosfiltfilt(band, final, axis=0)[calm] - expected[calm])
    # a gain or stem mistake moves most samples; a few band-limit residues are allowed
    assert np.mean(error < 2e-3) > 0.999


WORDS = {
    "ближе",
    "дальше",
    "так же",
    "в диапазоне",
    "вне диапазона",
    "мало референсов",
    "мало данных",
}


def test_report_says_what_was_done_in_words(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    text = (folder / "report.md").read_text(encoding="utf-8")
    for phrase in ("Вокал", "октав", "808", "Мастер", "медианн", "срезано"):
        assert phrase in text
    assert "лучше" not in text
    rows = [line for line in text.splitlines() if line.startswith("| ") and "(" in line]
    assert len(rows) == 4
    for row in rows:
        word = row.split("(")[1].split(")")[0]
        assert word in WORDS, row
    final_lufs = json.loads((folder / "chain.json").read_text())["master"]["final_lufs"]
    assert f"{final_lufs:.1f} LUFS" in text


def test_report_says_the_beat_was_left_alone_without_drive(hot_inputs, tmp_path):
    from redline.engine.settings import from_dict

    track, refs = hot_inputs
    folder = run_track(
        track,
        sorted(refs.iterdir()),
        tmp_path / "w",
        tmp_path / "o",
        HalfSeparator(),
        from_dict({"bass_drive_db": 0.0}),
    )
    text = (folder / "report.md").read_text(encoding="utf-8")
    assert "**Бит:** не трогал." in text
    assert "808" not in text


def test_reference_audio_never_reaches_the_result(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    for name in (
        "final.wav",
        "original.wav",
        "compare/final.wav",
        "compare/original.wav",
        "stems/vocal.wav",
        "stems/beat.wav",
    ):
        data, rate = sf.read(folder / name, always_2d=True)
        spec = np.abs(np.fft.rfft(data[:, 0] * np.hanning(len(data)))) ** 2
        freqs = np.fft.rfftfreq(len(data), 1 / rate)
        line = spec[np.abs(freqs - MARKER_HZ) <= 3].max()
        floor = np.median(
            spec[(np.abs(freqs - MARKER_HZ) > 20) & (np.abs(freqs - MARKER_HZ) <= 200)]
        )
        assert 10 * np.log10(line / floor) < 20, name


def test_run_clips_to_reach_hot_references(hot_inputs, tmp_path):
    chain = json.loads((make_track(hot_inputs, tmp_path) / "chain.json").read_text())
    assert chain["master"]["clipped_share"] > 0
    assert chain["timbre"] == "octave down + doubles"


def test_run_drives_the_808_and_keeps_its_level(inputs, tmp_path):
    track, refs = inputs
    folder = run_track(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator()
    )
    assert json.loads((folder / "chain.json").read_text())["bass_drive_db"] == 12.0
    played, rate = sf.read(track, always_2d=True)
    beat, _ = sf.read(folder / "stems" / "beat.wav", always_2d=True)
    before, after = low_end(played * 0.5, rate), low_end(beat, rate)
    assert after.share_db == pytest.approx(before.share_db, abs=1.5)

    def third_over_fundamental(x):
        spec = np.abs(np.fft.rfft(x[:, 0])) ** 2
        freqs = np.fft.rfftfreq(len(x), 1 / rate)
        third = spec[(freqs > 150) & (freqs < 180)].sum()
        return 10 * np.log10(third / spec[(freqs > 45) & (freqs < 65)].sum())

    # tanh on the kick's 55 Hz adds its odd harmonics; this kick is quiet, so only a few dB
    assert third_over_fundamental(beat) > third_over_fundamental(played * 0.5) + 1


def test_cli_run_without_references_exits_2(inputs, tmp_path):
    track, _ = inputs
    empty = tmp_path / "empty"
    empty.mkdir()
    args = ["run", "--track", str(track), "--ref", str(empty), "--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 2


def test_master_targets_drop_duplicates():
    targets = master_targets([ref("a", -9.0), ref("b", -9.0)])
    assert targets == {"master -11.0": -11.0, "master -9.0": -9.0}


def test_cli_run_prints_the_folder(inputs, tmp_path, capsys):
    track, refs = inputs
    args = ["run", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o")]
    assert main(args, separator=HalfSeparator()) == 0
    printed = capsys.readouterr().out.strip()
    (folder,) = list((tmp_path / "o").iterdir())
    assert printed == str(folder)


def test_run_masters_to_the_references_median(hot_inputs, tmp_path):
    folder = make_track(hot_inputs, tmp_path)
    chain = json.loads((folder / "chain.json").read_text())
    refs = sorted(hot_inputs[1].iterdir())
    levels = [integrated_lufs(sf.read(r, always_2d=True)[0], 44100) for r in refs]
    assert chain["master"]["target_lufs"] == pytest.approx(round(float(np.median(levels)), 1))


def test_master_round_output_holds_only_the_letters(inputs, tmp_path):
    result = run(inputs, tmp_path, seed=4)
    out_files = sorted(
        str(p.relative_to(tmp_path / "out")) for p in (tmp_path / "out").rglob("*") if p.is_file()
    )
    stamp = result.listen_dir.parent.name
    expected = [f"{stamp}/.gitignore"] + [f"{stamp}/listen/{c}.wav" for c in "ABCDE"]
    assert out_files == expected


@pytest.mark.parametrize(("work", "out"), [("w", "w/o"), ("o/w", "o")])
def test_run_refuses_overlapping_folders(inputs, tmp_path, work, out):
    track, refs = inputs
    with pytest.raises(ValueError, match="separate"):
        run_track(track, sorted(refs.iterdir()), tmp_path / work, tmp_path / out, HalfSeparator())


def test_timbre_round_writes_the_original_the_base_and_each_effect(inputs, tmp_path):
    track, refs = inputs
    result = timbre_round(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), seed=5
    )
    key = json.loads((result.run_dir / "key.json").read_text())
    assert key["axis"] == "timbre"
    assert sorted(key["key"].values()) == sorted(["original", "base", *TIMBRE_EFFECTS])
    assert len(list(result.listen_dir.iterdir())) == 2 + len(TIMBRE_EFFECTS)
    levels = [integrated_lufs(*sf.read(f, always_2d=True)) for f in result.listen_dir.iterdir()]
    assert max(levels) - min(levels) <= 0.1
    variants = json.loads((result.run_dir / "chain.json").read_text())["variants"]
    bare = replace(Settings(), octave_down_on=False, doubles_on=False)
    assert variants == {
        "base": asdict(bare),
        "doubles": asdict(replace(bare, doubles_on=True)),
        "octave down": asdict(replace(bare, octave_down_on=True)),
        "bitcrush": asdict(replace(bare, bitcrush_on=True)),
    }


def test_timbre_round_plays_the_chosen_timbre_as_the_final(inputs, tmp_path):
    track, refs = inputs
    result = timbre_round(
        track,
        sorted(refs.iterdir()),
        tmp_path / "w",
        tmp_path / "o",
        HalfSeparator(),
        6,
        ("octave down + doubles",),
    )
    letters = {
        v: k for k, v in json.loads((result.run_dir / "key.json").read_text())["key"].items()
    }
    heard, _ = sf.read(
        result.listen_dir / f"{letters['octave down + doubles']}.wav", always_2d=True
    )
    folder = run_track(
        track, sorted(refs.iterdir()), tmp_path / "w2", tmp_path / "o2", HalfSeparator()
    )
    final, _ = sf.read(folder / "final.wav", always_2d=True)
    assert np.corrcoef(heard[:, 0], final[:, 0])[0, 1] > 0.999


def test_timbre_round_takes_chosen_combinations(inputs, tmp_path):
    track, refs = inputs
    chosen = ("octave down + doubles", "bitcrush + doubles")
    result = timbre_round(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), 6, chosen
    )
    key = json.loads((result.run_dir / "key.json").read_text())
    assert sorted(key["key"].values()) == sorted(["original", "base", *chosen])


@pytest.mark.parametrize(
    "timbre", ["chipmunk", "octave sideways", "pitched up", "doubles + octave sideways"]
)
def test_an_unknown_timbre_is_refused(inputs, tmp_path, timbre):
    track, refs = inputs
    with pytest.raises(ValueError, match="unknown timbre"):
        timbre_round(
            track,
            sorted(refs.iterdir()),
            tmp_path / "w",
            tmp_path / "o",
            HalfSeparator(),
            6,
            (timbre,),
        )


def test_bass_round_holds_the_loudness_and_steps_the_808_drive(inputs, tmp_path):
    track, refs = inputs
    result = bass_round(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), seed=7
    )
    key = json.loads((result.run_dir / "key.json").read_text())
    assert key["axis"] == "808"
    names = [f"808 +{d:.0f} dB" for d in BASS_DRIVES_DB]
    assert sorted(key["key"].values()) == sorted(["original", *names])
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert chain["variants"] == {
        f"808 +{d:.0f} dB": asdict(replace(Settings(), bass_drive_db=d)) for d in (0, 6, 12, 18, 24)
    }
    masters = {m["name"]: m for m in chain["masters"]}
    assert sorted(masters) == sorted(names)
    finals = [m["final_lufs"] for m in masters.values()]
    assert max(finals) - min(finals) <= 0.5
    for name in names:
        assert "low_crest_db" in masters[name]


def keys_of(result):
    return json.loads((result.run_dir / "key.json").read_text())


def test_cli_voice_round_writes_the_chosen_timbre_with_each_new_voice(inputs, tmp_path):
    track, refs = inputs
    args = ["master", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--out", str(tmp_path / "o"), "--seed", "4", "--axis", "voice"]
    assert main(args, separator=HalfSeparator()) == 0
    run_dir = next((tmp_path / "w" / "runs").iterdir())
    key = json.loads((run_dir / "key.json").read_text())
    assert key["axis"] == "voice"
    # the owner tells the original apart every time, so the voice round leaves it out
    assert sorted(key["key"].values()) == sorted(["base", *VOICES])
    assert len(VOICES) == 4
    assert all("octave down + doubles" in v for v in VOICES)
    # the base is the chosen timbre; each voice changes one thing on top of it
    variants = json.loads((run_dir / "chain.json").read_text())["variants"]
    chosen = Settings()
    assert variants == {
        "base": asdict(chosen),
        "formant x1.35 + octave down + doubles": asdict(replace(chosen, formant=1.35)),
        "formant x0.78 + octave down + doubles": asdict(replace(chosen, formant=0.78)),
        "voice up + octave down + doubles": asdict(replace(chosen, voice_octave=1)),
        "nightcore + octave down + doubles": asdict(replace(chosen, nightcore=True)),
    }
    letters = list(key["key"])
    assert sorted(p.name for p in (tmp_path / "o").rglob("*.wav")) == [f"{c}.wav" for c in letters]


def test_cli_voice_round_takes_named_voices_and_refuses_unknown_ones(inputs, tmp_path):
    track, refs = inputs
    args = ["master", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    args += ["--axis", "voice", "--timbres"]
    out = ["--out", str(tmp_path / "o")]
    assert main([*args, "doubles + formant down", *out], separator=HalfSeparator()) == 0
    run_dir = next((tmp_path / "w" / "runs").iterdir())
    assert json.loads((run_dir / "key.json").read_text())["axis"] == "voice"
    bad = ["--out", str(tmp_path / "o2")]
    assert main([*args, "formant sideways", *bad], separator=HalfSeparator()) == 2
    assert not (tmp_path / "o2").exists()


def test_a_voice_round_hands_the_tracks_measured_tuning_to_hard_tune(inputs, tmp_path, monkeypatch):
    from redline.engine import render as engine
    from redline.master import run as final_run

    measure = final_run.measure_inputs

    def measured(*args):
        result = measure(*args)
        result.track.info["vocal.tuning_cents"] = -23.0
        return result

    seen = []
    monkeypatch.setattr(final_run, "measure_inputs", measured)
    monkeypatch.setattr(
        engine, "hard_tune", lambda x, rate, cents, strength: seen.append((cents, strength)) or x
    )
    track, refs = inputs
    result = timbre_round(
        track,
        sorted(refs.iterdir()),
        tmp_path / "w",
        tmp_path / "o",
        HalfSeparator(),
        1,
        ("hard tune",),
        "voice",
    )
    # a full snap on the track's own tuning, only in the variant that names it
    assert seen == [(-23.0, 1.0)]
    chain = json.loads((result.run_dir / "chain.json").read_text())
    assert chain["tuning_cents"] == -23.0
    assert chain["variants"]["hard tune"]["hard_tune"] == 100.0
    assert chain["variants"]["base"]["hard_tune"] == 0.0


def test_hard_tune_in_a_round_snaps_to_the_tracks_own_tuning(inputs, tmp_path, monkeypatch):
    from redline.engine import render as engine
    from redline.master import run as final_run

    measure = final_run.measure_inputs
    tunings = []

    def measured(*args):
        result = measure(*args)
        tunings.append(result.track.info["vocal.tuning_cents"])
        return result

    seen = []
    monkeypatch.setattr(final_run, "measure_inputs", measured)
    monkeypatch.setattr(
        engine, "hard_tune", lambda x, rate, cents, strength: seen.append(cents) or x
    )
    _, refs = inputs
    # a held 400 Hz with its harmonics over a light kick, long enough for the pitch tracker
    rng = make_rng()
    n = 12 * 44100
    t = np.arange(n) / 44100
    voice = 0.5 * np.sin(2 * np.pi * 400 * t) + 0.2 * np.sin(2 * np.pi * 800 * t)
    voice += 0.1 * np.sin(2 * np.pi * 1200 * t)
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    mono = 0.3 * voice + 0.1 * kick + 0.0005 * rng.standard_normal(n)
    track = write(tmp_path, "held.wav", np.column_stack([mono, mono]).astype(np.float32), 44100)
    timbre_round(
        track,
        sorted(refs.iterdir()),
        tmp_path / "w",
        tmp_path / "o",
        HalfSeparator(),
        1,
        ("hard tune",),
        "voice",
    )
    (tuning,) = tunings
    # 400 Hz is 165 cents under A440, so 35 cents above the equal-tempered grid
    assert tuning == pytest.approx(1200 * np.log2(400 / 440) + 200, abs=3.0)
    assert seen == [tuning]


def test_each_named_effect_switches_on_its_setting():
    bare = replace(Settings(), octave_down_on=False, doubles_on=False)
    # the timbre the rounds picked is the default chain
    assert timbre_settings("octave down + doubles") == Settings()
    assert timbre_settings("doubles") == replace(bare, doubles_on=True)
    assert timbre_settings("octave up") == replace(bare, octave_up_on=True)
    assert timbre_settings("octave up").octave_up_db == -9.5
    crushed = timbre_settings("bitcrush")
    assert crushed == replace(bare, bitcrush_on=True)
    assert (crushed.bitcrush_bits, crushed.bitcrush_hold) == (8, 3)
    assert timbre_settings("formant up") == replace(bare, formant=1.2)
    assert timbre_settings("formant down") == replace(bare, formant=0.85)
    assert timbre_settings("hard tune") == replace(bare, hard_tune=100.0)
    assert timbre_settings("voice up + nightcore") == replace(bare, voice_octave=1, nightcore=True)


def test_effects_render_in_the_engines_order_whatever_order_they_are_named_in():
    assert timbre_settings("doubles + formant down") == timbre_settings("formant down + doubles")
    assert timbre_settings("bitcrush + doubles") == timbre_settings("doubles + bitcrush")


@pytest.mark.parametrize("ratio", ["0.6", "1.8", "1.35", "0.78"])
def test_a_formant_ratio_in_the_settings_range_can_be_named(ratio):
    assert timbre_settings(f"formant x{ratio}").formant == float(ratio)


@pytest.mark.parametrize(
    "wild",
    ["formant x0.59", "formant x1.81", "formant x0.5", "formant x2", "formant x", "formant xabc"],
)
def test_a_formant_ratio_outside_the_settings_range_is_refused(wild):
    with pytest.raises(ValueError, match="unknown timbre"):
        timbre_settings(wild)


@pytest.mark.parametrize(
    "twice", ["formant x1.35 + formant x0.78", "formant up + formant down", "doubles + doubles"]
)
def test_one_setting_named_twice_is_refused(twice):
    with pytest.raises(ValueError, match="twice"):
        timbre_settings(twice)


def test_a_round_refuses_a_setting_named_twice_before_writing(inputs, tmp_path):
    track, refs = inputs
    with pytest.raises(ValueError, match="twice"):
        timbre_round(
            track,
            sorted(refs.iterdir()),
            tmp_path / "w",
            tmp_path / "o",
            HalfSeparator(),
            6,
            ("formant up + formant x1.3",),
        )
    assert not (tmp_path / "o").exists() and not (tmp_path / "w").exists()


def test_run_with_settings_uses_them_and_the_tracks_tuning(inputs, tmp_path, monkeypatch):
    from redline.engine import render as engine
    from redline.engine.settings import from_dict

    seen = []
    monkeypatch.setattr(
        engine, "formant_shift", lambda x, rate, r: seen.append(("formant", r)) or x
    )
    monkeypatch.setattr(
        engine, "hard_tune", lambda x, rate, cents, s: seen.append(("tune", cents, s)) or x
    )
    track, refs = inputs
    settings = from_dict({"formant": 1.35, "hard_tune": 100, "master_on": False})
    folder = run_track(
        track, sorted(refs.iterdir()), tmp_path / "w", tmp_path / "o", HalfSeparator(), settings
    )
    assert ("formant", 1.35) in seen
    tune = [s for s in seen if s[0] == "tune"]
    assert len(tune) == 1 and tune[0][2] == 1.0
    record = json.loads((folder / "chain.json").read_text())
    assert record["settings"]["formant"] == 1.35 and record["master"] is None
    assert tune[0][1] == record["tuning_cents"]
    assert "мастер" in (folder / "report.md").read_text(encoding="utf-8").lower()


def test_cli_run_takes_a_settings_file_and_refuses_a_bad_one(inputs, tmp_path):
    track, refs = inputs
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"doubles_on": False}), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"volume": 3}), encoding="utf-8")
    args = ["run", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    assert (
        main(
            [*args, "--out", str(tmp_path / "o"), "--settings", str(good)],
            separator=HalfSeparator(),
        )
        == 0
    )
    record = json.loads(next((tmp_path / "o").glob("*/chain.json")).read_text())
    assert record["settings"]["doubles_on"] is False
    assert (
        main(
            [*args, "--out", str(tmp_path / "o2"), "--settings", str(bad)],
            separator=HalfSeparator(),
        )
        == 2
    )
    assert not (tmp_path / "o2").exists()


def test_cli_studio_prints_a_local_address_with_a_token(inputs, tmp_path, capsys, monkeypatch):
    from redline.studio import server as studio_server

    started = {}

    class Fake:
        server_address = ("127.0.0.1", 5555)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            started["closed"] = True

    def fake_make(studio, token, port=0):
        started.update(studio=studio, token=token, port=port)
        return Fake()

    monkeypatch.setattr(studio_server, "make_server", fake_make)
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    track, refs = inputs
    args = ["studio", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    assert main([*args, "--no-browser"], separator=HalfSeparator()) == 0
    out = capsys.readouterr().out
    first = started["token"]
    assert main(args, separator=HalfSeparator()) == 0
    assert len(opened) == 1 and opened[0].startswith("file://")
    assert started["token"] not in opened[0]
    opened.clear()
    assert f"http://127.0.0.1:5555/?t={first}" in out
    assert len(started["token"]) >= 32 and started["closed"]
    assert started["studio"].folder == tmp_path / "w" / "studio"


@pytest.fixture
def takes(inputs, tmp_path):
    _, refs = inputs
    folder = tmp_path / "takes"
    folder.mkdir()
    write(folder, "take one.wav", song(8), 44100)
    write(folder, "take two.wav", song(8, amp=0.2), 44100)
    return folder, refs


def run_args(tracks, refs, tmp_path, *extra):
    args = ["run", "--track", *map(str, tracks), "--ref", str(refs)]
    return [*args, "--work", str(tmp_path / "w"), "--out", str(tmp_path / "o"), *extra]


def test_cli_run_takes_a_folder_and_names_each_result_after_its_take(takes, tmp_path, capsys):
    folder, refs = takes
    assert main(run_args([folder], refs, tmp_path), separator=HalfSeparator()) == 0
    printed = capsys.readouterr().out.split("\n")
    made = sorted((tmp_path / "o").iterdir())
    assert [p.name.split("-", 2)[2] for p in made] == ["take_one", "take_two"]
    assert sorted(p for p in printed if p) == [str(p) for p in made]
    for result in made:
        assert (result / "final.wav").is_file()


def test_cli_run_goes_on_past_a_broken_take_and_exits_2(takes, tmp_path, capsys):
    folder, refs = takes
    (folder / "broken.wav").write_bytes(b"not audio at all")
    assert main(run_args([folder], refs, tmp_path), separator=HalfSeparator()) == 2
    assert len(list((tmp_path / "o").iterdir())) == 2
    err = capsys.readouterr().err
    assert "broken.wav" in err and "1 of 3" in err


def test_cli_run_takes_a_preset_the_studio_saved(inputs, tmp_path):
    track, refs = inputs
    presets = tmp_path / "w" / "studio" / "presets"
    presets.mkdir(parents=True)
    (presets / "мой пресет.json").write_text(
        json.dumps({"doubles_on": False, "formant": 1.35}, ensure_ascii=False), "utf-8"
    )
    args = run_args([track], refs, tmp_path, "--preset", "мой пресет")
    assert main(args, separator=HalfSeparator()) == 0
    record = json.loads(next((tmp_path / "o").glob("*/chain.json")).read_text())
    assert record["settings"]["doubles_on"] is False and record["settings"]["formant"] == 1.35


@pytest.mark.parametrize("name", ["missing", "../presets/x", ""])
def test_cli_run_refuses_a_preset_it_cannot_find(inputs, tmp_path, capsys, name):
    track, refs = inputs
    presets = tmp_path / "w" / "studio" / "presets"
    presets.mkdir(parents=True)
    (presets / "x.json").write_text("{}", "utf-8")
    args = run_args([track], refs, tmp_path, "--preset", name)
    assert main(args, separator=HalfSeparator()) == 2
    assert "x" in capsys.readouterr().err.split("saved:")[-1]
    assert not (tmp_path / "o").exists()


def test_cli_run_takes_a_preset_or_a_settings_file_not_both(inputs, tmp_path, capsys):
    track, refs = inputs
    args = run_args([track], refs, tmp_path, "--preset", "x", "--settings", "s.json")
    with pytest.raises(SystemExit):
        main(args, separator=HalfSeparator())
    assert "not allowed with" in capsys.readouterr().err


def test_cli_run_with_no_audio_in_the_folder_exits_2(inputs, tmp_path):
    _, refs = inputs
    empty = tmp_path / "none"
    empty.mkdir()
    assert main(run_args([empty], refs, tmp_path), separator=HalfSeparator()) == 2
