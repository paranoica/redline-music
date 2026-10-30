"""The 808 notes of a bass stem: starts, pitches, glides and the stem's detune."""

import math
from dataclasses import dataclass

import librosa
import numpy as np
import numpy.typing as npt

from redline.flip.grid import (
    ANALYSIS_RATE,
    ENV_HOP,
    Grid,
    attacks,
    envelope,
    low_band,
)

Samples = npt.NDArray[np.float32]
Floats = npt.NDArray[np.float64]

FMIN_HZ = 28.0
FMAX_HZ = 140.0
LONG_FRAME = 8192
SHORT_FRAMES = (4096, 2048)
PYIN_HOP = 1024
GLIDE_FRAME = 2048
GLIDE_HOP = 256
LOW_CONFIDENCE = 0.5
STEADY_SEMITONES = 0.5
SHORT_SCALE = 0.85
ZERO_CROSSING_CONFIDENCE = 0.3
MIN_ZERO_CROSSING_S = 0.04
# the gate: an 808 still sounds this long after its attack, a kick's blip does not
SUSTAIN_FROM_S = 0.055
SUSTAIN_TO_S = 0.13
SUSTAIN_SHARE = 0.3
END_SHARE = 0.1
MAX_NOTE_S = 4.0
# a note's pitch is read after the first part of it, where a pitch drop or a slide happens
SKIP_S = 0.12
SKIP_SHARE = 0.2
GLIDE_WINDOW_S = 0.15
GLIDE_MIN_SEMITONES = 1.0
GLIDE_MAX_SEMITONES = 12.0
GLIDE_SHARE = 0.35
GLIDE_GAP_S = 0.03
MIDI_LOW = 23
MIDI_HIGH = 60
OCTAVE_JUMP = 9.0
# a note of the same pitch starts anew only when its level rises this much over the dip before it
REATTACK = 2.5
REATTACK_FAST = 1.3
REATTACK_RISE_S = 0.02
# how near a sixteenth (as a share of one) a shallow strike must start
ON_GRID_SHARE = 0.15
DIP_S = 0.03


@dataclass(frozen=True)
class Note:
    """One 808 note.

    Attributes:
        start_s: Start in seconds.
        end_s: End in seconds: the next note's start or where the note has died away.
        midi: MIDI note number on the equal-tempered grid, detune removed.
        confidence: 0..1; below LOW_CONFIDENCE the pitch is a guess.
        glide_into_next: The pitch slides from this note's into the next one's start.
    """

    start_s: float
    end_s: float
    midi: int
    confidence: float
    glide_into_next: bool = False


@dataclass(frozen=True)
class StepNote:
    """A note on the sixteenth grid.

    Attributes:
        step: Start, in sixteenths from the first downbeat of the song.
        length: Length in sixteenths; a glide reaches one past the next note's start.
        midi: MIDI note number.
        confidence: The note's confidence.
    """

    step: int
    length: int
    midi: int
    confidence: float


def _hz_to_midi(hz: float) -> float:
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def _zero_crossing_hz(x: Floats) -> float | None:
    x = x - x.mean()
    sign = np.signbit(x)
    at = np.flatnonzero(sign[1:] != sign[:-1])
    if at.size < 3:
        return None
    exact = at - x[at] / (x[at + 1] - x[at])
    span = (exact[-1] - exact[0]) / ANALYSIS_RATE
    return float((at.size - 1) / (2 * span)) if span > 0 else None


def _pyin(x: Floats, frame: int, hop: int) -> tuple[Floats, int]:
    f0, voiced, _ = librosa.pyin(
        x,
        fmin=FMIN_HZ,
        fmax=FMAX_HZ,
        sr=ANALYSIS_RATE,
        frame_length=frame,
        hop_length=hop,
        center=False,
    )
    keep = voiced & np.isfinite(f0)
    return f0[keep], f0.size


def _pitch(x: Floats) -> tuple[float, float] | None:
    """The pitch of a stretch of one note in MIDI (fractional) and a confidence."""
    if x.size >= LONG_FRAME:
        frame, scale = LONG_FRAME, 1.0
    else:
        frame, scale = next(((f, SHORT_SCALE) for f in SHORT_FRAMES if x.size >= f), (0, 0.0))
    if frame:
        f0, frames = _pyin(x, frame, PYIN_HOP if frame == LONG_FRAME else GLIDE_HOP)
        if f0.size and f0.size >= 0.5 * frames:
            midi = 69.0 + 12.0 * np.log2(f0 / 440.0)
            median = float(np.median(midi))
            steady = float(np.mean(np.abs(midi - median) < STEADY_SEMITONES))
            return median, scale * steady * f0.size / frames
    if x.size >= MIN_ZERO_CROSSING_S * ANALYSIS_RATE:
        hz = _zero_crossing_hz(x)
        if hz is not None and FMIN_HZ <= hz <= FMAX_HZ:
            return _hz_to_midi(hz), ZERO_CROSSING_CONFIDENCE
    return None


