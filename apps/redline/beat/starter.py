"""A playable first score for a new own beat, written without the agent.

The owner builds the beat from it on the page: two bars of drums in the genre's habit, an 808
on the key's root starting with each kick, an empty melody pattern on the lead, and the recipe's
sections. It is a starting point, not a finished beat.
"""

import json
from pathlib import Path
from typing import Any

from redline.beat.recipe import Recipe, parse_key, recipe_json

# two bars of drums a family of genres starts from, by role
FAMILIES: dict[str, dict[str, tuple[str, str]]] = {
    "half_time": {
        "kick": ("x.........x.x...", "....x.......x..."),
        "snare": ("........x.......", "........x......."),
        "hat": ("x.x.x.x.x.x.x.3.", "x.x.x.x.x.x.x.3."),
        "open_hat": ("......x.........", "................"),
    },
    "drill": {
        "kick": ("x.............x.", "x..x..x.......x."),
        "snare": ("........x.......", "............x..."),
        "hat": ("x..x..x.x..x..x.", "x..x..x.x..x.4.."),
        "open_hat": ("................", "......x........."),
    },
    "jersey": {
        "kick": ("x...x...x..x..x.", "x...x...x..x..x."),
        "snare": ("........x..x....", "........x.xx...."),
        "hat": ("....x.......x...", "....x.......x..."),
        "open_hat": ("................", "................"),
    },
    "straight": {
        "kick": ("x.....x...x.....", "x.....x...x...x."),
        "snare": ("....x.......x...", "....x.......x..."),
        "hat": ("x.x.x.x.x.x.x.x.", "x.x.x.x.x.x.x.x."),
        "open_hat": ("......x.........", "..............x."),
    },
}
FAMILY_OF = {
    "uk_drill": "drill",
    "ny_drill": "drill",
    "jersey_club": "jersey",
    "boom_bap": "straight",
    "lofi": "straight",
    "cloud": "straight",
    "memphis": "straight",
}
# the roles a recipe's sound names play in the starter, first match wins
ROLES = {"kick": ("kick",), "snare": ("snare", "clap"), "hat": ("hat",), "open_hat": ("open_hat",)}


def _note(midi: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def starter_score(recipe: Recipe) -> dict[str, Any]:
    """The first score of a recipe: drums and 808 patterns over its sections.

    The intro plays the 808 alone, every later section both; the lead has an empty `melody`
    pattern that no section plays yet.
    """
    parsed = parse_key(recipe.key)
    tonic = parsed[0] if parsed else 0
    family = FAMILIES[FAMILY_OF.get(recipe.genre, "half_time" if recipe.half_time else "straight")]
    names = {s.name for s in recipe.sounds}
    rows: dict[str, str] = {}
    for role, candidates in ROLES.items():
        name = next((n for n in candidates if n in names), None)
        if name is not None and name not in rows:
            rows[name] = "|".join(family[role])
    bass = next((s.name for s in recipe.sounds if s.instrument in ("bass808", "sample808")), None)
    if bass is None:
        raise ValueError(f"recipe of seed {recipe.seed} has no 808 to start from")
    # the root in the 808's lowest octave: C1..B1, MIDI 24..35
    root = _note(24 + tonic)
    kick = "".join(family["kick"])
    kicks = [i for i, c in enumerate(kick) if c != "."]
    lengths = [b - a for a, b in zip(kicks, [*kicks[1:], len(kick)], strict=True)]
    patterns: dict[str, Any] = {
        "drums": {"bars": 2, "rows": rows},
        "bass": {
            "bars": 2,
            "rows": {
                bass: [
                    {"step": s, "len": n, "note": root, "vel": 0.9}
                    for s, n in zip(kicks, lengths, strict=True)
                ]
            },
        },
    }
    if "lead" in names:
        patterns["melody"] = {"bars": 1, "rows": {"lead": []}}
    sections = [
        {"name": part.name, "bars": part.bars, "play": ["bass"] if i == 0 else ["drums", "bass"]}
        for i, part in enumerate(recipe.structure)
    ]
    sounds: dict[str, Any] = {}
    for s in recipe.sounds:
        sound: dict[str, Any] = {"instrument": s.instrument}
        if s.params:
            sound["params"] = dict(s.params)
        if s.library:
            sound["library"] = s.library
        sounds[s.name] = sound
    return {
        "version": 1,
        "bpm": recipe.bpm,
        "swing": recipe.swing,
        "seed": recipe.seed,
        "key": recipe.key,
        "sounds": sounds,
        "patterns": patterns,
        "sections": sections,
        "mix": {"master": [{"effect": "limiter", "params": {"ceiling": -1}}]},
    }


def write_starter(folder: Path, recipe: Recipe) -> Path:
    """Save the recipe, the starter as `score-1.json` and a saved state; return the score."""
    from redline.beat.handoff import mark_saved
    from redline.core.private import write_private

    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    write_private(folder / "recipe.json", recipe_json(recipe))
    score = folder / "score-1.json"
    write_private(score, json.dumps(starter_score(recipe), ensure_ascii=False, indent=1))
    mark_saved(folder, recipe.seed, score)
    return score
