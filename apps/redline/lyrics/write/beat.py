"""The tempo of the beat a text is written for."""

import math
from collections.abc import Iterable
from pathlib import Path

import librosa
import soundfile as sf

from redline.core.audio import AudioError, decode
from redline.core.pitch import tempo_candidates
from redline.lyrics.text.parse import LyricsError

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


# Set between the owner's three type beats: the one they called hard read 2144 Hz, the two
# others 1751 and 1975. Three beats are a thin base, so the owner can name the delivery instead
HARD_BRIGHTNESS_HZ = 2050.0
ANALYSIS_RATE = 22050


def beat_brightness(path: Path) -> float:
    """The beat's mean spectral centroid, in Hz: bright synths and hard 808s read higher."""
    try:
        decoded = decode(path)
    except AudioError as error:
        raise LyricsError(str(error)) from error
    mono = librosa.resample(
        decoded.samples.mean(axis=1), orig_sr=decoded.rate, target_sr=ANALYSIS_RATE
    )
    return float(librosa.feature.spectral_centroid(y=mono, sr=ANALYSIS_RATE).mean())


def delivery_of(brightness_hz: float) -> str:
    """Hard or calm, by the beat's brightness."""
    return "hard" if brightness_hz >= HARD_BRIGHTNESS_HZ else "calm"
