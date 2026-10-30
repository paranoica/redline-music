from collections.abc import Sequence

import numpy as np
import pytest
from redline.flip.grid import Grid
from redline.flip.notes import Note, StepNote, _fold, find_notes, to_steps

RATE = 22050
BPM = 140.0
STEP_S = 60.0 / BPM / 4


def grid_for(seconds: float) -> Grid:
    bar = STEP_S * 16
    return Grid(BPM, 0.0, tuple(i * bar for i in range(int(seconds / bar))), seconds)


def hz(midi: float, cents: float = 0.0) -> float:
    return 440.0 * 2 ** ((midi + cents / 100 - 69) / 12)


def tone(
    midi: int,
    steps: float,
    cents: float = 0.0,
    drive: float = 0.0,
    attack_s: float = 0.004,
    slide_from: int | None = None,
    slide_s: float = 0.08,
    tau: float = 0.6,
) -> np.ndarray:
    """An 808: a decaying sine at the note, gated after `steps` sixteenths."""
    n = int(steps * STEP_S * RATE)
    t = np.arange(n) / RATE
    f = np.full(n, hz(midi, cents))
    if slide_from is not None:
        mix = np.clip(t / slide_s, 0, 1)
        f = hz(slide_from, cents) * (hz(midi, cents) / hz(slide_from, cents)) ** mix
    phase = 2 * np.pi * np.cumsum(f) / RATE
    wave = np.sin(phase)
    if drive:
        wave = np.tanh(drive * wave) / np.tanh(drive)
    env = np.exp(-t / tau) * np.minimum(1.0, t / attack_s)
    env[-int(0.005 * RATE) :] *= np.linspace(1, 0, int(0.005 * RATE))
    return (0.8 * wave * env).astype(np.float32)


def phrase(notes: Sequence[tuple[int, float, int]], seconds: float = 6.0, **kwargs) -> np.ndarray:
    """Notes as (start step, length in steps, midi)."""
    out = np.zeros(int(seconds * RATE), dtype=np.float32)
    for step, length, midi in notes:
        at = int(step * STEP_S * RATE)
        body = tone(midi, length, **kwargs)
        out[at : at + body.size] += body
    return out


def kick_in_bass(at_step: float, rate: int = RATE) -> np.ndarray:
    rng = np.random.default_rng(5)
    n = int(0.03 * rate)
    t = np.arange(n) / rate
    blip = 0.7 * np.sin(2 * np.pi * 60 * t) * np.exp(-t / 0.01)
    blip[:50] += 0.5 * rng.standard_normal(50)
    out = np.zeros(int(6.0 * rate), dtype=np.float32)
    at = int(at_step * STEP_S * rate)
    out[at : at + n] = blip
    return out


PHRASE = [(0, 4, 31), (4, 2, 34), (6, 2, 29), (8, 4, 26), (12, 4, 33)]


def summary(steps: list[StepNote]) -> list[tuple[int, int, int]]:
    return [(s.step, s.length, s.midi) for s in steps]


def test_a_phrase_comes_back_with_its_pitches_and_sixteenth_starts():
    notes, detune = find_notes(phrase(PHRASE), RATE)
    assert summary(to_steps(notes, grid_for(6.0))) == PHRASE
    assert abs(detune) < 8
    assert all(n.confidence >= 0.5 for n in notes)


def test_a_sixteenth_roll_is_one_note_per_step():
    roll = [(i, 1, 31) for i in range(8)] + [(8, 4, 26)]
    notes, _ = find_notes(phrase(roll), RATE)
    assert summary(to_steps(notes, grid_for(6.0))) == roll


def test_a_driven_note_keeps_its_octave():
    score = [(0, 4, 31), (4, 4, 28), (8, 4, 26), (12, 4, 31)]
    notes, _ = find_notes(phrase(score, drive=6.0), RATE)
    assert [n.midi for n in notes] == [31, 28, 26, 31]


def test_a_source_30_cents_sharp_is_detuned_and_its_notes_are_right():
    notes, detune = find_notes(phrase(PHRASE, cents=30.0), RATE)
    assert detune == pytest.approx(30.0, abs=8.0)
    assert summary(to_steps(notes, grid_for(6.0))) == PHRASE


def test_a_lone_octave_jump_between_neighbours_is_folded_and_a_real_one_is_kept():
    assert _fold([31.0, 43.0, 29.0]) == [31.0, 31.0, 29.0]
    assert _fold([31.0, 19.3, 31.0]) == [31.0, 31.3, 31.0]
    assert _fold([31.0, 31.0, 43.0, 43.0, 43.0]) == [31.0, 31.0, 43.0, 43.0, 43.0]
    assert _fold([65.0, 31.0]) == [53.0, 31.0]
    assert _fold([31.0, 28.0, 26.0]) == [31.0, 28.0, 26.0]


