import copy
import json
import re
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
        "hats": {"mode": "ours", "remove": ["hh", "ride"], "patterns": ["8ths", "16ths"]},
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
        ("hats.patterns", ["8ths"], "hats.patterns"),
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
    # fast enough that 512 bars stay under the time limit
    data["grid"]["bpm"] = 240
    data["arrangement"] = [
        {"label": "a", "from_bar": 0, "to_bar": 256},
        {"label": "b", "from_bar": 0, "to_bar": 256},
    ]
    data["hats"]["patterns"] = ["8ths", "8ths"]
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
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith(".")) == [
        "draft.json",
        "draft.rev",
    ]


def test_load_refuses_a_broken_file(tmp_path):
    (tmp_path / "flip-1.json").write_text("{nope")
    with pytest.raises(model.FlipError):
        model.load(tmp_path / "flip-1.json")


def test_a_flip_plays_at_most_fifteen_minutes_whatever_its_bars():
    data = doc()
    data["effects"] = []
    data["grid"]["bpm"] = 30
    # 113 bars at 30 BPM are 904 s; 112 are 896 s
    data["arrangement"] = [{"label": "a", "from_bar": 0, "to_bar": 113}]
    data["hats"]["patterns"] = ["8ths"]
    with pytest.raises(model.FlipError, match="minutes"):
        model.parse(data)
    data["arrangement"][0]["to_bar"] = 112
    model.parse(data)


def test_our_parts_name_the_tempo_the_synth_cannot_play():
    data = doc()
    data["effects"] = []
    data["grid"]["bpm"] = 260
    data["bass808"]["mode"] = "ours"
    assert "40..220" in model.synth_tempo_problem(model.parse(data))
    data["bass808"]["mode"] = "original"
    data["hats"]["mode"] = "original"
    assert model.synth_tempo_problem(model.parse(data)) is None


def test_a_downbeat_a_little_before_the_file_is_a_bar_line_cut_off_by_the_file():
    data = doc()
    data["grid"]["downbeat_s"] = -0.015
    assert model.parse(data).downbeat_s == -0.015
    data["grid"]["downbeat_s"] = -0.5
    with pytest.raises(model.FlipError, match="downbeat"):
        model.parse(data)


def with_block(**fields):
    data = doc()
    data["arrangement"][0].update(fields)
    return data


NOTES = [
    {"step": 0, "len": 4, "note": 36, "vel": 100},
    {"step": 63, "len": 17, "note": 127, "vel": 1},
]
DRUMS = {"kick": "x" * 4 + "." * 60, "perc": "." * 64}


def test_block_notes_round_trip():
    data = with_block(notes=NOTES, drums=DRUMS)
    data["gains"]["ours"] = -6.5
    flip = model.parse(data)
    block = flip.arrangement[0]
    assert block.notes == (model.BlockNote(0, 4, 36, 100), model.BlockNote(63, 17, 127, 1))
    assert block.drums == DRUMS and flip.ours_gain == -6.5
    assert flip.to_json() == data
    assert model.parse(json.loads(json.dumps(flip.to_json()))) == flip


def test_silent_notes_are_kept_and_absent_ones_are_not_written():
    flip = model.parse(with_block(notes=[], drums={}))
    assert flip.arrangement[0].notes == () and flip.arrangement[0].drums == {}
    assert flip.to_json()["arrangement"][0]["notes"] == []
    assert flip.to_json()["arrangement"][0]["drums"] == {}
    plain = model.parse(doc())
    assert plain.arrangement[0].notes is None and plain.arrangement[0].drums is None
    assert "notes" not in plain.to_json()["arrangement"][0]
    assert "ours" not in plain.to_json()["gains"]


