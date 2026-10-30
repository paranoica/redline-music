import json

import pytest
from redline.cli import main
from redline.core import native

pytest.importorskip("redline_dsp")


def test_effects_prints_every_effect_with_its_params_and_ranges(capsys):
    assert main(["effects"]) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    listed = native.rack_effects()
    assert listed is not None
    for effect in listed:
        assert any(line.split()[:1] == [effect["effect"]] for line in lines), effect["effect"]
    gain = next(line for line in lines if line.split()[:2] == ["gain", "db"])
    assert gain.split()[2:6] == ["-60", "24", "0", "dB"]
    # a choice param lists its choices, not a range of indexes
    preserve = next(line for line in lines if "preserve" in line)
    assert "off, on" in preserve
    # an effect that needs the future says so on its first line
    reverse = next(line for line in lines if line.split()[:1] == ["reverse"])
    assert "offline" in reverse


def test_effects_json_prints_the_raw_list(capsys):
    assert main(["effects", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == native.rack_effects()


def test_effects_without_rust_says_so_and_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(native, "redline_dsp", None)
    assert main(["effects"]) == 2
    assert "redline_dsp" in capsys.readouterr().err
