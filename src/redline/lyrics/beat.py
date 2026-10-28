"""The tempo of the beat a text is written for."""

import math
from collections.abc import Iterable
from pathlib import Path

from redline.audio import AudioError, decode
from redline.lyrics.parse import LyricsError
from redline.pitch import tempo_candidates

# one octave, so exactly one of a tempo, its half and its double falls in it
HALF_TIME = (60.0, 120.0)


def half_time(candidates: Iterable[float]) -> float:
    """The candidate in the half-time octave, where one bar holds one sung line."""
    low, high = HALF_TIME
    for bpm in candidates:
        if not (math.isfinite(bpm) and bpm > 0):
            continue
        while bpm < low:
            bpm *= 2
        while bpm >= high:
            bpm /= 2
        if low <= bpm < high:
            return round(bpm, 1)
    raise LyricsError("no tempo found in the beat; pass --bpm")


def beat_bpm(path: Path) -> float:
    """The beat's measured tempo, folded to half time."""
    try:
        decoded = decode(path)
    except AudioError as error:
        raise LyricsError(str(error)) from error
    return half_time(tempo_candidates(decoded.samples, decoded.rate))
