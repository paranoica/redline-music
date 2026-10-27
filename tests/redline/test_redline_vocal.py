import json

import numpy as np
import pytest
import soundfile as sf
from redline_signals import make_rng, tone, write

from redline.cli import main
from redline.vocal_run import vocal_run

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
