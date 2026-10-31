import itertools
import json

import numpy as np
import pytest
import redline_dsp
from redline.flip.score import (
    HATS,
    Bass808Params,
    FlipNote,
    PlayBar,
    chunk_sizes,
    flip_score,
    note_name,
    render_drum_rows,
    render_flip_parts,
)

RATE = 48000


def events(score: dict, sound: str) -> list[dict]:
    table = json.loads(redline_dsp.beat_events(json.dumps(score), float(RATE)))
    names = [s["name"] for s in table["sounds"]]
    return [e for e in table["events"] if names[e["sound"]] == sound]


def plain(n: int, notes: tuple[FlipNote, ...] = (), hat: str | None = None) -> list[PlayBar]:
    return [PlayBar(i, notes, hat) for i in range(n)]


def test_chunk_sizes_edges():
    assert chunk_sizes(1) == [1]
    assert chunk_sizes(8) == [8]
    assert chunk_sizes(13) == [8, 4, 1]
    assert chunk_sizes(15) == [8, 4, 2, 1]
    assert chunk_sizes(512) == [8] * 64


def test_note_names_follow_the_score_format():
    assert note_name(24) == "C1"
    assert note_name(30) == "F#1"
    table = events(flip_score(plain(1, (FlipNote(0, 4, 30),)), 140), "bass")
    assert table[0]["note"] == 30


def test_thirteen_bars_make_patterns_of_8_4_1():
    bars = plain(13, (FlipNote(0, 4, 36),), "16ths")
    score = flip_score(bars, 140)
    assert [s["bars"] for s in score["sections"]] == [8, 4, 1]
    assert sorted(p["bars"] for p in score["patterns"].values()) == [1, 1, 4, 4, 8, 8]
    assert redline_dsp.beat_check(json.dumps(score)) == []


def test_two_alternating_patterns_over_200_bars_are_reused():
    a, b = (FlipNote(0, 4, 36),), (FlipNote(2, 4, 41),)
    bars = []
    for block in range(25):
        bars += [PlayBar(len(bars) + i, a if block % 2 == 0 else b, "8ths") for i in range(8)]
    score = flip_score(bars, 140)
    assert len(score["sections"]) == 25
    assert len(score["patterns"]) == 3
    assert redline_dsp.beat_check(json.dumps(score)) == []


def test_different_content_is_not_merged():
    bars = [PlayBar(i, (FlipNote(0, 4, 36 + i % 3),)) for i in range(8)]
    bars += [PlayBar(i, (FlipNote(0, 4, 36 + i % 3),)) for i in range(8)]
    other = [PlayBar(i, (FlipNote(0, 4, 40),)) for i in range(8)]
    score = flip_score(bars + other, 140)
    assert len(score["patterns"]) == 2


def test_512_bars_stay_valid():
    score = flip_score(plain(512, (FlipNote(0, 4, 36),), "8ths"), 140)
    assert redline_dsp.beat_check(json.dumps(score)) == []
    with pytest.raises(ValueError, match="513 bars"):
        flip_score(plain(513), 140)


def test_glide_pair_carries_legato_from():
    notes = (FlipNote(0, 6, 36), FlipNote(4, 4, 43))
    table = events(flip_score(plain(1, notes), 140), "bass")
    assert table[0]["legato_from"] is None
    assert table[1]["legato_from"] == 36


def test_separate_notes_do_not_glide():
    notes = (FlipNote(0, 4, 36), FlipNote(4, 4, 43))
    table = events(flip_score(plain(1, notes), 140), "bass")
    assert [e["legato_from"] for e in table] == [None, None]


def test_glide_across_a_bar_line_inside_a_pattern_is_kept():
    bars = [PlayBar(0, (FlipNote(12, 8, 36),)), PlayBar(1, (FlipNote(2, 4, 43),))]
    found: list[str] = []
    table = events(flip_score(bars, 140, compromises=found), "bass")
    assert table[1]["legato_from"] == 36
    assert found == []


def test_glide_across_a_pattern_end_is_shortened_and_reported():
    bars = plain(9)
    bars[7] = PlayBar(7, (FlipNote(12, 8, 36),))
    bars[8] = PlayBar(8, (FlipNote(2, 4, 43),))
    found: list[str] = []
    score = flip_score(bars, 140, compromises=found)
    table = events(score, "bass")
    assert table[0]["off"] - table[0]["start"] == pytest.approx(4 * RATE * 60 / 140 / 4, abs=2)
    assert table[1]["legato_from"] is None
    assert len(found) == 1 and "glide" in found[0] and "bar 7" in found[0]


def test_plain_note_over_a_pattern_end_is_cut_and_reported():
    bars = plain(9)
    bars[7] = PlayBar(7, (FlipNote(12, 8, 36),))
    found: list[str] = []
    flip_score(bars, 140, compromises=found)
    assert len(found) == 1 and "glide" not in found[0]