def note(**change):
    return {"step": 0, "len": 1, "note": 36, "vel": 100} | change


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"notes": [note(step=64)]}, "notes[0].step"),
        ({"notes": [note(step=-1)]}, "notes[0].step"),
        ({"notes": [note(len=0)]}, "notes[0].len"),
        ({"notes": [note(step=63, len=18)]}, "notes[0].len"),
        ({"notes": [note(note=128)]}, "notes[0].note"),
        ({"notes": [note(note=-1)]}, "notes[0].note"),
        ({"notes": [note(vel=0)]}, "notes[0].vel"),
        ({"notes": [note(vel=128)]}, "notes[0].vel"),
        ({"notes": [note(vel=1.5)]}, "notes[0].vel"),
        ({"notes": [note(step=True)]}, "notes[0].step"),
        ({"notes": [note(), note(step=64)]}, "notes[1].step"),
        ({"notes": [{"step": 0, "len": 1, "note": 36}]}, "notes[0].vel"),
        ({"notes": [dict(note(), x=1)]}, "notes[0].x"),
        ({"notes": [note()] * 257}, "notes"),
        ({"notes": "x"}, "notes"),
        ({"drums": {"cowbell": "." * 64}}, "drums.cowbell"),
        ({"drums": {"kick": "." * 63}}, "drums.kick"),
        ({"drums": {"kick": "." * 65}}, "drums.kick"),
        ({"drums": {"kick": "y" * 64}}, "drums.kick"),
        ({"drums": {"kick": ["."] * 64}}, "drums.kick"),
        ({"drums": ["kick"]}, "drums"),
    ],
)
def test_block_fields_refused(change, where):
    with pytest.raises(model.FlipError, match=re.escape(where)):
        model.parse(with_block(**change))


def test_block_note_limit_is_per_bar():
    crowded = [note(step=i % 16) for i in range(64)]
    model.parse(with_block(notes=crowded))
    with pytest.raises(model.FlipError, match="notes"):
        model.parse(with_block(notes=[*crowded, note(step=0)]))
    spread = [note(step=i % 64) for i in range(256)]
    model.parse(with_block(notes=spread))
    with pytest.raises(model.FlipError, match="notes"):
        model.parse(with_block(notes=[*spread, note(step=63)]))
    lopsided = [note(step=16 + i % 16) for i in range(65)]
    with pytest.raises(model.FlipError, match="notes"):
        model.parse(with_block(notes=lopsided))
    with pytest.raises(model.FlipError, match="notes"):
        model.parse(with_block(notes=[note(step=i % 16) for i in range(257)]))


def test_ours_gain_is_bounded():
    for value in (-60, 24):
        data = doc()
        data["gains"]["ours"] = value
        assert model.parse(data).ours_gain == value
    for value in (-61, 25, "x", True):
        data = doc()
        data["gains"]["ours"] = value
        with pytest.raises(model.FlipError, match=r"gains\.ours"):
            model.parse(data)


def test_old_flip_parses_and_writes_the_same():
    text = json.dumps(doc(), indent=2, ensure_ascii=False) + "\n"
    flip = model.parse(json.loads(text))
    assert all(b.notes is None and b.drums is None for b in flip.arrangement)
    assert flip.ours_gain is None
    assert model._text(flip) == text


def test_unknown_hat_preset_is_refused_at_parse():
    data = doc()
    data["hats"]["patterns"] = ["8ths", "eighths"]
    with pytest.raises(model.FlipError, match=r"hats.patterns\[1\].*eighths"):
        model.parse(data)
    data["hats"]["patterns"] = ["trip_roll", "roll32"]
    model.parse(data)


def test_drums_or_notes_need_a_tempo_the_synth_plays():
    data = doc()
    data["effects"] = []
    data["bass808"]["mode"] = "original"
    data["hats"] = {"mode": "original", "remove": [], "patterns": []}
    data["grid"]["bpm"] = 250
    assert model.synth_tempo_problem(model.parse(data)) is None
    data["arrangement"][0]["drums"] = {"kick": "x" * 64}
    assert "40..220" in model.synth_tempo_problem(model.parse(data))
    data["arrangement"][0].pop("drums")
    data["arrangement"][1]["notes"] = [note()]
    assert "40..220" in model.synth_tempo_problem(model.parse(data))
    data["arrangement"][1]["notes"] = []
    assert model.synth_tempo_problem(model.parse(data)) is None
    data["arrangement"][1]["drums"] = {}
    assert model.synth_tempo_problem(model.parse(data)) is None
    data["grid"]["bpm"] = 220
    data["arrangement"][1]["drums"] = {"kick": "x" * 128}
    assert model.synth_tempo_problem(model.parse(data)) is None


def test_the_draft_revision_counts_saves_and_refuses_a_stale_base(tmp_path):
    flip = model.parse(doc())
    assert model.draft_rev(tmp_path) == 0
    assert model.save_draft(tmp_path, flip) == 1
    assert model.save_draft(tmp_path, flip, 1) == 2
    before = (tmp_path / "draft.json").read_bytes()
    other = model.parse(with_block(notes=[]))
    assert model.save_draft(tmp_path, other, 1) is None
    assert (tmp_path / "draft.json").read_bytes() == before and model.draft_rev(tmp_path) == 2
