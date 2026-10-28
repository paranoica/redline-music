"""The recipe of a new text, drawn from a style profile by seed."""

import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

SCHEMES = ("AABB", "ABAB", "ABBA", "AAAA")
BANDS = ("short", "middle", "long")
MIN_BAND = 3
WORDS = 25
LATIN = 7
ADLIBS = 4
FALLBACK_LINES = 4
# about 3000 characters, which Suno sings without rushing
MAX_LINES = 48
# a lone section or a short snippet is a demo, not a track
MIN_SECTIONS = 2
MIN_LINES = 20
FALLBACK_SYLLABLES = (8, 12)
# a drawn topic is a few anchor words that set the vibe; the scene tells no stories
ANCHORS = (
    "кэш",
    "лёд",
    "аптека",
    "дроп",
    "ночь",
    "клуб",
    "район",
    "дым",
    "сука",
    "трасса",
    "отель",
    "свэг",
    "блок",
    "дрип",
    "колёса",
    "кровь",
    "снег",
    "бит",
    "фейк",
    "пачка",
    "капсула",
    "шмот",
    "газ",
    "сон",
)
ANCHOR_COUNT = (2, 3)
# what a text does besides rhyming: the writer gets a few of these, so texts differ in kind
DEVICES = {
    "punch": "a punchline on a double meaning of one word",
    "absurd": "an absurd, deadpan line that is funny because it is said straight",
    "brand": "a brand with a detail: the model, the price, where it came from",
    "pharma": "a reference to pills or a drug in the scene's slang, as an image, never a how-to",
    "english": "a whole phrase in English in the middle of a Russian line",
    "number": "an exact number: money, hours, kilometres, a year",
    "place": "a real, ordinary place set against the flex (a small town, a shop, a stop)",
    "dialogue": "a line said by someone else, with a dash",
    "shout": "a line shouted in capitals, short",
    "internet": "game or internet slang used straight, not as a joke about the internet",
    "contrast": "a cold boast and a weak spot in the same line",
    "sound": "a line built for its sound: repeated syllables, chained inner rhymes, alliteration",
}
DEVICE_COUNT = (3, 5)


@dataclass(frozen=True)
class SectionPlan:
    """One section: its kind, how many sung lines, their syllable range and rhyme scheme."""

    kind: str
    lines: int
    syllables: tuple[int, int]
    scheme: str


@dataclass(frozen=True)
class Recipe:
    """Everything the writer is told about a text.

    Nothing from the base goes in but numbers and single words.
    """

    seed: int
    topic: str
    band: str
    sections: tuple[SectionPlan, ...]
    words: tuple[str, ...]
    latin: tuple[str, ...]
    adlibs: tuple[str, ...]
    adlib_share: float
    hook_repeat_share: float
    en_share: float
    devices: tuple[tuple[str, int], ...] = ()


def _draw(rng: np.random.Generator, items: list[Any], weights: list[float]) -> Any:
    shares = np.asarray(weights, dtype=float)
    return items[int(rng.choice(len(items), p=shares / shares.sum()))]


def _sample(rng: np.random.Generator, rows: list[tuple[str, float]], n: int) -> tuple[str, ...]:
    rows = [(w, s) for w, s in rows if s > 0]
    if not rows:
        return ()
    shares = np.asarray([s for _, s in rows], dtype=float)
    picked = rng.choice(len(rows), size=min(n, len(rows)), replace=False, p=shares / shares.sum())
    return tuple(rows[int(i)][0] for i in sorted(picked))


def _band_range(known: dict[str, Any] | None, band: str) -> tuple[int, int]:
    if not known or known.get("p10") is None or known.get("p90") is None:
        return FALLBACK_SYLLABLES
    low, high = int(known["p10"]), int(known["p90"])
    edges = np.linspace(low, high, len(BANDS) + 1)
    i = BANDS.index(band)
    start, end = round(edges[i]), round(edges[i + 1])
    # a band narrower than MIN_BAND syllables would leave no room for a living line
    while end - start < MIN_BAND and (start > low or end < high):
        start, end = max(low, start - 1), min(high, end + 1)
    return start, end


def _lines(rng: np.random.Generator, known: dict[str, Any] | None) -> int:
    hist = (known or {}).get("hist") or {}
    counts = [int(k) for k in hist if int(k) > 0]
    if not counts:
        return FALLBACK_LINES
    return int(_draw(rng, counts, [hist[str(c)] for c in counts]))


def _scheme(rng: np.random.Generator, known: dict[str, Any] | None) -> str:
    shares = ((known or {}).get("schemes")) or {}
    named = [(s, shares.get(s, 0.0)) for s in SCHEMES]
    if not any(v > 0 for _, v in named):
        return "AABB"
    return str(_draw(rng, [s for s, _ in named], [v for _, v in named]))


