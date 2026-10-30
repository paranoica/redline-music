"""A first arrangement of a beat for a lyrics text: one source bar per sung line."""

from collections import Counter
from collections.abc import Sequence

import librosa
import numpy as np

HOP = 512
TIE = 1e-9


def bar_features(mono: np.ndarray, rate: int, bar_starts_s: Sequence[float]) -> np.ndarray:
    """Per bar the mean chroma (12) and mean MFCC (13), each standardized across the bars."""
    starts = np.asarray(bar_starts_s, dtype=np.float64)
    if len(starts) == 0:
        return np.zeros((0, 25))
    signal = np.asarray(mono, dtype=np.float32)
    chroma = librosa.feature.chroma_stft(y=signal, sr=rate, hop_length=HOP)
    mfcc = librosa.feature.mfcc(y=signal, sr=rate, hop_length=HOP, n_mfcc=13)
    frames = chroma.shape[1]
    ends = np.append(starts[1:], max(len(signal) / rate, starts[-1] + 1e-3))
    rows = []
    for begin, end in zip(starts, ends, strict=True):
        a = min(round(begin * rate / HOP), frames - 1)
        b = min(max(round(end * rate / HOP), a + 1), frames)
        rows.append(np.concatenate([chroma[:, a:b].mean(axis=1), mfcc[:, a:b].mean(axis=1)]))
    out = np.asarray(rows)
    for cols in (slice(0, 12), slice(12, 25)):
        part = out[:, cols]
        spread = part.std(axis=0)
        out[:, cols] = (part - part.mean(axis=0)) / np.where(spread > 1e-9, spread, 1.0)
    return out


def best_span(features: np.ndarray, length: int) -> int:
    """First bar of the span of `length` bars most like the other spans of that length.

    Spans overlapping the candidate are not counted: a span always resembles its neighbour
    shifted by one bar, which says nothing about a repeating part.
    """
    n = len(features)
    length = max(1, min(length, n))
    count = n - length + 1
    vectors = np.stack([features[i : i + length].ravel() for i in range(count)])
    norms = np.linalg.norm(vectors, axis=1)
    unit = vectors / np.where(norms > 1e-12, norms, 1.0)[:, None]
    sim = unit @ unit.T
    best, best_score = 0, -np.inf
    for i in range(count):
        others = [j for j in range(count) if abs(j - i) >= length] or [
            j for j in range(count) if j != i
        ]
        score = float(sim[i, others].mean()) if others else 0.0
        if score > best_score + TIE:
            best, best_score = i, score
    return best


def _label(kind: str, index: int, total: int) -> str:
    return kind if total == 1 else f"{kind} {index}"


def propose(
    sections: Sequence[tuple[str, int]],
    bar_features: np.ndarray,
    *,
    intro: int = 4,
    outro: int = 4,
) -> list[dict[str, int | str]]:
    """Blocks {label, from_bar, to_bar} in play order for a text of (kind, line_count) sections.

    A text intro or outro takes the first `intro` or the last `outro` source bars whatever its
    line count; every hook repeats the most self-similar span of its length; the other sections
    walk the source from bar `intro`, looping back to it at the end.
    """
    n = len(bar_features)
    if n == 0:
        raise ValueError("the beat has no bars")
    kept = [
        (kind if kind in ("intro", "hook", "outro") else "verse", c)
        for kind, c in sections
        if c > 0
    ]
    totals = Counter(kind for kind, _ in kept)
    start = intro if 0 <= intro < n else 0
    cursor = start
    spans: dict[int, int] = {}
    seen: Counter[str] = Counter()
    blocks: list[dict[str, int | str]] = []
    for kind, lines in kept:
        seen[kind] += 1
        label = _label(kind, seen[kind], totals[kind])
        if kind == "intro" and intro > 0:
            blocks.append({"label": label, "from_bar": 0, "to_bar": min(intro, n)})
        elif kind == "outro" and outro > 0:
            size = min(outro, n)
            blocks.append({"label": label, "from_bar": n - size, "to_bar": n})
        elif kind == "hook":
            length = min(lines, n)
            if length not in spans:
                spans[length] = best_span(bar_features, length)
            first = spans[length]
            blocks.append({"label": label, "from_bar": first, "to_bar": first + length})
        elif kind in ("intro", "outro"):
            continue
        else:
            left = lines
            while left > 0:
                take = min(left, n - cursor)
                blocks.append({"label": label, "from_bar": cursor, "to_bar": cursor + take})
                left -= take
                cursor = start if cursor + take >= n else cursor + take
    return blocks
