import json

import pytest
from redline.cli import main
from redline.takes import rank as rank_module
from redline.takes.rank import rank_folder
from redline.takes.report import write_outputs
from takes_signals import click_beat, put, vocal


@pytest.fixture
def beat(tmp_path):
    return put(tmp_path, "beat.wav", click_beat())


def test_one_take_gives_a_report_without_a_ranking(tmp_path, beat, capsys):
    folder = tmp_path / "input" / "takes" / "song"
    put(folder, "take-01.wav", vocal(1))
    work = tmp_path / "work"
    code = main(["takes", "--beat", str(beat), "--dir", str(folder), "--work", str(work)])
    assert code == 0
    data = json.loads((work / "takes" / "song" / "ranking.json").read_text(encoding="utf-8"))
    assert data["ranked"] is False
    assert [t["name"] for t in data["takes"]] == ["take-01"]
    assert data["takes"][0]["rank"] is None
    report = (work / "takes" / "song" / "report.md").read_text(encoding="utf-8")
    assert "take-01" in report
    out = capsys.readouterr().out
    assert "redline voice" in out and "take-01.wav" in out


def test_the_cli_writes_the_ranking_and_points_voice_at_the_cleanest(tmp_path, beat, capsys):
    folder = tmp_path / "input" / "takes" / "song"
    put(folder, "take-01.wav", vocal(1, shift_s=0.07))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3))
    refs = tmp_path / "refs"
    refs.mkdir()
    work = tmp_path / "work"
    args = ["takes", "--beat", str(beat), "--dir", str(folder), "--ref", str(refs)]
    assert main([*args, "--work", str(work)]) == 0
    out_dir = work / "takes" / "song"
    data = json.loads((out_dir / "ranking.json").read_text(encoding="utf-8"))
    assert data["ranked"] is True
    assert [t["rank"] for t in data["takes"]] == [1, 2, 3]
    assert data["takes"][-1]["name"] == "take-01"
    best = data["takes"][0]["name"]
    assert best in ("take-02", "take-03")
    assert (out_dir / ".gitignore").is_file()
    report = (out_dir / "report.md").read_text(encoding="utf-8")
    assert "лучше звучит" not in report and "sounds better" not in report
    out = capsys.readouterr().out
    voice_line = next(line for line in out.splitlines() if "redline voice" in line)
    assert f"{best}.wav" in voice_line and str(refs) in voice_line


def test_deep_adds_the_distance_to_the_references(tmp_path, beat, monkeypatch, capsys):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    refs = tmp_path / "refs"
    put(refs, "a.wav", click_beat())
    calls = []

    def fake(take, beat_path, ref_paths, work, separator):
        calls.append((take.name, [p.name for p in ref_paths]))
        return {"take-01.wav": 40.0, "take-02.wav": 1.0}[take.name]

    monkeypatch.setattr(rank_module, "deep_distance", fake)
    args = ["takes", "--beat", str(beat), "--dir", str(folder), "--ref", str(refs), "--deep"]
    assert main([*args, "--work", str(tmp_path / "work")]) == 0
    assert calls == [("take-01.wav", ["a.wav"]), ("take-02.wav", ["a.wav"])]
    data = json.loads((tmp_path / "work/takes/takes/ranking.json").read_text(encoding="utf-8"))
    assert [t["name"] for t in data["takes"]] == ["take-02", "take-01"]
    assert data["takes"][1]["metrics"]["deep_distance"] == 40.0
    assert data["takes"][1]["points"]["deep"] > data["takes"][0]["points"]["deep"]


def test_deep_needs_references(tmp_path, beat, capsys):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    assert main(["takes", "--beat", str(beat), "--dir", str(folder), "--deep"]) == 2
    assert "--deep needs --ref" in capsys.readouterr().err


def test_a_missing_folder_exits_2(tmp_path, beat, capsys):
    code = main(["takes", "--beat", str(beat), "--dir", str(tmp_path / "nope")])
    assert code == 2
    assert "nope" in capsys.readouterr().err


def test_a_folder_without_takes_exits_2(tmp_path, beat, capsys):
    folder = tmp_path / "takes"
    folder.mkdir()
    put(folder, "vocal.wav", vocal(1))
    code = main(["takes", "--beat", str(beat), "--dir", str(folder)])
    assert code == 2
    assert "no take-NN.wav" in capsys.readouterr().err


def test_a_missing_beat_exits_2(tmp_path, capsys):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    code = main(["takes", "--beat", str(tmp_path / "gone.wav"), "--dir", str(folder)])
    assert code == 2
    assert "gone.wav" in capsys.readouterr().err


def test_the_parser_needs_beat_and_dir(capsys):
    with pytest.raises(SystemExit) as raised:
        main(["takes", "--dir", "somewhere"])
    assert raised.value.code == 2
    assert "--beat" in capsys.readouterr().err


def test_takes_that_are_all_skipped_get_a_report_and_exit_2(tmp_path, beat, capsys):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", 1e-4 * vocal(1))
    work = tmp_path / "work"
    code = main(["takes", "--beat", str(beat), "--dir", str(folder), "--work", str(work)])
    assert code == 2
    assert "no take could be measured" in capsys.readouterr().err
    data = json.loads((work / "takes" / "takes" / "ranking.json").read_text(encoding="utf-8"))
    assert data["takes"] == [] and data["voice"] is None
    assert [s["name"] for s in data["skipped"]] == ["take-01"]


def test_the_report_words_skips_and_short_takes_in_russian(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3, seconds=3.0))
    put(folder, "take-04.wav", vocal(4, rate=44100), rate=44100)
    put(folder, "take-05.wav", 1e-4 * vocal(5))
    ranking = rank_folder(beat, folder)
    out = tmp_path / "out"
    text = write_outputs(out, ranking, "song", None).read_text(encoding="utf-8")
    assert "- take-04: пропущен — частота 44100 Гц, у остальных дублей 48000 Гц" in text
    assert "- take-05: пропущен — дубль пустой или почти без звука" in text
    assert "- take-03: дубль короткий, 3.0 с, бит 12.0 с." in text
    assert "Hz" not in text
    data = json.loads((out / "ranking.json").read_text(encoding="utf-8"))
    codes = {s["name"]: (s["code"], s["reason"]) for s in data["skipped"]}
    assert codes["take-04"] == ("other_rate", "44100 Hz, the other takes are at 48000 Hz")
    assert codes["take-05"][0] == "silent"
