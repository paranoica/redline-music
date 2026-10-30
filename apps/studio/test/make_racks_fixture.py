"""Writes racks-python.json: what redline.engine.live.racks gives for a set of settings.

The page builds the same racks in TypeScript between two analyses; the test checks them
against these. Run from the repository root:

    uv run python apps/studio/test/make_racks_fixture.py
"""

import json
from pathlib import Path

from redline.engine.live import racks, vocal_lanes
from redline.engine.settings import Settings, from_dict, to_dict

NUMBERS = {
    "rate": 48000,
    "pre_gain_db": 7.25,
    "gate_db": -63.5,
    "makeup_db": 21.75,
    "peak_db": -4.5,
    "eq_fit_db": [1.5, -2.0, 0.0, 3.25, -1.0, 2.5],
    "post_gain_db": 2.5,
    "tuning_cents": 13.0,
    "bitcrush_peak_db": -3.0,
    "throw_threshold_db": -31.0,
    "master_target_lufs": -9.0,
    "master_gain_db": 11.5,
    "master_trim_db": -0.4,
}

CASES = {
    "defaults": {},
    "chain off": {"chain_on": False},
    "no drive": {"drive_db": 0.0},
    "clip off": {"clip_on": False},
    "eq offsets": {"eq_200": 3.0, "eq_1000": -12.0, "eq_8000": 12.0},
    "eq fit off": {"eq_fit": False, "eq_2500": 4.5},
    "tune and formant": {"hard_tune": 60.0, "formant": 1.25},
    "octave and nightcore": {"voice_octave": -1, "nightcore": True},
    "layers": {
        "octave_down_on": False,
        "octave_up_on": True,
        "octave_up_db": -6.0,
        "doubles_wander_ms": 6.0,
        "bitcrush_on": True,
        "bitcrush_bits": 5,
        "bitcrush_hold": 4,
    },
    "fx": {"throw_on": True, "reverb_on": True, "reverb_mix_db": -10.0},
    "loud vocal": {"vocal_db": 6.0},
    "quiet vocal": {"vocal_db": -24.0},
    "beat": {"beat_db": 3.0, "bass_drive_db": 0.0},
    "master off": {"master_on": False},
    "user rack": {
        "version": 2,
        "rack": [
            {"effect": "delay", "params": {"mode": "pingpong", "time": 250.0}},
            {
                "effect": "gain",
                "on": False,
                "params": {"db": -3.0},
                "automation": [{"param": "db", "points": [[0.0, -3.0], [2.0, 0.0]]}],
            },
        ],
    },
}


def main() -> None:
    """Write the fixture next to this script."""
    out = {"numbers": NUMBERS, "cases": {}}
    for name, changes in CASES.items():
        settings = from_dict({**to_dict(Settings()), **changes}) if changes else Settings()
        built = racks(settings, NUMBERS)
        out["cases"][name] = {
            "settings": to_dict(settings),
            "racks": {track: json.loads(text) for track, text in built.items()},
            "lanes": [
                {"slot": slot, "param": param, "points": points}
                for slot, param, points in vocal_lanes(settings, NUMBERS)
            ],
        }
    path = Path(__file__).with_name("fixtures") / "racks-python.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
