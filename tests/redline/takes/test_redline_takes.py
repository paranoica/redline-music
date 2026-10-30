import json

import numpy as np
import pytest
from redline.takes.rank import BLEED_FLAG, DROPOUT_MAX_POINTS, rank_folder
from redline.takes.report import report_markdown
from takes_signals import RATE, SECONDS, click_beat, put, vocal


@pytest.fixture
def beat(tmp_path):
    return put(tmp_path, "beat.wav", click_beat())


def by_name(ranking):
    return {take.name: take for take in ranking.takes}


def test_a_late_take_ranks_below_on_grid_ones(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2, shift_s=0.07))
    put(folder, "take-03.wav", vocal(3))
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert ranking.ranked
    assert [t.name for t in ranking.takes][-1] == "take-02"
    assert takes["take-02"].metrics.timing_ms == pytest.approx(70, abs=12)
    assert takes["take-02"].points["timing"] > 0
    assert takes["take-01"].points["timing"] == 0
    assert takes["take-03"].points["timing"] == 0


def test_a_clipped_take_ranks_below_a_clean_one(tmp_path, beat):
    folder = tmp_path / "takes"
    # the faulty take comes first by name, so the tie-break by name cannot rank it last
    put(folder, "take-01.wav", np.clip(vocal(1) * 3.0, -1.0, 1.0))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3))
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert ranking.takes[-1].name == "take-01"
    assert takes["take-01"].metrics.clip_runs > 0
    assert takes["take-01"].metrics.clip_share > 0
    assert takes["take-01"].points["clipping"] > 0
    assert takes["take-02"].metrics.clip_runs == takes["take-03"].metrics.clip_runs == 0


def test_a_single_sample_at_full_scale_is_no_clipped_run(tmp_path, beat):
    folder = tmp_path / "takes"
    data = vocal(1)
    data[int(2.0 * RATE)] = 1.0
    put(folder, "take-01.wav", data)
    put(folder, "take-02.wav", vocal(2))
    takes = by_name(rank_folder(beat, folder))
    assert takes["take-01"].metrics.clip_runs == 0
    assert takes["take-01"].points["clipping"] == 0


def test_a_noisy_take_ranks_below_a_clean_one(tmp_path, beat):
    folder = tmp_path / "takes"
    noisy = vocal(1) + 0.02 * np.random.default_rng(9).standard_normal((int(SECONDS * RATE), 1))
    put(folder, "take-01.wav", noisy.astype(np.float32))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3))
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert ranking.takes[-1].name == "take-01"
    # white noise at 0.02 rms peaks about 10 dB higher in 1 ms blocks: near -24 dBFS
    assert takes["take-01"].metrics.floor_db == pytest.approx(-24, abs=6)
    assert takes["take-01"].points["noise"] > 0
    assert takes["take-02"].metrics.floor_db < -60
    assert takes["take-02"].points["noise"] == 0


def test_a_bled_take_ranks_below_a_clean_one_and_asks_for_headphones(tmp_path, beat):
    folder = tmp_path / "takes"
    delay = int(0.002 * RATE)
    leak = np.zeros_like(click_beat())
    leak[delay:] = click_beat()[:-delay]
    put(folder, "take-01.wav", vocal(1) + 0.3 * leak)
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3))
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert ranking.takes[-1].name == "take-01"
    assert takes["take-01"].metrics.bleed >= BLEED_FLAG
    assert takes["take-01"].points["bleed"] > 0
    assert takes["take-01"].headphones
    assert takes["take-02"].metrics.bleed < BLEED_FLAG / 2
    assert not takes["take-02"].headphones and not takes["take-03"].headphones


def test_a_consistently_late_set_is_not_punished_against_itself(tmp_path, beat):
    folder = tmp_path / "takes"
    for i in (1, 2, 3):
        put(folder, f"take-0{i}.wav", vocal(i, shift_s=0.06))
    ranking = rank_folder(beat, folder)
    for take in ranking.takes:
        assert take.metrics.timing_ms < 10
        assert take.points["timing"] == 0
        # the grid sees the laid-back flow, the score does not
        assert take.metrics.grid_ms == pytest.approx(60, abs=20)


