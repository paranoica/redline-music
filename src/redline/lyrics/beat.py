"""The tempo of the beat a text is written for."""

import math
from collections.abc import Iterable
from pathlib import Path

import soundfile as sf

from redline.audio import AudioError, decode
from redline.lyrics.parse import LyricsError
from redline.pitch import tempo_candidates

# one octave, so exactly one of a tempo, its half and its double falls in it
HALF_TIME = (60.0, 120.0)
# four bars at the slowest half-time tempo; anything shorter gives a tempo of noise
MIN_SECONDS = 16.0


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
        # rounding can carry a value just under the octave's top onto it
        bpm = round(bpm, 1)
        return round(bpm / 2, 1) if bpm >= high else bpm
    raise LyricsError("no tempo found in the beat; pass --bpm")


def beat_bpm(path: Path) -> float:
    """The beat's measured tempo, folded to half time."""
    try:
        decoded = decode(path)
    except AudioError as error:
        raise LyricsError(str(error)) from error
    seconds = len(decoded.samples) / decoded.rate
    if seconds < MIN_SECONDS:
        raise LyricsError(f"the beat lasts {seconds:.1f} seconds; a tempo needs {MIN_SECONDS:g}")
    return half_time(tempo_candidates(decoded.samples, decoded.rate))


def beat_bars(path: Path, bpm: float) -> int:
    """Whole bars of the beat at its half-time tempo, one sung line each."""
    try:
        seconds = sf.info(path).duration
    except (sf.LibsndfileError, RuntimeError) as error:
        raise LyricsError(f"cannot read {path}: {error}") from error
    return int(seconds * bpm / 240)
