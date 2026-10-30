"""Writes the native rack's output that apps/studio/test/wasm.test.mjs compares the wasm build to.

Run after a change to the gain or pitch effects, with `redline_dsp` built from this tree:
`python tests/redline/core/make_wasm_fixture.py`. The node test makes the same input itself.
"""

import base64
import json
from pathlib import Path

import numpy as np
import redline_dsp

RATE = 48000
FRAMES = 32768
GAIN_FRAMES = 4096
# the pitch output is compared over this window, past the stretcher's start
WINDOW = (8192, 8192 + 16384)
FIXTURE = Path(__file__).resolve().parents[3] / "apps/studio/test/fixtures/wasm-native.json"

RACKS = {
    "gain": {"effects": [{"effect": "gain", "params": {"db": -6}}]},
    "pitch": {
        "effects": [
            {"effect": "gain", "params": {"db": -6}},
            {"effect": "pitch", "params": {"semitones": 12}},
        ]
    },
}


def sines(frames: int) -> np.ndarray:
    """375 Hz at half scale on the left, 625 Hz at a quarter on the right, as the node test.

    Args:
        frames: how many stereo frames.

    Returns:
        float32 samples, (frames, 2).
    """
    t = np.arange(frames, dtype=np.float64) / RATE
    left = 0.5 * np.sin(2 * np.pi * 375.0 * t)
    right = 0.25 * np.sin(2 * np.pi * 625.0 * t)
    return np.stack([left, right], axis=1).astype(np.float32)


def encode(samples: np.ndarray) -> str:
    """Little-endian float32 samples as base64."""
    return base64.b64encode(np.ascontiguousarray(samples, dtype="<f4").tobytes()).decode()


def main() -> None:
    """Renders both racks natively and writes the fixture."""
    x = sines(FRAMES)
    cases = {}
    for name, rack in RACKS.items():
        text = json.dumps(rack)
        out = redline_dsp.rack_process(x, float(RATE), text)
        _, latency = redline_dsp.rack_alignment(float(RATE), text)
        if name == "gain":
            frames, samples = (0, GAIN_FRAMES), out[:GAIN_FRAMES].reshape(-1)
        else:
            frames, samples = WINDOW, out[WINDOW[0] : WINDOW[1], 0]
        cases[name] = {
            "rack": rack,
            "latency": latency,
            "frames": list(frames),
            "interleaved": name == "gain",
            "samples": encode(samples),
        }
    fixture = {"rate": RATE, "frames": FRAMES, "cases": cases}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=1) + "\n")


if __name__ == "__main__":
    main()
