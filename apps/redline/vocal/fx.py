"""Vocal effects that live around the phrases: reverb, delay throws and stutter at phrase ends."""

import numpy as np
import numpy.typing as npt
from scipy.signal import butter, fftconvolve, sosfilt

Samples = npt.NDArray[np.floating]

FRAME_S = 0.02
HOP_S = 0.01
# a frame counts as voice within this many dB of the loud part of the vocal
VOICED_BELOW_LOUD_DB = 30.0
LOUD_PERCENTILE = 95
# a pause shorter than this is a breath inside a phrase, not its end
MIN_GAP_S = 0.2
THROW_TAIL_S = 0.3
MAX_ECHOES = 8
QUIETEST_ECHO = 1e-3
FADE_S = 0.004
REVERB_SEED = 20261029
MAX_REVERB_S = 8.0


def _frames_db(mono: Samples, rate: int) -> npt.NDArray[np.float64]:
    frame, hop = int(FRAME_S * rate), int(HOP_S * rate)
    count = max(0, 1 + (len(mono) - frame) // hop)
    if count == 0:
        return np.zeros(0)
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    power = np.mean(np.square(mono[index], dtype=np.float64), axis=1)
    return 10 * np.log10(power + 1e-20)


def phrases(samples: Samples, rate: int) -> list[tuple[int, int]]:
    """Start and end sample of every phrase: voice runs split by pauses of MIN_GAP_S or more."""
    db = _frames_db(samples.mean(axis=1), rate)
    heard = db[db > -100]
    if heard.size == 0:
        return []
    voiced = db > np.percentile(heard, LOUD_PERCENTILE) - VOICED_BELOW_LOUD_DB
    frame, hop = int(FRAME_S * rate), int(HOP_S * rate)
    # a frame overlaps the voice on both sides of a pause: the silent frames between two
    # phrases are the pause minus a frame
    gap = round((MIN_GAP_S - FRAME_S) / HOP_S)
    out: list[tuple[int, int]] = []
    start = None
    last = -1
    for i, on in enumerate(voiced):
        if on:
            if start is None:
                start = i
            elif i - last > gap:
                out.append((start * hop, last * hop + frame))
                start = i
            last = i
    if start is not None:
        out.append((start * hop, last * hop + frame))
    # the end to the sample: the last one still near the voice's level in the last frame. The
    # start stays at its frame, which begins no later than the voice does
    mono = np.abs(samples.mean(axis=1))
    floor = 10 ** ((np.percentile(heard, LOUD_PERCENTILE) - VOICED_BELOW_LOUD_DB) / 20)
    exact = []
    for s, e in out:
        e = min(e, len(samples))
        loud = np.flatnonzero(mono[max(s, e - frame) : e] > floor)
        exact.append((s, max(s, e - frame) + int(loud[-1]) + 1 if loud.size else e))
    return exact


def _lowpass(x: Samples, rate: int, hz: float) -> Samples:
    if hz >= rate / 2 * 0.95:
        return x
    sos = butter(2, hz, fs=rate, output="sos")
    return np.asarray(sosfilt(sos, x, axis=0), dtype=np.float32)


def reverb(
    samples: Samples, rate: int, mix_db: float, decay_s: float, predelay_ms: float, tone_hz: float
) -> Samples:
    """A room from decaying noise: 60 dB down after decay_s, dark above tone_hz, seeded."""
    length = int(min(decay_s * 1.2, MAX_REVERB_S) * rate)
    rng = np.random.default_rng(REVERB_SEED)
    t = np.arange(length) / rate
    ir = rng.standard_normal((length, 2)) * np.exp(-6.9078 * t / decay_s)[:, None]
    ir = _lowpass(ir.astype(np.float32), rate, tone_hz)
    # unit energy: the wet signal reads about as loud as the dry before mix_db
    ir = ir / np.sqrt(np.sum(np.square(ir, dtype=np.float64), axis=0))
    ir = np.concatenate([np.zeros((int(predelay_ms / 1000 * rate), 2)), ir])
    wet = np.column_stack([fftconvolve(samples[:, c], ir[:, c])[: len(samples)] for c in range(2)])
    return (samples + wet * 10 ** (mix_db / 20)).astype(np.float32)


def throws(
    samples: Samples,
    rate: int,
    level_db: float,
    time_ms: float,
    feedback: float,
    tone_hz: float,
    found: list[tuple[int, int]] | None = None,
) -> Samples:
    """The last words of each phrase thrown into echoes that bounce left and right.

    found: the phrases, when another effect already changed the pauses they are read from.
    """
    ends = [end for _, end in (phrases(samples, rate) if found is None else found)]
    if not ends:
        return samples.copy()
    mono = samples.mean(axis=1)
    send = np.zeros(len(samples), dtype=np.float64)
    tail = int(THROW_TAIL_S * rate)
    for end in ends:
        send[max(0, end - tail) : end] = mono[max(0, end - tail) : end]
    step = int(time_ms / 1000 * rate)
    wet = np.zeros((len(samples), 2), dtype=np.float64)
    gain = 10 ** (level_db / 20)
    for k in range(MAX_ECHOES):
        if gain < QUIETEST_ECHO or step * (k + 1) >= len(samples):
            break
        shift = step * (k + 1)
        wet[shift:, k % 2] += send[: len(samples) - shift] * gain
        gain *= feedback
    return (samples + _lowpass(wet.astype(np.float32), rate, tone_hz)).astype(np.float32)


def stutter(
    samples: Samples,
    rate: int,
    slice_ms: float,
    repeats: int,
    found: list[tuple[int, int]] | None = None,
) -> Samples:
    """The last slice of each phrase repeated into the pause after it, never into the next."""
    found = phrases(samples, rate) if found is None else found
    piece = int(slice_ms / 1000 * rate)
    fade = min(int(FADE_S * rate), piece // 4)
    ramp = np.ones(piece, dtype=np.float32)
    if fade:
        ramp[:fade] = np.linspace(0, 1, fade, dtype=np.float32)
        ramp[-fade:] = np.linspace(1, 0, fade, dtype=np.float32)
    out = samples.copy()
    for i, (start, end) in enumerate(found):
        if end - start < piece:
            continue
        stop = found[i + 1][0] if i + 1 < len(found) else len(samples)
        chunk = samples[end - piece : end] * ramp[:, None]
        for k in range(repeats):
            at = end + k * piece
            if at + piece > stop:
                break
            out[at : at + piece] += chunk
    return out.astype(np.float32)
