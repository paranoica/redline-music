from collections.abc import Sequence

import numpy as np
import pytest
from redline.flip import grid as grid_module
from redline.flip.grid import Grid, GridError, find_grid
from redline.takes.rank import Grid as TrackerGrid

RATE = 22050


def kick(rate: int = RATE) -> np.ndarray:
    t = np.arange(int(0.2 * rate)) / rate
    phase = 2 * np.pi * (50 * t + 90 * 0.05 * (1 - np.exp(-t / 0.05)))
    return (np.sin(phase) * np.exp(-t / 0.07)).astype(np.float32)


def click(rate: int = RATE) -> np.ndarray:
    return (np.random.default_rng(3).standard_normal(int(0.004 * rate)) * 0.5).astype(np.float32)


def track(
    bpm: float,
    bars: int,
    hits: dict[int, float],
    lead_s: float = 0.0,
    extras: Sequence[tuple[float, float]] = (),
    rate: int = RATE,
) -> np.ndarray:
    """Kicks at sixteenth positions of every bar, each with its gain, after `lead_s` of silence."""
    step = 60.0 / bpm / 4
    out = np.zeros(int((lead_s + bars * 16 * step) * rate) + rate // 2, dtype=np.float32)
    body = kick(rate)
    for bar in range(bars):
        for pos, gain in hits.items():
            at = int((lead_s + (bar * 16 + pos) * step) * rate)
            out[at : at + body.size] += gain * body[: out.size - at]
    for t, gain in extras:
        at = int(t * rate)
        out[at : at + body.size] += gain * body
    return out[: int((lead_s + bars * 16 * step) * rate)]


def test_the_last_bar_of_a_long_beat_lands_on_its_start():
    bpm, bars, lead = 142.86, 104, 0.25
    drums = track(bpm, bars, {0: 1.0, 6: 0.7, 10: 0.7, 15: 0.4}, lead)
    grid = find_grid(drums, np.zeros_like(drums), RATE)
    bar_s = 60.0 / bpm * 4
    assert len(grid.bars) == bars
    assert grid.bars[-1] == pytest.approx(lead + (bars - 1) * bar_s, abs=0.005)
    assert grid.bars[0] == pytest.approx(lead, abs=0.005)
    assert grid.bpm == pytest.approx(bpm, abs=0.02)


def test_the_last_bar_lands_at_another_sample_rate():
    bpm, bars = 142.86, 104
    drums = track(bpm, bars, {0: 1.0, 6: 0.7, 10: 0.7, 15: 0.4}, rate=44100)
    grid = find_grid(drums, np.zeros_like(drums), 44100)
    assert grid.bars[-1] == pytest.approx((bars - 1) * 60.0 / bpm * 4, abs=0.005)


def test_a_half_time_pattern_puts_the_downbeat_on_its_first_kick():
    drums = track(140.0, 60, {0: 1.0, 10: 1.0})
    grid = find_grid(drums, np.zeros_like(drums), RATE, bpm=140.0)
    bar_s = 60.0 / 140.0 * 4
    assert grid.bpm == pytest.approx(140.0, abs=0.05)
    assert grid.downbeat_s == pytest.approx(0.0, abs=0.005)
    assert grid.bars[-1] == pytest.approx(59 * bar_s, abs=0.005)


def test_a_pickup_beat_before_the_first_downbeat_is_not_a_bar():
    bpm = 140.0
    beat = 60.0 / bpm
    drums = track(bpm, 60, {0: 1.0, 8: 0.5}, lead_s=beat, extras=[(0.0, 0.3)])
    grid = find_grid(drums, np.zeros_like(drums), RATE)
    assert grid.downbeat_s == pytest.approx(beat, abs=0.005)
    assert grid.bars[0] == pytest.approx(beat, abs=0.005)


def test_a_given_tempo_overrides_the_tracker(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the tracker must not run")

    monkeypatch.setattr(grid_module, "beat_grid", refuse)
    drums = track(142.86, 40, {0: 1.0, 6: 0.7, 10: 0.7, 15: 0.4})
    grid = find_grid(drums, np.zeros_like(drums), RATE, bpm=142.0)
    assert grid.bpm == pytest.approx(142.86, abs=0.05)
    wrong = find_grid(drums, np.zeros_like(drums), RATE, bpm=100.0)
    assert abs(wrong.bpm - 100.0) <= 0.6


@pytest.mark.parametrize(("tracked", "kwargs"), [(70.0, {"double": True}), (280.0, {"half": True})])
def test_half_and_double_change_the_tracked_tempo(monkeypatch, tracked, kwargs):
    monkeypatch.setattr(grid_module, "beat_grid", lambda mono, rate: TrackerGrid(tracked, ()))
    drums = track(140.0, 40, {0: 1.0, 6: 0.7, 10: 0.7, 15: 0.4})
    grid = find_grid(drums, np.zeros_like(drums), RATE, **kwargs)
    assert grid.bpm == pytest.approx(140.0, abs=0.05)


def test_half_and_double_together_are_refused():
    drums = track(140.0, 8, {0: 1.0})
    with pytest.raises(ValueError):
        find_grid(drums, drums, RATE, half=True, double=True)


def test_the_bass_stem_places_the_grid_when_the_drums_have_no_low_band():
    bass = track(140.0, 40, {0: 1.0, 6: 0.7, 10: 0.7, 15: 0.4})
    grid = find_grid(np.zeros_like(bass), bass, RATE, bpm=140.0)
    assert grid.bpm == pytest.approx(140.0, abs=0.05)
    assert grid.downbeat_s == pytest.approx(0.0, abs=0.005)


def test_silence_gives_no_grid():
    with pytest.raises(GridError):
        find_grid(np.zeros(RATE * 5, np.float32), np.zeros(RATE * 5, np.float32), RATE, bpm=120.0)


def test_nudging_the_downbeat_moves_every_bar_and_keeps_the_tempo():
    grid = Grid(120.0, 0.1, (0.1, 2.1, 4.1), 6.0).with_downbeat(0.3)
    assert grid.bpm == 120.0
    assert grid.bars == pytest.approx((0.3, 2.3, 4.3))
    assert grid.downbeat_s == 0.3
