"""Blind listening: every variant at one loudness, under shuffled letters."""

import random
import string
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf

from redline.core.audio import true_peak_db
from redline.core.loudness import integrated_lufs

Samples = npt.NDArray[np.floating]

LOUDEST_LUFS = -14.0
CEILING_DB = -1.0
# the 4x true-peak estimate can read a little under the real inter-sample peak
PEAK_MARGIN_DB = 0.3


def common_lufs(variants: dict[str, Samples], rate: int) -> float:
    """The loudest level at which every variant keeps its true peak under the ceiling."""
    level = LOUDEST_LUFS
    for samples in variants.values():
        headroom = CEILING_DB - PEAK_MARGIN_DB - true_peak_db(samples)
        level = min(level, integrated_lufs(samples, rate) + headroom)
    return level


def write_listen_set(
    variants: dict[str, Samples], rate: int, out_dir: Path, seed: int
) -> dict[str, str]:
    """Write each variant as <letter>.wav at one loudness; return letter -> variant name."""
    if len(variants) > len(string.ascii_uppercase):
        raise ValueError(f"too many variants: {len(variants)}")
    target = common_lufs(variants, rate)
    names = random.Random(seed).sample(sorted(variants), len(variants))
    key = dict(zip(string.ascii_uppercase, names, strict=False))
    out_dir.mkdir(parents=True, exist_ok=True)
    for letter, name in key.items():
        samples = variants[name]
        gain = 10 ** ((target - integrated_lufs(samples, rate)) / 20)
        sf.write(out_dir / f"{letter}.wav", samples * gain, rate, subtype="PCM_24")
    return key
