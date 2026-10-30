import json
import re
from dataclasses import asdict, replace

import numpy as np
import pytest
import soundfile as sf
from redline.cli import main
from redline.engine.settings import Settings, to_dict
from redline.vocal.auto import AutoResult
from redline_signals import make_rng, write

RATE = 44100
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
CHOSEN = replace(Settings(), vocal_db=-9.0, comp_threshold_db=-44.0, drive_db=20.0)
PICKED = AutoResult(
    CHOSEN,
    12.5,
    3.25,
    {"vocal.crest_db": 11.0, "vocal.kurtosis": None},
    {"vocal.crest_db": (10.0, 1.0)},
    (),
    {"vocal.crest_db": 1.5},
)


class CountingSeparator:
    id = "fake:counting"

    def __init__(self):
        self.lengths = []

    def __call__(self, samples, rate):
        self.lengths.append(len(samples))
        return samples * 0.5, samples * 0.5


def voice(seconds, amp=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    mono = amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    return np.column_stack([mono, mono]).astype(np.float32)


def kick(seconds, amp=0.5):
    rng = make_rng()
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    mono = amp * np.sin(2 * np.pi * 55 * t) * np.exp(-((t % 0.5) / 0.15))
    mono = mono + 0.0005 * rng.standard_normal(n)
    return np.column_stack([mono, mono]).astype(np.float32)


@pytest.fixture
def own(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    vocal = write(folder, "my vocal.wav", voice(4), RATE, subtype="FLOAT")
    beat = write(folder, "beat.wav", kick(4), RATE, subtype="FLOAT")
    refs = folder / "refs"
    refs.mkdir()
    write(refs, "a.wav", voice(3, amp=0.2) + kick(3), RATE, subtype="FLOAT")
    write(refs, "b.wav", voice(5, amp=0.4) + kick(5), RATE, subtype="FLOAT")
    return vocal, beat, refs


@pytest.fixture
def auto(monkeypatch):
    from redline.vocal import auto as auto_module

    calls = []

    def fake(engine, refs, *args, **kwargs):
        calls.append((engine, list(refs), kwargs.get("separator")))
        return PICKED

    monkeypatch.setattr(auto_module, "auto_settings", fake)
    return calls


@pytest.fixture
def no_auto(monkeypatch):
    from redline.vocal import auto as auto_module

    def refuse(*args, **kwargs):
        raise AssertionError("the auto search ran")

    monkeypatch.setattr(auto_module, "auto_settings", refuse)


def voice_args(own, tmp_path, *extra):
    vocal, beat, refs = own
    args = ["voice", "--vocal", str(vocal), "--beat", str(beat), "--ref", str(refs)]
    return [*args, "--work", str(tmp_path / "w"), "--out", str(tmp_path / "o"), *extra]


def as_record(settings):
    # through JSON, so the rack's tuple compares as the list chain.json holds
    return json.loads(json.dumps(asdict(settings)))


def only_result(tmp_path):
    (folder,) = list((tmp_path / "o").iterdir())
    return folder, json.loads((folder / "chain.json").read_text())


def test_voice_writes_the_folder_named_after_the_vocal_and_separates_only_refs(
    own, tmp_path, auto, capsys
):
    separator = CountingSeparator()
    assert main(voice_args(own, tmp_path), separator=separator) == 0
    folder, _ = only_result(tmp_path)
    assert re.fullmatch(r"\d{8}-\d{6}-my_vocal", folder.name)
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert files == SPEC_FILES
    # the vocal and the beat are 4 s; only the 3 s and 5 s references go through separation
    assert sorted(separator.lengths) == [3 * RATE, 5 * RATE]
    original, rate = sf.read(folder / "original.wav", always_2d=True)
    assert rate == RATE
    given = (voice(4) + kick(4))[:, 0]
    assert np.corrcoef(original[:, 0], given)[0, 1] > 0.9999
    assert capsys.readouterr().out.splitlines()[0] == str(folder)


def test_voice_runs_the_auto_search_by_default_and_records_it(own, tmp_path, auto, capsys):
    separator = CountingSeparator()
    assert main(voice_args(own, tmp_path), separator=separator) == 0
    (call,) = auto
    engine, refs, given = call
    # the search calibrates on the separator the references went through
    assert given is separator
    np.testing.assert_allclose(engine.vocal, voice(4), atol=1e-7)
    np.testing.assert_allclose(engine.beat, kick(4), atol=1e-7)
    assert [r.name for r in refs] == ["a.wav", "b.wav"]
    folder, record = only_result(tmp_path)
    assert record["settings"] == as_record(CHOSEN)
    assert record["vocal_db"] == -9.0
    assert record["auto"] == {
        "settings": as_record(CHOSEN),
        "distance_before": 12.5,
        "distance_after": 3.25,
        "values": {"vocal.crest_db": 11.0, "vocal.kurtosis": None},
        "targets": {"vocal.crest_db": [10.0, 1.0]},
        "at_edge": [],
        "bias": {"vocal.crest_db": 1.5},
    }
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        str(folder),
        "vocal_db: -9.0",
        "comp_threshold_db: -44.0",
        "drive_db: 20.0",
        "distance: 12.50 → 3.25",
    ]


def test_voice_names_the_keys_left_at_the_edge_of_their_range(own, tmp_path, monkeypatch, capsys):
    from redline.vocal import auto as auto_module

    edged = replace(PICKED, at_edge=("vocal_db", "drive_db"))
    monkeypatch.setattr(auto_module, "auto_settings", lambda *args, **kwargs: edged)
    assert main(voice_args(own, tmp_path), separator=CountingSeparator()) == 0
    folder, record = only_result(tmp_path)
    assert record["auto"]["at_edge"] == ["vocal_db", "drive_db"]
    assert capsys.readouterr().out.splitlines() == [
        str(folder),
        "vocal_db: -9.0",
        "comp_threshold_db: -44.0",
        "drive_db: 20.0",
        "distance: 12.50 → 3.25",
        "at the edge of its range: vocal_db, drive_db",
    ]


def test_voice_without_auto_renders_the_defaults(own, tmp_path, no_auto, capsys):
    args = voice_args(own, tmp_path, "--no-auto")
    assert main(args, separator=CountingSeparator()) == 0
    folder, record = only_result(tmp_path)
    assert record["settings"] == as_record(Settings())
    assert "auto" not in record
    assert capsys.readouterr().out.strip() == str(folder)


def test_voice_takes_a_preset_without_the_auto_search(own, tmp_path, no_auto):
    presets = tmp_path / "w" / "studio" / "presets"
    presets.mkdir(parents=True)
    (presets / "dry.json").write_text(json.dumps({"drive_db": 10.0, "doubles_on": False}))
    assert main(voice_args(own, tmp_path, "--preset", "dry"), separator=CountingSeparator()) == 0
    _, record = only_result(tmp_path)
    assert record["settings"] == as_record(replace(Settings(), drive_db=10.0, doubles_on=False))
    assert "auto" not in record


def test_voice_takes_a_settings_file_without_the_auto_search(own, tmp_path, no_auto):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"vocal_db": -3.0}))
    args = voice_args(own, tmp_path, "--settings", str(path))
    assert main(args, separator=CountingSeparator()) == 0
    _, record = only_result(tmp_path)
    assert record["settings"] == as_record(replace(Settings(), vocal_db=-3.0))
    assert "auto" not in record