def test_note_over_a_splice_ends_with_its_bar():
    bars = [PlayBar(0, (FlipNote(12, 8, 36),)), PlayBar(5, (FlipNote(2, 4, 43),))]
    found: list[str] = []
    table = events(flip_score(bars, 140, compromises=found), "bass")
    assert table[1]["legato_from"] is None
    assert table[0]["off"] - table[0]["start"] == pytest.approx(4 * RATE * 60 / 140 / 4, abs=2)
    assert len(found) == 1 and "source bars part" in found[0]


def test_contiguous_source_bars_keep_a_long_note():
    bars = [PlayBar(5, (FlipNote(12, 8, 36),)), PlayBar(6)]
    found: list[str] = []
    table = events(flip_score(bars, 140, compromises=found), "bass")
    assert table[0]["off"] - table[0]["start"] == pytest.approx(8 * RATE * 60 / 140 / 4, abs=2)
    assert found == []


@pytest.mark.parametrize(
    ("preset", "per_bar"), [("8ths", 8), ("16ths", 16), ("trip_roll", 20), ("roll32", 20)]
)
def test_hat_preset_event_counts(preset, per_bar):
    assert len(events(flip_score(plain(3, hat=preset), 140), "hat")) == 3 * per_bar
    assert preset in HATS


def test_hat_less_bars_are_silent_between_hat_bars():
    bars = [PlayBar(0, hat="16ths"), PlayBar(1), PlayBar(2, hat="8ths")]
    table = events(flip_score(bars, 140), "hat")
    assert sorted({e["bar"] for e in table}) == [0, 2]
    assert len(table) == 24


def test_swing_moves_only_the_off_sixteenths():
    bars = plain(1, hat="16ths")
    straight = events(flip_score(bars, 120, swing=50), "hat")
    swung = events(flip_score(bars, 120, swing=66), "hat")
    for a, b in zip(straight, swung, strict=True):
        if a["step"] % 2:
            assert b["start"] > a["start"] + 1000
        else:
            assert b["start"] == a["start"]


def test_bad_settings_are_refused_with_a_place():
    with pytest.raises(ValueError, match=r"bars\[0\].hat"):
        flip_score([PlayBar(0, hat="nope")], 140)
    with pytest.raises(ValueError, match=r"bars\[0\].notes\[0\].step"):
        flip_score([PlayBar(0, (FlipNote(16, 1, 36),))], 140)
    with pytest.raises(ValueError, match="swing"):
        flip_score(plain(1), 140, swing=80)
    with pytest.raises(ValueError, match="bpm"):
        flip_score(plain(1), 300)
    with pytest.raises(ValueError):
        flip_score([], 140)


def onset(x: np.ndarray, floor: float = 0.02, gap: int = 960) -> list[int]:
    """First loud frames, each after at least `gap` quiet frames (an oscillation is one onset)."""
    loud = np.flatnonzero(np.abs(x).max(axis=1) > floor * np.abs(x).max())
    return [int(loud[0])] + [int(b) for a, b in itertools.pairwise(loud) if b - a > gap]


def test_render_lengths_are_the_play_order_exactly():
    bars = plain(3, (FlipNote(0, 16, 36),), "16ths")
    score = flip_score(bars, 140)
    parts = render_flip_parts(score)
    assert set(parts) == {"bass", "hat"}
    expected = round(3 * 4 * 60 / 140 * RATE)
    for stem in parts.values():
        assert stem.shape == (expected, 2)
        assert stem.dtype == np.float32
    assert np.abs(parts["bass"]).max() > 0.05
    assert np.abs(parts["hat"]).max() > 0.01


def test_silent_sound_renders_silence():
    parts = render_flip_parts(flip_score(plain(2, (FlipNote(0, 4, 36),)), 140))
    assert np.abs(parts["hat"]).max() == 0.0


def test_808_onsets_land_on_their_steps():
    bpm = 150.0
    step = RATE * 60 / bpm / 4
    bars = [
        PlayBar(3, (FlipNote(0, 3, 36), FlipNote(8, 3, 41))),
        PlayBar(9, (FlipNote(5, 3, 38),)),
        PlayBar(2),
    ]
    stem = render_flip_parts(flip_score(bars, bpm, bass=Bass808Params(decay=0.4)))["bass"]
    found = onset(stem)
    expected = [0, 8, 16 + 5]
    assert len(found) == 3
    for f, s in zip(found, expected, strict=True):
        assert abs(f - s * step) <= RATE / 1000


def test_808_pitch_matches_the_midi():
    bars = plain(2, (FlipNote(0, 16, 40),))
    bars[1] = PlayBar(1, (FlipNote(0, 16, 33),))
    bass = Bass808Params(drive=0.0, decay=4.0, click=0.0)
    stem = render_flip_parts(flip_score(bars, 120, bass=bass))["bass"]
    bar = RATE * 2
    for i, midi in enumerate((40, 33)):
        steady = stem[i * bar + int(0.5 * RATE) : i * bar + int(1.5 * RATE), 0]
        spectrum = np.abs(np.fft.rfft(steady * np.hanning(len(steady)), n=1 << 18))
        freq = np.argmax(spectrum) * RATE / (1 << 18)
        want = 440 * 2 ** ((midi - 69) / 12)
        assert abs(1200 * np.log2(freq / want)) < 25


