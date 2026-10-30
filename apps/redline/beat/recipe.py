"""The recipe of a new beat, drawn by seed: genre, tempo, key, sounds, structure and devices.

The genre facts come from blogs (research of block b21, section B) and are unverified; the
numbers no blog gave are marked as own choices in `invented`.
"""

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from redline.beat import library

BLOG = "blog, unverified"
OWN = "own choice"
# the score's limits, the same as Rust's
BPM = (40.0, 220.0)
MAX_SEED = 2**32 - 1
TONICS = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MODES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}
EXTRAS = (1, 3)
DEVICE_COUNT = (2, 3)
# how far the score's bars may stray from the drawn structure
BARS_SLACK = 8
MIN_BARS = 8
SAMPLER_KINDS = ("fetch",)


class RecipeError(ValueError):
    """An option the recipe cannot take."""


@dataclass(frozen=True)
class Sound:
    """One sound of the palette: its name in the score, instrument and starting params."""

    name: str
    instrument: str
    params: tuple[tuple[str, str | float], ...] = ()
    library: str | None = None


@dataclass(frozen=True)
class Part:
    """One section of the structure."""

    name: str
    bars: int


@dataclass(frozen=True)
class Genre:
    """What a genre asks of a beat.

    Attributes:
        bpm: The tempo range, whole BPM, as the score counts it (16 steps a bar).
        half_time: The snare lands on beat 3, so the groove feels at half the tempo.
        swing: The swing range, percent of the sixteenth pair.
        sounds: The core palette, always drawn.
        extras: Sounds of which one to three are added.
        hats: How the hats move.
        bass: How the 808 behaves.
        kick: Where the kicks go.
        modes: The modes the key is drawn from.
        structures: Section templates, one drawn.
        hats_per_bar: Hat hits a bar a section should stay inside, rolls counted per hit.
        kicks_per_bar: Kick hits a bar a section should stay inside.
        devices: The devices this genre draws from.
        source: Where the facts come from.
        invented: The fields set by own choice.
    """

    bpm: tuple[int, int]
    half_time: bool
    swing: tuple[float, float]
    sounds: tuple[Sound, ...]
    extras: tuple[Sound, ...]
    hats: str
    bass: str
    kick: str
    modes: tuple[str, ...]
    structures: tuple[tuple[Part, ...], ...]
    hats_per_bar: tuple[float, float]
    kicks_per_bar: tuple[float, float]
    devices: tuple[str, ...]
    source: str
    invented: tuple[str, ...]


@dataclass(frozen=True)
class Device:
    """Something the beat must also do, with where the idea comes from."""

    text: str
    source: str


DEVICES = {
    "drop_808": Device("the 808 drops out for the last bar before each hook (use a fill)", BLOG),
    "hat_rolls": Device(
        "hat rolls (3, 4 or 6 hits in a step) at the end of each 4-bar phrase, not elsewhere",
        BLOG,
    ),
    "filtered_intro": Device(
        "the intro plays the melody through a low-pass: a copy of the melody sound with a "
        "filter rack, played only in the intro",
        OWN,
    ),
    "glide_sparingly": Device(
        "808 glides (overlapping notes) only at phrase ends; constant glide removes contrast",
        BLOG,
    ),
    "open_hat_offbeat": Device("one open hat on an off-beat a bar, choked by the next hat", BLOG),
    "ghost_snares": Device("ghost snares (g) in the verses, never in the hooks", OWN),
    "kick_switch": Device("the kick pattern changes in the second half of each verse", OWN),
    "stop_before_hook": Device(
        "everything but the melody stops in the last bar before the hook", OWN
    ),
    "thin_verse": Device(
        "verses play fewer sounds than hooks: the melody or the hats thinned in verses", BLOG
    ),
    "call_response": Device("the melody plays a phrase in one bar and answers it in the next", OWN),
}

KICK = Sound("kick", "kick")
BASS = Sound("bass", "bass808")
SNARE = Sound("snare", "snare")
CLAP = Sound("clap", "clap")
HAT = Sound("hat", "hat_closed")
OPEN_HAT = Sound("open_hat", "hat_open")
RIM = Sound("rim", "rim")
COWBELL = Sound("cowbell", "cowbell")
BELL = Sound("bell", "fm", (("preset", "bell"),))
KEYS = Sound("keys", "fm", (("preset", "keys"),))
PLUCK = Sound("pluck", "pluck")
PAD = Sound("pad", "saw", (("attack", 0.3), ("release", 1.0)))
LEAD = Sound("lead", "saw", (("voices", 1.0), ("cutoff", 2500.0)))
SAMPLE = Sound("sample", "sampler")


