import json

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, tone, write
from scipy.ndimage import maximum_filter1d
from scipy.signal import butter, sosfiltfilt

from redline.cli import main
from redline.final_run import (
    BASS_DRIVES_DB,
    TIMBRES,
    VOICES,
    bass_round,
    master_round,
    master_targets,
    run_track,
    timbre_round,
)
from redline.loudness import integrated_lufs
from redline.master import CEILING_DB
from redline.run import FileResult
from redline.spectrum import low_end


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


def test_report_says_the_beat_was_left_alone_without_drive(hot_inputs, tmp_path, monkeypatch):
    monkeypatch.setattr("redline.final_run.BASS_DRIVE_DB", 0.0)
    text = (make_track(hot_inputs, tmp_path) / "report.md").read_text(encoding="utf-8")
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
    assert sorted(key["key"].values()) == sorted(["original", "base", *TIMBRES])
    assert len(list(result.listen_dir.iterdir())) == 2 + len(TIMBRES)


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
    "timbre", ["nightcore", "octave sideways", "pitched up", "doubles + octave sideways"]
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
    assert sorted(key["key"].values()) == sorted(["original", "base", *VOICES])
    assert len(VOICES) == 3
    assert all("octave down + doubles" in v for v in VOICES)


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


def test_hard_tune_in_a_round_snaps_to_the_tracks_own_tuning(monkeypatch):
    from redline import final_run

    seen = []
    monkeypatch.setattr(final_run, "hard_tune", lambda x, rate, cents: seen.append(cents) or x)
    vocal = np.zeros((100, 2), dtype=np.float32)
    final_run._with_timbre("hard tune", vocal, 44100, tuning_cents=-17.0)
    assert seen == [-17.0]