def test_sidecar_dropouts_lower_the_rank(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    dropped = vocal(2)
    spans = [[int(3.0 * RATE), int(3.02 * RATE)], [int(7.0 * RATE), int(7.03 * RATE)]]
    for start, end in spans:
        dropped[start:end] = 0.0
    put(folder, "take-02.wav", dropped)
    (folder / "take-02.json").write_text(json.dumps({"dropouts": spans}), encoding="utf-8")
    put(folder, "take-03.wav", vocal(3))
    for name in ("take-01.json", "take-03.json"):
        (folder / name).write_text(json.dumps({"dropouts": []}), encoding="utf-8")
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert ranking.takes[-1].name == "take-02"
    assert takes["take-02"].metrics.dropouts == 2
    assert takes["take-02"].metrics.dropout_ms == pytest.approx(50, abs=0.1)
    assert takes["take-01"].metrics.dropouts == 0
    assert takes["take-01"].points["dropouts"] == 0


def test_an_unreadable_sidecar_is_a_note_not_a_crash(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    (folder / "take-02.json").write_text('{"dropouts": [[5, 1]]}', encoding="utf-8")
    takes = by_name(rank_folder(beat, folder))
    assert takes["take-02"].metrics.dropouts is None
    assert any("take-02.json" in note for note in takes["take-02"].metrics.notes)


def test_a_take_at_another_rate_or_far_past_the_beat_is_skipped(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3, rate=44100), rate=44100)
    put(folder, "take-04.wav", vocal(4, seconds=SECONDS + 10))
    ranking = rank_folder(beat, folder)
    assert sorted(t.name for t in ranking.takes) == ["take-01", "take-02"]
    skipped = {s.name: s for s in ranking.skips}
    assert set(skipped) == {"take-03", "take-04"}
    assert "44100" in skipped["take-03"].reason
    assert skipped["take-03"].code == "other_rate"
    assert skipped["take-04"].code == "too_long"


def test_a_short_take_is_ranked_over_the_range_it_shares(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3, shift_s=0.05, seconds=SECONDS / 4))
    ranking = rank_folder(beat, folder)
    assert ranking.skips == []
    takes = by_name(ranking)
    short = takes["take-03"]
    assert short.metrics.short and not takes["take-01"].metrics.short
    assert short.metrics.seconds == pytest.approx(SECONDS / 4)
    # measured against the others over its own range: 50 ms late shows
    assert short.metrics.timing_ms is not None and short.metrics.timing_ms > 30
    assert short.rank == 3
    assert ranking.beat_seconds == pytest.approx(SECONDS)


def test_a_take_just_over_half_the_beat_is_not_short(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2, seconds=SECONDS * 0.55))
    put(folder, "take-03.wav", vocal(3, seconds=SECONDS * 0.45))
    takes = by_name(rank_folder(beat, folder))
    assert not takes["take-02"].metrics.short
    assert takes["take-03"].metrics.short


@pytest.mark.parametrize("seconds", [SECONDS, SECONDS / 4])
def test_a_silent_take_is_skipped_however_long(tmp_path, beat, seconds):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", 1e-4 * vocal(3, seconds=seconds))
    ranking = rank_folder(beat, folder)
    assert sorted(t.name for t in ranking.takes) == ["take-01", "take-02"]
    assert [(s.name, s.code) for s in ranking.skips] == [("take-03", "silent")]


def clean_sidecar(folder, name, **fields):
    data = {"dropouts": [], "complete": True, **fields}
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("sidecar", [None, {"complete": False, "error": "stop timed out"}])
def test_a_take_with_unknown_dropouts_scores_the_worst_dropouts(tmp_path, beat, sidecar):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(1))
    clean_sidecar(folder, "take-01.json")
    if sidecar is not None:
        (folder / "take-02.json").write_text(json.dumps(sidecar), encoding="utf-8")
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    unknown = takes["take-02"]
    assert unknown.metrics.dropouts is None
    assert unknown.points["dropouts"] == DROPOUT_MAX_POINTS
    assert takes["take-01"].points["dropouts"] == 0
    assert [t.name for t in ranking.takes] == ["take-01", "take-02"]
    text = report_markdown(ranking, "song", None)
    assert "- take-02: выпадения неизвестны" in text
    assert "- take-01: выпадения неизвестны" not in text