def test_voice_takes_a_rack_from_a_settings_file(own, tmp_path, no_auto):
    pytest.importorskip("redline_dsp")
    plain, racked = tmp_path / "plain.json", tmp_path / "rack.json"
    plain.write_text(json.dumps({"vocal_db": -3.0}))
    rack = [{"effect": "gain", "params": {"db": -12}}]
    racked.write_text(json.dumps({"version": 2, "vocal_db": -3.0, "rack": rack}))
    stems = []
    for path, out in ((plain, "o1"), (racked, "o2")):
        args = voice_args(own, tmp_path, "--settings", str(path))
        args[args.index("--out") + 1] = str(tmp_path / out)
        assert main(args, separator=CountingSeparator()) == 0
        (folder,) = list((tmp_path / out).iterdir())
        record = json.loads((folder / "chain.json").read_text())
        stems.append((sf.read(folder / "stems" / "vocal.wav")[0], record))
    (before, _), (after, record) = stems
    assert record["settings"]["rack"][0]["effect"] == "gain"
    # the rack is the vocal's last stage: its stem is the same vocal 12 dB down
    np.testing.assert_allclose(after, before * 10 ** (-12 / 20), atol=1e-6)


def test_voice_refuses_a_rack_with_an_unknown_effect(own, tmp_path, no_auto, capsys):
    pytest.importorskip("redline_dsp")
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"version": 2, "rack": [{"effect": "flanger9"}]}))
    assert main(voice_args(own, tmp_path, "--settings", str(path))) == 2
    assert "flanger9" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_voice_refuses_a_missing_preset(own, tmp_path, no_auto, capsys):
    args = voice_args(own, tmp_path, "--preset", "missing")
    assert main(args, separator=CountingSeparator()) == 2
    assert capsys.readouterr().err.startswith("redline: ")
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ("--no-auto", "--preset", "x"),
        ("--no-auto", "--settings", "s.json"),
        ("--preset", "x", "--settings", "s.json"),
    ],
)
def test_voice_takes_one_source_of_settings(own, tmp_path, capsys, extra):
    with pytest.raises(SystemExit):
        main(voice_args(own, tmp_path, *extra), separator=CountingSeparator())
    assert "not allowed with" in capsys.readouterr().err