def kit(n: int, **rows: str) -> list[PlayBar]:
    return [PlayBar(i, drums=rows) for i in range(n)]


def test_each_drum_row_has_its_own_sound_apart_from_the_ours_hats():
    rows = {
        "kick": "x" + "." * 15,
        "snare": "." * 4 + "x" + "." * 11,
        "clap": "." * 8 + "x" + "." * 7,
        "hat": "x" * 16,
        "hat_open": "." * 2 + "x" + "." * 13,
        "perc": "." * 15 + "x",
    }
    score = flip_score([PlayBar(0, hat="8ths", drums=rows)], 140)
    sounds = {n: s["instrument"] for n, s in score["sounds"].items()}
    assert sounds == {
        "bass": "bass808",
        "hat": "hat_closed",
        "drum_kick": "kick",
        "drum_snare": "snare",
        "drum_clap": "clap",
        "drum_hat": "hat_closed",
        "drum_hat_open": "hat_open",
        "drum_perc": "rim",
    }
    assert len(events(score, "hat")) == 8
    assert len(events(score, "drum_hat")) == 16
    assert [e["step"] for e in events(score, "drum_snare")] == [4]
    assert redline_dsp.beat_check(json.dumps(score)) == []


def test_a_score_without_drum_rows_has_only_bass_and_hat_sounds():
    score = flip_score(kit(2, kick="." * 16), 140)
    assert set(score["sounds"]) == {"bass", "hat"}


def test_drum_rows_follow_the_8_4_1_chunking():
    bars = kit(13, kick="x...x...x...x...")
    score = flip_score(bars, 140)
    assert [s["bars"] for s in score["sections"]] == [8, 4, 1]
    assert len(events(score, "drum_kick")) == 13 * 4
    assert len({k for k in score["patterns"] if k.startswith("drum_kick")}) == 3


def test_a_drum_row_that_is_empty_in_a_chunk_adds_no_pattern_there():
    bars = kit(8, kick="." * 16) + kit(4, kick="x" + "." * 15)
    score = flip_score(bars, 140)
    assert [len(s["play"]) for s in score["sections"]] == [0, 1]


def test_a_bad_drum_row_is_refused_with_its_place():
    with pytest.raises(ValueError, match=r"bars\[1\].drums: no row 'tom'"):
        flip_score([PlayBar(0), PlayBar(1, drums={"tom": "." * 16})], 140)
    with pytest.raises(ValueError, match=r"bars\[0\].drums.kick"):
        flip_score([PlayBar(0, drums={"kick": "x" * 15})], 140)
    with pytest.raises(ValueError, match=r"bars\[0\].drums.kick"):
        flip_score([PlayBar(0, drums={"kick": "x" * 15 + "y"})], 140)


def test_a_block_note_may_ring_into_the_next_bar_and_a_source_note_may_not():
    notes = (FlipNote(12, 8, 36),)
    own = [PlayBar(0, notes, own=True), PlayBar(5)]
    found: list[str] = []
    table = events(flip_score(own, 140, compromises=found), "bass")
    assert table[0]["off"] - table[0]["start"] == pytest.approx(8 * RATE * 60 / 140 / 4, abs=2)
    assert found == []
    cut: list[str] = []
    flip_score([PlayBar(0, notes), PlayBar(5)], 140, compromises=cut)
    assert len(cut) == 1


def test_drum_stems_have_the_play_orders_length_and_the_row_does_not_leak():
    score = flip_score(kit(2, kick="x" + "." * 15), 140)
    parts = render_flip_parts(score)
    assert set(parts) == {"bass", "hat", "drum_kick"}
    assert parts["drum_kick"].shape == (round(2 * 4 * 60 / 140 * RATE), 2)
    assert np.abs(parts["drum_kick"]).max() > 0.05


def test_a_row_added_leaves_the_other_rows_samples_unchanged():
    kick = "x......." * 2
    alone = render_drum_rows(kit(2, kick=kick), 140)
    both = render_drum_rows(kit(2, kick=kick, snare="....x..." * 2), 140)
    assert set(alone) == {"kick"} and set(both) == {"kick", "snare"}
    np.testing.assert_array_equal(alone["kick"], both["kick"])
    assert np.abs(both["snare"]).max() > 0.05


def test_in_one_score_a_second_row_shifts_the_noise_of_the_first():
    # why the rows are rendered apart
    kick = "x......." * 2
    alone = render_flip_parts(flip_score(kit(2, kick=kick), 140))["drum_kick"]
    both = render_flip_parts(flip_score(kit(2, kick=kick, snare="....x..." * 2), 140))["drum_kick"]
    assert not np.array_equal(alone, both)