def _parts(*pairs: tuple[str, int]) -> tuple[Part, ...]:
    return tuple(Part(name, bars) for name, bars in pairs)


# intro 4-8, hook 8, verse 16 (blogs)
SONG = _parts(("intro", 8), ("hook", 8), ("verse", 16), ("hook", 8), ("verse", 16), ("hook", 8))
SHORT_INTRO = _parts(
    ("intro", 4), ("verse", 16), ("hook", 8), ("verse", 16), ("hook", 8), ("outro", 4)
)
# the drill template of the blogs, 8/8/16/8/16/8/8/8
DRILL = _parts(
    ("intro", 8),
    ("hook", 8),
    ("verse", 16),
    ("hook", 8),
    ("verse", 16),
    ("hook", 8),
    ("break", 8),
    ("outro", 8),
)
LOOP = _parts(("intro", 4), ("a", 8), ("b", 8), ("a", 8), ("b", 8), ("outro", 4))
ALL_DEVICES = tuple(DEVICES)
NO_ROLLS = tuple(d for d in ALL_DEVICES if d not in ("hat_rolls", "glide_sparingly"))

GENRES: dict[str, Genre] = {
    "trap": Genre(
        bpm=(130, 160),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, SNARE, HAT),
        extras=(OPEN_HAT, CLAP, BELL, PAD, PLUCK, SAMPLE),
        hats="sixteenths or eighths, triplet and 1/32 rolls; one open hat off the beat",
        bass="the 808 is the bass line; keep kick and 808 starts apart",
        kick="sparse kicks; snare (or clap) on beat 3 of the bar",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(8, 40),
        kicks_per_bar=(1, 6),
        devices=ALL_DEVICES,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "uk_drill": Genre(
        bpm=(140, 150),
        half_time=True,
        swing=(50.0, 56.0),
        sounds=(KICK, BASS, SNARE, HAT),
        extras=(RIM, OPEN_HAT, PAD, BELL, SAMPLE),
        hats="eighths with gaps and short bursts, a two-bar pocket",
        bass="808 slides to the root, fifth or octave; constant glide removes contrast",
        kick="two-bar pocket: snare on beat 3 of bar 1, then beat 4 of bar 2",
        modes=("minor", "harmonic_minor"),
        structures=(DRILL,),
        hats_per_bar=(4, 24),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "ny_drill": Genre(
        bpm=(140, 150),
        half_time=True,
        swing=(50.0, 56.0),
        sounds=(KICK, BASS, SNARE, HAT),
        extras=(SAMPLE, PAD, KEYS, OPEN_HAT),
        hats="eighths with gaps and bursts, a two-bar pocket",
        bass="sliding 808 that answers the melody; slides to root, fifth or octave",
        kick="two-bar pocket like UK drill, with more kicks after the snare",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(DRILL,),
        hats_per_bar=(4, 24),
        kicks_per_bar=(1, 6),
        devices=ALL_DEVICES,
        source=BLOG,
        # the research has no facts of its own for it: the drill facts with own differences
        invented=("bass", "kick", "swing", "modes", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "plugg": Genre(
        bpm=(130, 150),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, CLAP, HAT, KEYS),
        extras=(BELL, LEAD, PAD, SAMPLE),
        hats="restrained hats, few rolls",
        bass="melodic 808 that follows the keys",
        kick="sparse kicks, clap on beat 3",
        modes=("major", "minor", "dorian"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(4, 20),
        kicks_per_bar=(1, 4),
        devices=NO_ROLLS,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "boom_bap": Genre(
        bpm=(85, 95),
        half_time=False,
        swing=(54.0, 58.0),
        sounds=(KICK, BASS, SNARE, HAT),
        extras=(SAMPLE, KEYS, RIM, PLUCK),
        hats="straight eighths with swing, a few ghost hits",
        bass="a short bass under the kick; little glide",
        kick="kicks around beat 1 and the 'and' of 2; snare on 2 and 4",
        modes=("minor", "dorian"),
        structures=(SHORT_INTRO, SONG),
        hats_per_bar=(4, 16),
        kicks_per_bar=(2, 6),
        devices=NO_ROLLS,
        source=BLOG,
        invented=("bass", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "phonk": Genre(
        bpm=(130, 150),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, SNARE, HAT, COWBELL),
        extras=(OPEN_HAT, CLAP, SAMPLE, PAD),
        hats="busy hats with rolls",
        bass="distorted gliding 808",
        kick="kicks on the beat, snare or clap on beat 3",
        modes=("phrygian", "minor"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(4, 32),
        kicks_per_bar=(1, 6),
        devices=ALL_DEVICES,
        source=BLOG,
        # the blogs disagree on its tempo
        invented=("bpm", "swing", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "jersey_club": Genre(
        bpm=(130, 140),
        half_time=False,
        swing=(50.0, 52.0),
        sounds=(KICK, BASS, CLAP, HAT),
        extras=(SAMPLE, LEAD, OPEN_HAT, PAD),
        hats="light offbeat hats",
        bass="short 808 hits, little glide",
        kick="kicks on the 16ths 1, 5, 9, 12 and 15 (steps 0, 4, 8, 11, 14)",
        modes=("minor", "major"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(0, 16),
        kicks_per_bar=(4, 10),
        devices=NO_ROLLS,
        source=BLOG,
        invented=("swing", "bass", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "cloud": Genre(
        bpm=(65, 85),
        half_time=False,
        swing=(50.0, 56.0),
        sounds=(KICK, BASS, SNARE, HAT, PAD),
        extras=(BELL, PLUCK, SAMPLE, KEYS),
        hats="soft eighths",
        bass="long 808 notes under the pad",
        kick="sparse kicks, snare on 2 and 4",
        modes=("major", "minor", "lydian", "dorian"),
        structures=(LOOP, SHORT_INTRO),
        hats_per_bar=(2, 16),
        kicks_per_bar=(1, 4),
        devices=NO_ROLLS,
        source=BLOG,
        # the blogs disagree on its tempo; the range kept is one of them
        invented=("swing", "bass", "kick", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "lofi": Genre(
        bpm=(70, 90),
        half_time=False,
        swing=(54.0, 62.0),
        sounds=(KICK, BASS, SNARE, HAT, KEYS),
        extras=(SAMPLE, PLUCK, RIM, PAD),
        hats="swung eighths, soft",
        bass="a soft round bass, little drive",
        kick="kick on 1 and around 3, snare on 2 and 4",
        modes=("major", "dorian", "minor"),
        structures=(LOOP,),
        hats_per_bar=(4, 16),
        kicks_per_bar=(2, 5),
        devices=NO_ROLLS,
        source=BLOG,
        invented=("swing", "bass", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
}


@dataclass(frozen=True)
class Recipe:
    """Everything the beatmaker is told about a beat."""

    seed: int
    genre: str
    bpm: float
    key: str
    swing: float
    half_time: bool
    sounds: tuple[Sound, ...]
    structure: tuple[Part, ...]
    bars: tuple[int, int]
    devices: tuple[str, ...]
    hats_per_bar: tuple[float, float]
    kicks_per_bar: tuple[float, float]


KEY = re.compile(r"([A-G])([#b]?) ([a-z_]+)")


def parse_key(text: str) -> tuple[int, str] | None:
    """The pitch class of the tonic and the mode of a key like `F# minor`; None if not one."""
    found = KEY.fullmatch(text)
    if found is None or found.group(3) not in MODES:
        return None
    shift = {"#": 1, "b": -1, "": 0}[found.group(2)]
    return (LETTERS[found.group(1)] + shift) % 12, found.group(3)


def scale(key: str) -> frozenset[int]:
    """The pitch classes of the key's scale."""
    parsed = parse_key(key)
    if parsed is None:
        raise RecipeError(f"{key!r} is not a key")
    tonic, mode = parsed
    return frozenset((tonic + step) % 12 for step in MODES[mode])


def _samplers() -> tuple[str, ...]:
    return tuple(
        name for name, source in sorted(library.load().items()) if source.kind in SAMPLER_KINDS
    )


def _check(seed: int, genre: str | None, bpm: float | None, key: str | None) -> None:
    if not 0 <= seed <= MAX_SEED:
        raise RecipeError(f"seed {seed} is outside 0..{MAX_SEED}")
    if genre is not None and genre not in GENRES:
        raise RecipeError(f"no genre {genre}; one of {', '.join(GENRES)}")
    if bpm is not None and not BPM[0] <= bpm <= BPM[1]:
        raise RecipeError(f"{bpm:g} BPM is outside {BPM[0]:g}..{BPM[1]:g}")
    if key is not None and parse_key(key) is None:
        raise RecipeError(
            f"{key!r} is not a key: a tonic like C, F# or Bb, a space and one of "
            + ", ".join(MODES)
        )


def _palette(rng: np.random.Generator, spec: Genre) -> tuple[Sound, ...]:
    count = int(rng.integers(EXTRAS[0], EXTRAS[1] + 1))
    picked = sorted(rng.choice(len(spec.extras), size=count, replace=False).tolist())
    samplers = _samplers()
    drawn = int(rng.integers(len(samplers))) if samplers else 0
    out = list(spec.sounds)
    for i in picked:
        sound = spec.extras[i]
        if sound.instrument == "sampler" and samplers:
            sound = Sound(sound.name, sound.instrument, sound.params, samplers[drawn])
        out.append(sound)
    return tuple(out)


def make_recipe(
    seed: int, genre: str | None = None, bpm: float | None = None, key: str | None = None
) -> Recipe:
    """Draw a beat's recipe; the same seed and options draw the same.

    Every draw is taken whatever the options, so an option changes only what it names (and,
    for a genre, what the genre's tables hold).

    Args:
        seed: The draw's seed, 0..2**32-1 (the score's seed is a u32).
        genre: A genre of `GENRES`, or None to draw one.
        bpm: The tempo, or None to draw one inside the genre's range.
        key: A key like `F# minor`, or None to draw one from the genre's modes.

    Raises:
        RecipeError: An option the score format cannot take.
    """
    _check(seed, genre, bpm, key)
    rng = np.random.default_rng(seed)
    names = list(GENRES)
    drawn_genre = names[int(rng.integers(len(names)))]
    name = genre or drawn_genre
    spec = GENRES[name]
    drawn_bpm = int(rng.integers(spec.bpm[0], spec.bpm[1] + 1))
    tonic = TONICS[int(rng.integers(len(TONICS)))]
    mode = spec.modes[int(rng.integers(len(spec.modes)))]
    swing = round(float(rng.uniform(*spec.swing)), 1)
    sounds = _palette(rng, spec)
    structure = spec.structures[int(rng.integers(len(spec.structures)))]
    count = int(rng.integers(DEVICE_COUNT[0], DEVICE_COUNT[1] + 1))
    devices = rng.choice(len(spec.devices), size=count, replace=False).tolist()
    total = sum(part.bars for part in structure)
    return Recipe(
        seed=seed,
        genre=name,
        bpm=float(bpm) if bpm is not None else float(drawn_bpm),
        key=key or f"{tonic} {mode}",
        swing=swing,
        half_time=spec.half_time,
        sounds=sounds,
        structure=structure,
        bars=(max(MIN_BARS, total - BARS_SLACK), total + BARS_SLACK),
        devices=tuple(spec.devices[i] for i in sorted(devices)),
        hats_per_bar=spec.hats_per_bar,
        kicks_per_bar=spec.kicks_per_bar,
    )


def recipe_json(recipe: Recipe) -> str:
    """The recipe as JSON."""
    return json.dumps(asdict(recipe), ensure_ascii=False, indent=1)


def _tuples(value: Any) -> Any:
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


def recipe_from_json(text: str) -> Recipe:
    """The recipe written by `recipe_json`."""
    raw = json.loads(text)
    sounds = tuple(
        Sound(s["name"], s["instrument"], _tuples(s["params"]), s["library"])
        for s in raw.pop("sounds")
    )
    structure = tuple(Part(p["name"], p["bars"]) for p in raw.pop("structure"))
    rest = {k: _tuples(v) for k, v in raw.items()}
    return Recipe(sounds=sounds, structure=structure, **rest)