def test_voice_offset_reaches_measure_own(own, tmp_path, no_auto, monkeypatch):
    from redline.measure import run as measure_run

    seen = []
    measure_own = measure_run.measure_own

    def measured(*args, **kwargs):
        seen.append(args[5] if len(args) > 5 else kwargs.get("offset_s"))
        return measure_own(*args, **kwargs)

    monkeypatch.setattr(measure_run, "measure_own", measured)
    args = voice_args(own, tmp_path, "--no-auto", "--offset", "0.5")
    assert main(args, separator=CountingSeparator()) == 0
    assert seen == [0.5]
    folder, _ = only_result(tmp_path)
    # the 4 s vocal delayed by half a second runs past the 4 s beat
    assert sf.info(folder / "final.wav").frames == int(4.5 * RATE)


def test_voice_without_an_offset_measures_at_zero(own, tmp_path, no_auto, monkeypatch):
    from redline.measure import run as measure_run

    seen = []
    measure_own = measure_run.measure_own

    def measured(*args, **kwargs):
        seen.append(args[5] if len(args) > 5 else kwargs.get("offset_s"))
        return measure_own(*args, **kwargs)

    monkeypatch.setattr(measure_run, "measure_own", measured)
    assert main(voice_args(own, tmp_path, "--no-auto"), separator=CountingSeparator()) == 0
    assert seen == [0.0]


@pytest.mark.parametrize("broken", ["missing beat", "offset past the vocal", "no references"])
def test_voice_errors_exit_2_with_the_reason(own, tmp_path, no_auto, capsys, broken):
    _, beat, refs = own
    if broken == "missing beat":
        beat.unlink()
        args = voice_args(own, tmp_path, "--no-auto")
    elif broken == "offset past the vocal":
        args = voice_args(own, tmp_path, "--no-auto", "--offset", "-5")
    else:
        for path in refs.iterdir():
            path.unlink()
        args = voice_args(own, tmp_path, "--no-auto")
    assert main(args, separator=CountingSeparator()) == 2
    assert capsys.readouterr().err.startswith("redline: ")
    assert not (tmp_path / "o").exists() or not any((tmp_path / "o").iterdir())


@pytest.fixture
def served(monkeypatch):
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
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    return started


def studio_args(own, tmp_path, *extra):
    vocal, beat, refs = own
    args = ["studio", "--vocal", str(vocal), "--beat", str(beat), "--ref", str(refs)]
    return [*args, "--work", str(tmp_path / "w"), "--no-browser", *extra]


def test_studio_opens_on_the_own_stems_with_the_auto_settings(own, tmp_path, auto, served):
    separator = CountingSeparator()
    assert main(studio_args(own, tmp_path), separator=separator) == 0
    assert auto[0][2] is separator
    studio = served["studio"]
    np.testing.assert_allclose(studio.engine.vocal, voice(4), atol=1e-7)
    np.testing.assert_allclose(studio.engine.beat, kick(4), atol=1e-7)
    np.testing.assert_allclose(studio.original, voice(4) + kick(4), atol=1e-6)
    assert studio.folder == tmp_path / "w" / "studio"
    assert studio.track_key.startswith("my vocal-")
    saved = studio.folder / studio.track_key / "settings.json"
    assert json.loads(saved.read_text()) == to_dict(CHOSEN)
    assert len(auto) == 1 and served["closed"]