def _anchors(rng: np.random.Generator) -> str:
    count = int(rng.integers(ANCHOR_COUNT[0], ANCHOR_COUNT[1] + 1))
    return ", ".join(ANCHORS[int(i)] for i in rng.choice(len(ANCHORS), size=count, replace=False))


def make_recipe(profile: dict[str, Any], seed: int, topic: str | None = None) -> Recipe:
    """Draw a text's shape and material from the profile; the same seed draws the same."""
    rng = np.random.Generator(np.random.PCG64(seed))
    patterns = profile["structure"]["patterns"]
    # a song has a verse or a hook; shapes of skits and intros alone are not songs
    tracks = [
        (p, s)
        for p, s in patterns
        if len(p.split("-")) >= MIN_SECTIONS and {"verse", "hook"} & set(p.split("-"))
    ] or patterns
    shape = str(_draw(rng, [p for p, _ in tracks], [s for _, s in tracks])).split("-")
    band = str(_draw(rng, list(BANDS), [1.0] * len(BANDS)))
    plans: dict[str, tuple[int, str]] = {}
    sections = []
    for kind in shape:
        # a hook comes back the same each time, other kinds are drawn anew
        if kind != "hook" or kind not in plans:
            plans[kind] = (
                _lines(rng, profile["lines_per_section"].get(kind)),
                _scheme(rng, profile["rhyme"].get(kind)),
            )
        lines, scheme = plans[kind]
        syllables = _band_range(profile["line_syllables"].get(kind), band)
        sections.append(SectionPlan(kind, lines, syllables, scheme))
    # verses grow first, then other sections; hooks only all together, so they stay equal
    hooks = [i for i, p in enumerate(sections) if p.kind == "hook"]
    groups = [[i] for i, p in enumerate(sections) if p.kind == "verse"]
    groups = groups or [[i] for i, p in enumerate(sections) if p.kind != "hook"] or [hooks]
    turn = 0
    while sum(p.lines for p in sections) < MIN_LINES:
        for i in groups[turn % len(groups)]:
            p = sections[i]
            sections[i] = SectionPlan(p.kind, p.lines + 1, p.syllables, p.scheme)
        turn += 1
    while sum(s.lines for s in sections) > MAX_LINES and max(s.lines for s in sections) > 1:
        longest = max(range(len(sections)), key=lambda i: (sections[i].lines, -i))
        s = sections[longest]
        sections = [
            (
                SectionPlan(p.kind, p.lines - 1, p.syllables, p.scheme)
                if p.kind == s.kind and p.lines == s.lines and (p.kind == "hook" or i == longest)
                else p
            )
            for i, p in enumerate(sections)
        ]
    vocabulary = [(w["word"], w["per_1000"]) for w in profile["vocabulary"]["top"]]
    latin = [(w["word"], w["per_1000"]) for w in profile["latin"]["top"]]
    names = sorted(DEVICES)
    count = int(rng.integers(DEVICE_COUNT[0], DEVICE_COUNT[1] + 1))
    picked = sorted(rng.choice(len(names), size=count, replace=False).tolist())
    devices = tuple((names[i], int(rng.integers(1, 3))) for i in picked)
    return Recipe(
        seed=seed,
        topic=topic or _anchors(rng),
        band=band,
        sections=tuple(sections),
        words=_sample(rng, vocabulary, WORDS),
        latin=_sample(rng, latin, LATIN),
        adlibs=_sample(rng, [(w, s) for w, s in profile["adlibs"]["top"]], ADLIBS),
        adlib_share=float(profile["adlibs"]["line_share"] or 0.0),
        hook_repeat_share=float(profile["hook"]["repeated_line_share"] or 0.0),
        en_share=float(profile["latin"]["en_share"] or 0.0),
        devices=devices,
    )


def recipe_json(recipe: Recipe) -> str:
    """Byte-stable JSON of a recipe."""
    return json.dumps(asdict(recipe), ensure_ascii=False, sort_keys=True, indent=1)


def recipe_from_json(text: str) -> Recipe:
    """A recipe read back from its JSON."""
    data = json.loads(text)
    data["sections"] = tuple(
        SectionPlan(s["kind"], s["lines"], tuple(s["syllables"]), s["scheme"])
        for s in data["sections"]
    )
    for key in ("words", "latin", "adlibs"):
        data[key] = tuple(data[key])
    data["devices"] = tuple((name, count) for name, count in data.get("devices", ()))
    return Recipe(**data)
