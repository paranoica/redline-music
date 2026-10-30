"""Writes the native beat renders that apps/studio/test/beat.test.mjs compares the wasm build to.

Run after a change to the beat voices or player, with `redline_dsp` built from this tree:
`python tests/redline/core/make_beat_fixture.py`. Each case holds an 8 s note (or a bar of
drums repeated) so drift between the native and the wasm maths shows by the note's end; only
a few windows of each render are kept, to keep the fixture small.
"""

import base64
import json
from pathlib import Path

import numpy as np
import redline_dsp

RATE = 48000
WINDOW = 512
FIXTURE = Path(__file__).resolve().parents[3] / "apps/studio/test/fixtures/beat-native.json"

# 4 bars at 120 bpm: 8 s, 64 sixteenths
BARS = 4
STEPS = 16 * BARS


def _long_note(instrument: str, params: dict, note: str) -> dict:
    return {
        "sounds": {"v": {"instrument": instrument, "params": params}},
        "rows": {"v": [{"step": 0, "len": STEPS, "note": note, "vel": 0.9}]},
    }


DRUMS = {
    "sounds": {
        "kick": {"instrument": "kick"},
        "snare": {"instrument": "snare"},
        "clap": {"instrument": "clap"},
        "hat": {"instrument": "hat_closed"},
        "open": {"instrument": "hat_open", "params": {"decay": 2.0}},
        "rim": {"instrument": "rim"},
        "bell": {"instrument": "cowbell"},
    },
    "rows": {
        "kick": "x.....x...x.....|x..x......x..x..|x.....x...x.....|x..x......x..x..",
        "snare": "....x.......x...|....x.......x.g.|....x.......x...|....x.......x.g.",
        "clap": "....x.......x...|................|....x.......x...|............x...",
        "hat": "x3x.x4x.x6xgx2x.|xxxxxxxx4xxxxx6x|x3x.x4x.x6xgx2x.|xxxxxxxx4xxxxx6x",
        "open": "..x.......x.....|..x.............|..x.......x.....|..............x.",
        "rim": "...x.......x....|...x............|...x.......x....|...x.......x....",
        "bell": "..........x.....|..........x.....|..........x.....|..........x.....",
    },
}

CASES = {
    "bass808": _long_note("bass808", {"decay": 8, "drive": 0.5}, "A1"),
    "saw": _long_note("saw", {"release": 0.5}, "C3"),
    "pluck": _long_note("pluck", {"decay": 10}, "C3"),
    "fm": _long_note("fm", {"preset": "keys", "decay": 8}, "C4"),
    "drums": DRUMS,
    "racks": {
        **DRUMS,
        "mix": {
            "sounds": {
                "kick": {"rack": [{"effect": "saturate", "params": {"drive": 12}}]},
                "hat": {"pan": -0.4, "gain_db": -3},
            },
            "master": [{"effect": "limiter"}],
        },
    },
}


def score(case: dict) -> dict:
    """A whole score of one case: four bars of one pattern.

    Args:
        case: the sounds, the pattern's rows and an optional mix.

    Returns:
        The score as `json.loads` gives it.
    """
    out = {
        "version": 1,
        "bpm": 120,
        "seed": 11,
        "key": "A minor",
        "sounds": case["sounds"],
        "patterns": {"p": {"bars": BARS, "rows": case["rows"]}},
        "sections": [{"name": "a", "bars": BARS, "play": ["p"]}],
    }
    if "mix" in case:
        out["mix"] = case["mix"]
    return out


def windows(length: int, rendered: int) -> list[int]:
    """Where the kept windows start: the note's start, middle and end, and its tail.

    Args:
        length: frames of the song.
        rendered: frames of the render, tail included.

    Returns:
        The first frame of each window.
    """
    # an 808 has died away by 8 s: its render has no tail to keep
    return [0, length // 2, length - WINDOW, min(length, rendered - WINDOW)]


def encode(samples: np.ndarray) -> str:
    """Little-endian float32 samples as base64."""
    return base64.b64encode(np.ascontiguousarray(samples, dtype="<f4").tobytes()).decode()


def main() -> None:
    """Renders every case natively and writes the fixture."""
    cases = {}
    for name, case in CASES.items():
        text = json.dumps(score(case))
        mix = redline_dsp.beat_render(text, float(RATE))
        player = redline_dsp.BeatPlayer(text, float(RATE))
        starts = windows(player.length, len(mix))
        cases[name] = {
            "score": json.loads(text),
            "latency": player.latency,
            "length": player.length,
            "windows": starts,
            "samples": [encode(mix[s : s + WINDOW]) for s in starts],
        }
    fixture = {"rate": RATE, "window": WINDOW, "cases": cases}
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, indent=1) + "\n")


if __name__ == "__main__":
    main()