def test_studio_keeps_saved_settings_of_the_own_pair(own, tmp_path, auto, served):
    assert main(studio_args(own, tmp_path), separator=CountingSeparator()) == 0
    studio = served["studio"]
    saved = studio.folder / studio.track_key / "settings.json"
    mine = json.dumps({**asdict(Settings()), "drive_db": 10.0})
    saved.write_text(mine)
    assert main(studio_args(own, tmp_path), separator=CountingSeparator()) == 0
    assert served["studio"].track_key == studio.track_key
    assert saved.read_text() == mine
    assert len(auto) == 1


def test_studio_offset_reaches_measure_own(own, tmp_path, auto, served, monkeypatch):
    from redline.measure import run as measure_run

    seen = []
    measure_own = measure_run.measure_own

    def measured(*args, **kwargs):
        seen.append(args[5] if len(args) > 5 else kwargs.get("offset_s"))
        return measure_own(*args, **kwargs)

    monkeypatch.setattr(measure_run, "measure_own", measured)
    assert main(studio_args(own, tmp_path, "--offset", "0.5"), separator=CountingSeparator()) == 0
    assert seen == [0.5]
    assert len(served["studio"].engine.vocal) == int(4.5 * RATE)


def test_studio_on_a_track_runs_no_auto_search_and_saves_nothing(own, tmp_path, auto, served):
    _, _, refs = own
    track = write(tmp_path / "in", "track.wav", voice(4) + kick(4), RATE, subtype="FLOAT")
    args = ["studio", "--track", str(track), "--ref", str(refs), "--work", str(tmp_path / "w")]
    assert main([*args, "--no-browser"], separator=CountingSeparator()) == 0
    assert served["studio"].track_key.startswith("track-")
    assert auto == []
    assert not list((tmp_path / "w" / "studio").rglob("settings.json"))


@pytest.mark.parametrize(
    ("given", "said"),
    [
        (("--vocal", "v.wav"), "--vocal needs --beat"),
        (("--track", "t.wav", "--vocal", "v.wav", "--beat", "b.wav"), "not allowed with"),
        (("--track", "t.wav", "--beat", "b.wav"), "--beat goes with --vocal"),
        (("--track", "t.wav", "--offset", "0.5"), "--offset goes with --vocal"),
        ((), "one of the arguments --track --vocal is required"),
    ],
)
def test_studio_refuses_a_wrong_mix_of_inputs(tmp_path, capsys, given, said):
    args = ["studio", *given, "--ref", str(tmp_path), "--work", str(tmp_path / "w")]
    with pytest.raises(SystemExit) as raised:
        main(args, separator=CountingSeparator())
    assert raised.value.code == 2
    assert said in capsys.readouterr().err


@pytest.mark.parametrize("command", ["voice", "studio"])
@pytest.mark.parametrize("offset", ["inf", "-inf", "nan", "1e7", "3600", "-3600", "abc"])
def test_an_offset_that_is_not_a_plausible_number_of_seconds_exits_2(
    own, tmp_path, capsys, command, offset
):
    made = voice_args if command == "voice" else studio_args
    with pytest.raises(SystemExit) as raised:
        # with "=": argparse takes a bare -inf for an option
        main(made(own, tmp_path, f"--offset={offset}"), separator=CountingSeparator())
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "argument --offset: " in err
    assert "Traceback" not in err
    assert "seconds" in err.splitlines()[-1]
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("command", ["voice", "studio"])
@pytest.mark.parametrize(("offset", "seconds"), [("3599.5", 3599.5), ("-3599.5", -3599.5)])
def test_an_offset_just_under_an_hour_is_taken(command, offset, seconds):
    from redline.cli.parser import build_parser

    args = ["--vocal", "v.wav", "--beat", "b.wav", "--ref", "r", "--offset", offset]
    assert build_parser().parse_args([command, *args]).offset == seconds


def test_studio_names_the_keys_left_at_the_edge_of_their_range(
    own, tmp_path, served, monkeypatch, capsys
):
    from redline.vocal import auto as auto_module

    edged = replace(PICKED, at_edge=("comp_threshold_db",))
    monkeypatch.setattr(auto_module, "auto_settings", lambda *args, **kwargs: edged)
    assert main(studio_args(own, tmp_path), separator=CountingSeparator()) == 0
    out = capsys.readouterr().out.splitlines()
    assert "at the edge of its range: comp_threshold_db" in out
    assert (
        out.index("at the edge of its range: comp_threshold_db")
        == out.index("distance: 12.50 → 3.25") + 1
    )
