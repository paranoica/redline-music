import copy
import json
import stat
import threading
from typing import Any

import pytest
from redline.flip import model

pytest.importorskip("redline_dsp")


def doc():
    return {
        "format": 1,
        "source": "input/beats/some beat.mp3",
        "grid": {"bpm": 120.0, "downbeat_s": 0.25, "swing": 54},
        "bass808": {"mode": "ours", "params": {"tune": 12.5}},
        "hats": {"mode": "ours", "remove": ["hh", "ride"], "patterns": ["eighths", "sixteenths"]},
        "gains": {"drums": 0, "other": -3.5, "bass": 1},
        "arrangement": [
            {"label": "intro", "from_bar": 0, "to_bar": 4},
            {"label": "hook", "from_bar": 8, "to_bar": 16},
        ],
        "effects": [
            {"effect": "reverse", "from_bar": 3, "to_bar": 4, "params": {}},
            {"effect": "tape_stop", "from_bar": 11, "to_bar": 12, "params": {"duration": 1.5}},
            {
                "effect": "stutter",
                "from_bar": 5,
                "to_bar": 6,
                "params": {"length": 250, "repeats": 4},
            },
            {"effect": "granular", "from_bar": 6, "to_bar": 8, "params": {"size": 90, "seed": 7}},
            {"effect": "tape_start", "from_bar": 0, "to_bar": 1, "params": {"time": 0.5}},
        ],
    }


def test_round_trip():
    flip = model.parse(doc())
    assert model.parse(json.loads(json.dumps(flip.to_json()))) == flip
    assert flip.to_json() == doc()
    assert flip.play_bars == 12


def with_(path, value):
    data = doc()
    target: Any = data
    keys = path.split(".")
    for key in keys[:-1]:
        target = target[int(key)] if key.isdigit() else target[key]
    last = keys[-1]
    if last.isdigit():
        target[int(last)] = value
    else:
        target[last] = value
    return data


@pytest.mark.parametrize(
    "path, value, message",
    [
        ("extra", 1, "flip.extra: unknown field"),
        ("grid.tempo", 1, "grid.tempo: unknown field"),
        ("source", "../x.mp3", "source"),
        ("source", "/etc/passwd", "source"),
        ("source", "input/../../x", "source"),
        ("source", "docs/x.mp3", "source"),
        ("format", 2, "format"),
        ("grid.bpm", 10, "grid.bpm"),
        ("grid.swing", 80, "grid.swing"),
        ("grid.bpm", True, "grid.bpm"),
        ("bass808.mode", "loud", "bass808.mode"),
        ("hats.mode", "gone", "hats.mode"),
        ("hats.remove", ["ride"], "hats.remove"),
        ("hats.remove", ["hh", "hh"], "hats.remove"),
        ("hats.remove", ["hh", "kick"], "hats.remove[1]"),
        ("hats.patterns", ["eighths"], "hats.patterns"),
        ("gains.drums", 99, "gains.drums"),
        ("effects.0.effect", "flanger", "effects[0].effect"),
        ("effects.0.params", {"window": 5}, "effects[0].params.window"),
        ("effects.2.params", {"length": 5000}, "effects[2].params.length"),
        ("effects.2.params", {"repeats": 2.5}, "effects[2].params.repeats"),
        ("effects.2.params", {"trigger": 1}, "effects[2].params.trigger"),
        ("effects.3.params", {"size": 1}, "effects[3].params.size"),
        ("effects.1.params", {"duration": 4.5}, "effects[1].params.duration"),
        ("effects.1.to_bar", 99, "effects[1].to_bar"),
        ("effects.1.from_bar", 12, "effects[1].to_bar"),
        ("arrangement.1.to_bar", 8, "arrangement[1].to_bar"),
        ("arrangement.0.label", "", "arrangement[0].label"),
        ("arrangement", [], "arrangement"),
    ],
)
def test_refusals(path, value, message):
    with pytest.raises(model.FlipError, match=message.replace("[", r"\[").replace("]", r"\]")):
        model.parse(with_(path, value))


def test_missing_field_is_named():
    data = doc()
    del data["grid"]["swing"]
    with pytest.raises(model.FlipError, match=r"grid\.swing: missing"):
        model.parse(data)


def test_tape_stop_longer_than_its_range_is_refused():
    data = doc()
    data["effects"] = [
        {"effect": "tape_stop", "from_bar": 0, "to_bar": 1, "params": {"duration": 2.5}}
    ]
    with pytest.raises(model.FlipError, match="duration"):
        model.parse(data)
    data["effects"][0]["params"]["duration"] = 2.0
    model.parse(data)


def test_tape_duration_is_capped_at_four_seconds_on_a_long_range():
    data = doc()
    data["effects"] = [
        {"effect": "tape_start", "from_bar": 0, "to_bar": 8, "params": {"duration": 4.0}}
    ]
    model.parse(data)
    data["effects"][0]["params"]["duration"] = 4.01
    with pytest.raises(model.FlipError, match="duration"):
        model.parse(data)


def test_bars_limit_counts_play_order():
    data = doc()
    data["effects"] = []
    data["arrangement"] = [
        {"label": "a", "from_bar": 0, "to_bar": 256},
        {"label": "b", "from_bar": 0, "to_bar": 256},
    ]
    data["hats"]["patterns"] = ["eighths", "eighths"]
    model.parse(data)
    data["arrangement"][1]["to_bar"] = 257
    with pytest.raises(model.FlipError, match="512"):
        model.parse(data)


def test_save_new_never_overwrites(tmp_path):
    folder = tmp_path / "flips" / "ab12cd34"
    flip = model.parse(doc())
    assert model.save_new(folder, flip) == 1
    assert model.save_new(folder, flip) == 2
    first = folder / "flip-1.json"
    first.write_text("keep")
    (folder / "flip-7.json").write_text("{}")
    assert model.save_new(folder, flip) == 8
    assert first.read_text() == "keep"
    assert stat.S_IMODE((folder / "flip-2.json").stat().st_mode) == 0o600
    assert model.load(folder / "flip-2.json") == flip
    assert [p.name for p in folder.iterdir() if p.name.startswith(".")] == []


def test_two_concurrent_creators_get_different_numbers(tmp_path):
    flip = model.parse(doc())
    got: list[int] = []
    barrier = threading.Barrier(8)

    def run():
        barrier.wait()
        got.append(model.save_new(tmp_path, flip))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(got) == list(range(1, 9))
    for n in got:
        assert model.load(tmp_path / f"flip-{n}.json") == flip


def test_save_draft_replaces_atomically_and_privately(tmp_path):
    flip = model.parse(doc())
    model.save_draft(tmp_path, flip)
    draft = tmp_path / "draft.json"
    draft.chmod(0o644)
    other = copy.deepcopy(doc())
    other["gains"]["bass"] = 3
    model.save_draft(tmp_path, model.parse(other))
    assert model.load(draft).gains["bass"] == 3
    assert stat.S_IMODE(draft.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["draft.json"]


def test_load_refuses_a_broken_file(tmp_path):
    (tmp_path / "flip-1.json").write_text("{nope")
    with pytest.raises(model.FlipError):
        model.load(tmp_path / "flip-1.json")