def test_an_old_sidecar_without_the_complete_mark_still_reads(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    (folder / "take-01.json").write_text(json.dumps({"dropouts": [[0, 480]]}), encoding="utf-8")
    take = rank_folder(beat, folder).takes[0]
    assert take.metrics.dropouts == 1
    assert take.metrics.dropout_ms == pytest.approx(10.0)


def test_a_clean_short_take_ranks_below_a_slightly_worse_full_one(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    put(folder, "take-03.wav", vocal(3, seconds=SECONDS / 4))
    clean_sidecar(folder, "take-01.json", dropouts=[[RATE, RATE + 480]])
    clean_sidecar(folder, "take-02.json")
    clean_sidecar(folder, "take-03.json")
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert takes["take-03"].total < takes["take-01"].total
    assert [t.name for t in ranking.takes] == ["take-02", "take-01", "take-03"]
    text = report_markdown(ranking, "song", None)
    assert "ниже всех полных дублей" in text


def test_a_take_logged_with_a_quantum_change_scores_the_worst_dropouts(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(1))
    clean_sidecar(folder, "take-01.json")
    clean_sidecar(folder, "take-02.json", quantum_changed=True, quantum=64, quantum_now=256)
    ranking = rank_folder(beat, folder)
    takes = by_name(ranking)
    assert takes["take-02"].metrics.dropouts is None
    assert takes["take-02"].metrics.quantum_changed and not takes["take-01"].metrics.quantum_changed
    assert takes["take-02"].points["dropouts"] == DROPOUT_MAX_POINTS
    text = report_markdown(ranking, "song", None)
    assert "- take-02: во время дубля сменился квант" in text
    assert "- take-02: выпадения неизвестны" not in text


HASH = "ab" * 32


def sidecar_for(folder, name, **fields):
    data = {"dropouts": [], "complete": True, "beat_sha256": HASH, **fields}
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_the_beat_filter_ranks_only_complete_takes_over_that_beat(tmp_path, beat):
    folder = tmp_path / "takes"
    for n in range(1, 6):
        put(folder, f"take-0{n}.wav", vocal(n))
    sidecar_for(folder, "take-01.json")
    sidecar_for(folder, "take-03.json", complete=False)
    sidecar_for(folder, "take-04.json", beat_sha256="cd" * 32)
    (folder / "take-05.json").write_text(json.dumps({"dropouts": []}), encoding="utf-8")
    ranking = rank_folder(beat, folder, beat_sha256=HASH)
    assert [t.name for t in ranking.takes] == ["take-01"]
    assert sorted((s.name, s.code) for s in ranking.skips) == [
        ("take-02", "incomplete"),
        ("take-03", "incomplete"),
        ("take-04", "other_beat"),
        ("take-05", "incomplete"),
    ]
    text = report_markdown(ranking, "song", None)
    assert "- take-02: пропущен — запись дубля не завершена" in text
    assert "- take-04: пропущен — дубль записан под другой файл бита" in text


def test_the_beat_filter_with_every_take_left_out_ranks_nothing(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    ranking = rank_folder(beat, folder, beat_sha256=HASH)
    assert ranking.takes == []
    assert [(s.name, s.code) for s in ranking.skips] == [("take-01", "incomplete")]


def test_without_the_filter_takes_without_the_new_fields_are_ranked(tmp_path, beat):
    folder = tmp_path / "takes"
    put(folder, "take-01.wav", vocal(1))
    put(folder, "take-02.wav", vocal(2))
    sidecar_for(folder, "take-01.json", complete=False, beat_sha256="cd" * 32)
    ranking = rank_folder(beat, folder)
    assert sorted(t.name for t in ranking.takes) == ["take-01", "take-02"]
    assert ranking.skips == []
