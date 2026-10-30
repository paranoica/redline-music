import numpy as np
from redline.flip.model import parse
from redline.flip.propose import bar_features, propose

RATE = 22050


def beat_features() -> np.ndarray:
    rows = np.zeros((40, 48))
    for bar in range(40):
        rows[bar, bar] = 1.0
    for k in range(8):
        rows[8 + k] = rows[24 + k] = 0.0
        rows[8 + k, 40 + k] = rows[24 + k, 40 + k] = 1.0
    return rows


def spans(blocks: list[dict]) -> list[tuple[str, int, int]]:
    return [(b["label"], b["from_bar"], b["to_bar"]) for b in blocks]


def test_hooks_share_the_most_repeated_span_and_verses_run_in_order() -> None:
    text = [("intro", 4), ("verse", 8), ("hook", 8), ("verse", 8), ("hook", 8)]
    blocks = propose(text, beat_features())
    assert spans(blocks) == [
        ("intro", 0, 4),
        ("verse 1", 4, 12),
        ("hook 1", 8, 16),
        ("verse 2", 12, 20),
        ("hook 2", 8, 16),
    ]


def test_the_earliest_of_equal_spans_wins() -> None:
    motif = np.array([[1, 0], [0, 1], [1, 1], [2, 1]], dtype=float)
    rows = np.concatenate([motif, motif])
    assert propose([("hook", 4)], rows)[0]["from_bar"] == 0


def test_a_text_longer_than_the_source_loops_to_bar_intro() -> None:
    features = np.random.default_rng(1).standard_normal((12, 4))
    blocks = propose([("verse", 6), ("verse", 6)], features, intro=2)
    assert spans(blocks) == [
        ("verse 1", 2, 8),
        ("verse 2", 8, 12),
        ("verse 2", 2, 4),
    ]


def test_an_unknown_kind_is_a_verse_and_no_hook_gives_verses_only() -> None:
    features = np.random.default_rng(2).standard_normal((20, 4))
    blocks = propose([("bridge", 3), ("other", 2), ("verse", 0)], features)
    assert spans(blocks) == [("verse 1", 4, 7), ("verse 2", 7, 9)]


def test_outro_takes_the_last_bars() -> None:
    features = np.random.default_rng(2).standard_normal((20, 4))
    blocks = propose([("verse", 4), ("outro", 1)], features, outro=4)
    assert spans(blocks)[-1] == ("outro", 16, 20)


def test_blocks_are_accepted_by_the_flip_model() -> None:
    blocks = propose([("intro", 4), ("hook", 8), ("verse", 8)], beat_features())
    data = {
        "format": 1,
        "source": "input/beats/x.wav",
        "grid": {"bpm": 140.0, "downbeat_s": 0.0, "swing": 50},
        "bass808": {"mode": "original", "params": {}},
        "hats": {"mode": "original", "remove": [], "patterns": []},
        "gains": {"drums": 0, "other": 0, "bass": 0},
        "arrangement": blocks,
        "effects": [],
    }
    assert parse(data).play_bars == 20


def chord(freqs: list[float], seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return np.sin(2 * np.pi * np.array(freqs)[:, None] * t).mean(axis=0)


def test_bar_features_put_equal_chords_closer_than_different_ones() -> None:
    first, second = [261.63, 329.63, 392.0], [293.66, 369.99, 440.0]
    mono = np.concatenate([chord(first if i % 2 == 0 else second, 1.0) for i in range(8)])
    features = bar_features(mono, RATE, [float(i) for i in range(8)])
    assert features.shape == (8, 25)
    same = np.linalg.norm(features[0] - features[2])
    different = np.linalg.norm(features[0] - features[1])
    assert same < different / 3


def test_bar_features_of_a_first_bar_before_the_file_are_finite():
    rate = 22050
    t = np.arange(int(20 * rate)) / rate
    mono = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    starts = [-0.015 + 2.0 * k for k in range(10)]
    assert np.isfinite(bar_features(mono, rate, starts)).all()