def _segments(
    env: Floats, low: Floats, starts: list[float], tops: list[int]
) -> list[tuple[int, int]]:
    """Sample ranges (start, end) of each note: to the next attack or until it has died away."""
    out = []
    for i, start in enumerate(starts):
        a = int(start * ANALYSIS_RATE)
        limit = int(starts[i + 1] * ANALYSIS_RATE) if i + 1 < len(starts) else low.size
        limit = min(limit, a + int(MAX_NOTE_S * ANALYSIS_RATE))
        peak = float(env[tops[i]])
        frames = env[tops[i] : limit // ENV_HOP + 1]
        below = np.flatnonzero(frames < END_SHARE * peak)
        end = (tops[i] + int(below[0])) * ENV_HOP if below.size else limit
        out.append((a, max(a + ENV_HOP, min(end, limit))))
    return out


def _sustained(env: Floats, start_s: float, next_s: float | None, top: int) -> bool:
    """Whether the stem keeps sounding after the attack, as an 808 does and a kick's blip not."""
    gap = (next_s - start_s) if next_s is not None else SUSTAIN_TO_S + 0.1
    first = min(SUSTAIN_FROM_S, 0.5 * gap)
    last = min(SUSTAIN_TO_S, gap - 0.005)
    scale = ANALYSIS_RATE / ENV_HOP
    lo, hi = int((start_s + first) * scale), int((start_s + last) * scale)
    window = env[lo : max(hi, lo + 1)]
    if window.size == 0:
        return False
    return float(window.mean()) >= SUSTAIN_SHARE * float(env[top])


def _stretch(low: Floats, a: int, b: int) -> Floats:
    skip = int(min(SKIP_S, SKIP_SHARE * (b - a) / ANALYSIS_RATE) * ANALYSIS_RATE)
    return low[a + skip : b]


def _fold(midi: list[float]) -> list[float]:
    """Octave errors: a note far from both neighbours moves by octaves to sit between them."""
    clamped = []
    for m in midi:
        while m < MIDI_LOW:
            m += 12
        while m > MIDI_HIGH:
            m -= 12
        clamped.append(m)
    out = list(clamped)
    for i in range(1, len(clamped) - 1):
        near = (clamped[i - 1], clamped[i + 1])
        if min(abs(clamped[i] - n) for n in near) < OCTAVE_JUMP:
            continue
        for shift in (-12.0, 12.0):
            if max(abs(clamped[i] + shift - n) for n in near) < OCTAVE_JUMP:
                out[i] = clamped[i] + shift
                break
    return out


def _detune(midi: list[float], confidence: list[float]) -> float:
    """Cents the notes sit off the equal-tempered grid, by the circular mean of confident ones."""
    sure = [m for m, c in zip(midi, confidence, strict=True) if c >= LOW_CONFIDENCE]
    if not sure:
        return 0.0
    angle = 2 * np.pi * np.array(sure)
    return float(100 * math.atan2(np.sin(angle).sum(), np.cos(angle).sum()) / (2 * np.pi))


def _glide(low: Floats, a: int, own: float, previous: float) -> bool:
    """Whether the note starting at sample a begins on the previous pitch and slides to its own."""
    gap = abs(previous - own)
    if not GLIDE_MIN_SEMITONES <= gap <= GLIDE_MAX_SEMITONES:
        return False
    x = low[a : a + int(GLIDE_WINDOW_S * ANALYSIS_RATE) + GLIDE_FRAME]
    if x.size < GLIDE_FRAME:
        return False
    f0, _ = _pyin(x, GLIDE_FRAME, GLIDE_HOP)
    if f0.size == 0:
        return False
    first = _hz_to_midi(float(f0[0]))
    towards = (first - own) * (previous - own)
    return towards > 0 and abs(first - own) >= GLIDE_SHARE * gap


def find_notes(bass_mono: Samples, rate: int, grid: Grid | None = None) -> tuple[list[Note], float]:
    """The 808 notes of a bass stem and how far its tuning sits from A = 440 Hz.

    Args:
        bass_mono: Mono bass stem.
        rate: Its sample rate.
        grid: The beat's grid when known: a shallow fast rise counts as a new strike of the
            same pitch only near a sixteenth (a ripple on a held 808 falls anywhere).

    Returns:
        The notes in time order, and the stem's detune in cents (+ is sharp) which the notes'
        MIDI numbers already leave out.
    """
    low = low_band(bass_mono, rate)
    env = envelope(low, 0.015)
    found = attacks(env)
    starts = [t for t, _, _ in found]
    tops = [top for _, _, top in found]
    kept = [
        i
        for i in range(len(found))
        if _sustained(env, starts[i], starts[i + 1] if i + 1 < len(found) else None, tops[i])
    ]
    starts = [starts[i] for i in kept]
    tops = [tops[i] for i in kept]
    if not starts:
        return [], 0.0
    ranges = _segments(env, low, starts, tops)
    pitches = [_pitch(_stretch(low, a, b)) for a, b in ranges]
    midi = [p[0] if p else math.nan for p in pitches]
    confidence = [p[1] if p else 0.0 for p in pitches]
    last = next((m for m in midi if not math.isnan(m)), 36.0)
    for i, m in enumerate(midi):
        if math.isnan(m):
            midi[i] = last
        last = midi[i]
    midi = _fold(midi)
    detune = _detune(midi, confidence)
    tuned = [m - detune / 100.0 for m in midi]
    rounded = [round(m) for m in tuned]
    notes: list[Note] = []
    for i, (a, b) in enumerate(ranges):
        glides = False
        if i + 1 < len(ranges) and ranges[i + 1][0] - b <= GLIDE_GAP_S * ANALYSIS_RATE:
            glides = _glide(low, ranges[i + 1][0], tuned[i + 1], tuned[i])
        if notes and notes[-1].midi == rounded[i] and not _reattacks(env, a, tops[i], grid):
            # a held 808 whose level ripples (drive, beating with the kick) is one note
            last = notes[-1]
            notes[-1] = Note(
                last.start_s,
                b / ANALYSIS_RATE,
                last.midi,
                max(last.confidence, confidence[i]),
                glides,
            )
            continue
        notes.append(Note(a / ANALYSIS_RATE, b / ANALYSIS_RATE, rounded[i], confidence[i], glides))
    return notes, detune


def _on_grid(seconds: float, grid: Grid | None) -> bool:
    if grid is None:
        return True
    phase = ((seconds - grid.downbeat_s) / grid.step_s) % 1.0
    return min(phase, 1.0 - phase) <= ON_GRID_SHARE


def _reattacks(env: Floats, start: int, top: int, grid: Grid | None = None) -> bool:
    """Whether the level rises from a real dip before `start` (a sample), as a struck note does.

    `top` is the envelope frame of the note's peak; the envelope has one frame per ENV_HOP.
    """
    at = min(start // ENV_HOP, env.size - 1, top)
    lo = max(0, at - int(DIP_S * ANALYSIS_RATE / ENV_HOP))
    dip_at = lo + int(np.argmin(env[lo : top + 1]))
    rise = float(env[top]) / max(float(env[dip_at]), 1e-9)
    # a strike rises fast even from a shallow dip; a held note's ripple rises over half its period
    fast = (top - dip_at) * ENV_HOP / ANALYSIS_RATE <= REATTACK_RISE_S
    shallow = fast and rise >= REATTACK_FAST and _on_grid(start / ANALYSIS_RATE, grid)
    return rise >= REATTACK or shallow


def to_steps(notes: list[Note], grid: Grid) -> list[StepNote]:
    """Snap notes to the sixteenth grid.

    Steps count from the first downbeat of the grid (a note before it has a negative step). A
    note is as long as it sounds without running into the next one, except a glide: the
    synth glides only when notes overlap, so it reaches one step past the next note's start.
    Two notes on one step keep the first.
    """
    step_s = grid.step_s
    snapped: list[tuple[Note, int, int]] = []
    for note in notes:
        step = round((note.start_s - grid.downbeat_s) / step_s)
        if snapped and snapped[-1][1] == step:
            continue
        end = round((note.end_s - grid.downbeat_s) / step_s)
        snapped.append((note, step, max(1, end - step)))
    out = []
    for i, (note, step, length) in enumerate(snapped):
        if i + 1 < len(snapped):
            following = snapped[i + 1][1]
            length = following - step + 1 if note.glide_into_next else min(length, following - step)
        out.append(StepNote(step, length, note.midi, note.confidence))
    return out