def test_a_slow_attack_still_starts_on_its_step():
    score = [(0, 4, 31), (4, 4, 28), (8, 4, 26)]
    bass = phrase(score[:1]) + phrase(score[1:], attack_s=0.05)
    notes, _ = find_notes(bass, RATE)
    assert summary(to_steps(notes, grid_for(6.0))) == score


def test_a_slide_into_the_next_note_is_a_glide_and_overlaps_by_one_step():
    bass = phrase([(0, 4, 31)]) + phrase([(4, 4, 26)])
    glided = phrase([(0, 4, 31)])
    at = int(4 * STEP_S * RATE)
    body = tone(26, 4, slide_from=31)
    glided[at : at + body.size] += body
    notes, _ = find_notes(glided, RATE)
    assert [n.glide_into_next for n in notes] == [True, False]
    assert summary(to_steps(notes, grid_for(6.0))) == [(0, 5, 31), (4, 4, 26)]
    plain, _ = find_notes(bass, RATE)
    assert [n.glide_into_next for n in plain] == [False, False]
    assert summary(to_steps(plain, grid_for(6.0))) == [(0, 4, 31), (4, 4, 26)]


def test_a_kick_in_the_bass_stem_adds_no_note():
    bass = phrase(PHRASE)
    with_kick = bass + kick_in_bass(18)
    notes, _ = find_notes(with_kick, RATE)
    assert summary(to_steps(notes, grid_for(6.0))) == PHRASE


def test_a_kick_alone_is_no_note():
    notes, detune = find_notes(kick_in_bass(4), RATE)
    assert notes == []
    assert detune == 0.0


def test_silence_has_no_notes():
    assert find_notes(np.zeros(RATE, np.float32), RATE) == ([], 0.0)


def test_to_steps_reaches_a_glide_one_step_past_the_next_start():
    grid = grid_for(4.0)
    notes = [
        Note(0.0, 4 * STEP_S, 31, 0.9, True),
        Note(4 * STEP_S, 6 * STEP_S, 26, 0.9, False),
        Note(6 * STEP_S, 6.4 * STEP_S, 28, 0.9, False),
    ]
    assert summary(to_steps(notes, grid)) == [(0, 5, 31), (4, 2, 26), (6, 1, 28)]


def test_to_steps_counts_from_the_downbeat_and_keeps_the_first_of_two_on_a_step():
    grid = Grid(BPM, 0.5, (0.5,), 4.0)
    notes = [
        Note(0.5 - STEP_S, 0.5, 33, 0.9),
        Note(0.5 + 2.9 * STEP_S, 0.5 + 5 * STEP_S, 29, 0.9),
        Note(0.5 + 3.1 * STEP_S, 0.5 + 5 * STEP_S, 31, 0.9),
    ]
    assert summary(to_steps(notes, grid)) == [(-1, 1, 33), (3, 2, 29)]


def test_a_long_808_that_pulses_is_one_note_not_a_run_of_fragments():
    held = phrase([(0, 16, 30), (16, 8, 33)], seconds=8.0, drive=4.0, tau=2.0)
    t = np.arange(held.size) / RATE
    # distortion and beating make a held 808's level ripple many times a second
    pulsing = held * (1.0 - 0.5 * (0.5 + 0.5 * np.sin(2 * np.pi * 11 * t)))
    notes, _ = find_notes(pulsing.astype(np.float32), RATE)
    assert [n.midi for n in notes] == [30, 33]


def test_off_grid_ripple_on_a_held_808_is_no_strike_when_the_grid_is_known():
    held = phrase([(0, 16, 30), (16, 8, 33)], seconds=8.0, drive=4.0, tau=2.0)
    # short dips halfway between sixteenths: the level comes back fast, but off the grid
    level = np.ones(held.size, dtype=np.float32)
    dip = int(0.008 * RATE)
    for k in range(int(8.0 / STEP_S)):
        at = int((k + 0.5) * STEP_S * RATE)
        level[at : at + dip] = 0.6
    notes, _ = find_notes(held * level, RATE, grid_for(8.0))
    assert [n.midi for n in notes] == [30, 33]
    roll, _ = find_notes(phrase([(s, 1, 31) for s in range(8)] + [(8, 4, 26)]), RATE, grid_for(6.0))
    assert len(roll) == 9
