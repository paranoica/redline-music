"""Synthetic takes over a click beat for the takes tests; no real audio."""

import numpy as np
import soundfile as sf

RATE = 48000
BPM = 100.0
BEAT_S = 60.0 / BPM
SECONDS = 12.0
FREQS = (196.0, 247.0, 294.0, 330.0, 262.0)


def click_beat(seconds=SECONDS, rate=RATE):
    """Clicks on every beat over a decaying 55 Hz kick, shape (n, 2)."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    mono = 0.4 * np.sin(2 * np.pi * 55 * t) * np.exp(-((t % BEAT_S) / 0.15))
    click = np.random.default_rng(1).standard_normal(int(0.003 * rate))
    click *= 0.6 * np.exp(-np.arange(click.size) / (0.0008 * rate))
    for start in np.arange(0.0, seconds, BEAT_S):
        i = int(start * rate)
        end = min(n, i + click.size)
        mono[i:end] += click[: end - i]
    return np.column_stack([mono, mono]).astype(np.float32)


def vocal(seed, shift_s=0.0, seconds=SECONDS, rate=RATE, jitter_s=0.003):
    """Tone bursts on the eighth notes from beat 3, a rest at the end of every second bar."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    mono = 1e-4 * rng.standard_normal(n)
    length = int(0.15 * rate)
    t = np.arange(length) / rate
    # a smooth tail: a hard stop reads as a second onset
    envelope = np.minimum(t / 0.005, 1.0) * np.exp(-t / 0.08) * np.cos(np.pi / 2 * t / t[-1])
    eighth = BEAT_S / 2
    for k in range(int((seconds - 2 * BEAT_S) / eighth)):
        if k % 16 >= 12:
            continue
        jitter = float(np.clip(rng.normal(0, jitter_s), -0.006, 0.006))
        onset = 2 * BEAT_S + k * eighth + shift_s + jitter
        i = int(onset * rate)
        if i + length > n:
            break
        f = FREQS[k % len(FREQS)]
        tone = sum(np.sin(2 * np.pi * f * h * t) / h for h in (1, 2, 3))
        mono[i : i + length] += 0.4 * envelope * tone
    return np.column_stack([mono, mono]).astype(np.float32)


def put(folder, name, data, rate=RATE):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    sf.write(path, data, rate, subtype="FLOAT")
    return path
