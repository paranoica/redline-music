"""The recipe of a new beat, drawn by seed: genre, tempo, key, sounds, structure and moves.

The moves are the devices, the 808's style and the beat's character.

The genre facts come from producer blogs and tutorials and are unverified; the numbers no
source gave are marked as own choices in `invented`.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from redline.beat import library
from redline.beat.pack import PackSound

BLOG = "blog, unverified"
OWN = "own choice"
MEASURED = "measured on the owner's tracks"
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
CHARACTER_COUNT = (1, 2)
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
        leads: The melodic sounds of which one is drawn, named `lead`.
        extras: Sounds of which one to three are added.
        hats: How the hats move.
        bass: How the 808 behaves.
        kick: Where the kicks go.
        modes: The modes the key is drawn from.
        structures: Section templates, one drawn.
        hats_per_bar: Hat hits a bar a section should stay inside, rolls counted per hit.
        kicks_per_bar: Kick hits a bar a section should stay inside.
        devices: The devices this genre draws from.
        bass_styles: The 808 styles of `BASS_STYLES` this genre draws one from.
        character: The moves of `CHARACTER` this genre draws one or two from.
        source: Where the facts come from.
        invented: The fields set by own choice.
        feel: Where the backbeat sits, when neither half time nor 2 and 4 says it.
    """

    bpm: tuple[int, int]
    half_time: bool
    swing: tuple[float, float]
    sounds: tuple[Sound, ...]
    leads: tuple[Sound, ...]
    extras: tuple[Sound, ...]
    hats: str
    bass: str
    kick: str
    modes: tuple[str, ...]
    structures: tuple[tuple[Part, ...], ...]
    hats_per_bar: tuple[float, float]
    kicks_per_bar: tuple[float, float]
    devices: tuple[str, ...]
    bass_styles: tuple[str, ...]
    character: tuple[str, ...]
    source: str
    invented: tuple[str, ...]
    feel: str = ""


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
        BLOG,
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
    "mute_return": Device(
        "the 808 and the hats are muted for one whole bar inside a section, then come back", BLOG
    ),
    "build_by_subtraction": Device(
        "the hook is the full loop; every other section is that loop with parts taken out", BLOG
    ),
}

# Moves against the textbook: each beat draws one or two, and they outrank the general advice.
CHARACTER = {
    "off_grid_kick": Device(
        "one kick a bar sits on an unusual step (3, 6, 7, 11, 13 or 15) and the groove leans on "
        "it; the kick is never only on steps 0 and 8",
        BLOG,
    ),
    "kick_pickup": Device(
        "kicks work as pickups: a kick on step 14 or 15 leads into the next bar, and some phrases "
        "start without a kick on step 0",
        OWN,
    ),
    "snare_shift": Device(
        "the backbeat moves: in a two-bar pocket the second bar's snare lands a beat late (step "
        "12), or a second snare answers the main one off the beat",
        BLOG,
    ),
    "triplet_hats": Device(
        "the hats lean triplet against straight kicks: '3' rolls on the off-beats of beats 2 and "
        "4, or a '6' closing every second beat",
        BLOG,
    ),
    "pitched_rolls": Device(
        "hat rolls bend: their pitch lane rises or falls 3 to 12 semitones across the roll",
        BLOG,
    ),
    "hats_swing_alone": Device(
        "only the hats swing (the hat sound's own swing 58 to 64) while kick and snare stay "
        "straight",
        BLOG,
    ),
    "silent_beat": Device(
        "in the hooks one beat a bar is nearly empty, and whole bars drop out and return "
        "without warning",
        BLOG,
    ),
    "sparse_808": Device(
        "the 808 plays in only half to three quarters of the verse bars and is silent in the "
        "rest, so each return hits",
        MEASURED,
    ),
    "beat_switch": Device(
        "the last third of the beat switches: a new drum pattern and a new 808 rhythm for at "
        "least 8 bars, the snare moved (half time against double time)",
        BLOG,
    ),
    "no_hats_verse": Device(
        "the first half of each verse has no hats at all; they come in at its middle", OWN
    ),
    "counter_melody": Device(
        "the second and later hooks add a counter-melody on another sound that the first hook "
        "lacks",
        OWN,
    ),
    "loop_endings": Device(
        "the loop ends differently in its 4th and 8th bars: the melody's last two notes change "
        "and the drums break",
        BLOG,
    ),
    "velocity_tiers": Device(
        "hats and snares use three velocity tiers (main about 1.0, ghosts 0.5-0.6, the quietest "
        "0.3-0.4), and no two bars repeat the same velocities",
        BLOG,
    ),
    "chopped_lead": Device(
        "the lead is chopped in the hooks: a copy of it with a stutter or trance_gate rack",
        BLOG,
    ),
    "detuned_lead": Device(
        "the lead is detuned on purpose: a drift rack (15 to 30 cents, slow) and a tape_delay",
        BLOG,
    ),
    "bass_answers": Device(
        "the 808 answers the melody: it moves in the melody's gaps and holds while the melody "
        "plays",
        OWN,
    ),
}

# The 808's character: a text for the beatmaker and the bass808 params it starts from.
BASS_STYLES = {
    "clean_long": (
        Device(
            "a clean long 808 (decay 2-4 s) ringing under the melody; glides only into accents, "
            "one or two a bar at most",
            BLOG,
        ),
        (("decay", 3.0), ("drive", 0.15)),
    ),
    "distorted": (
        Device(
            "a distorted 808 that reads on a phone speaker: drive high, grit above the sub, "
            "long notes",
            BLOG,
        ),
        (("decay", 3.0), ("drive", 0.75), ("tone", 3500.0)),
    ),
    "constant_glide": (
        Device(
            "the 808 slides almost all the time: most notes overlap the next, slides of 2 to 12 "
            "semitones and octave drops; it is the melody of the main sections, so the "
            "general advice to glide sparingly does not apply",
            BLOG,
        ),
        (("decay", 4.0), ("drive", 0.5), ("glide_ms", 160.0)),
    ),
    "staccato_slides": (
        Device(
            "short 808 stabs (1 to 3 steps) on plain roots for three bars, slides in the fourth; "
            "staccato against sliding is the contrast",
            BLOG,
        ),
        (("decay", 1.2), ("drive", 0.4), ("glide_ms", 110.0)),
    ),
    "octave_bounce": (
        Device(
            "a clean 808 that jumps an octave on the loop's first note and moves between root "
            "and fifth",
            BLOG,
        ),
        (("decay", 1.8), ("drive", 0.1)),
    ),
    "kick_double": (
        Device("the 808 doubles the kick's rhythm on one held pitch, short notes", BLOG),
        (("decay", 0.6), ("drive", 0.3)),
    ),
    "short_sub": (
        Device("a short round sub (decay under 0.6 s) right after the kick, no glide", BLOG),
        (("decay", 0.5), ("drive", 0.05), ("tone", 600.0)),
    ),
    "blown_out": (
        Device(
            "a blown-out 808 pushed into clipping, gliding into the last note of each bar; the "
            "pitch stays readable",
            BLOG,
        ),
        (("decay", 2.5), ("drive", 1.0), ("tone", 5000.0), ("glide_ms", 100.0)),
    ),
    "lead_808": (
        Device(
            "the 808 is the lead: it carries the melody in its upper octave (C2..G3) with "
            "glides, and the melodic sounds play sparse stabs around it",
            BLOG,
        ),
        (("decay", 3.5), ("drive", 0.8), ("tone", 4000.0), ("glide_ms", 130.0)),
    ),
}

KICK = Sound("kick", "kick")
BASS = Sound("bass", "bass808")
SNARE = Sound("snare", "snare")
CLAP = Sound("clap", "clap")
HAT = Sound("hat", "hat_closed")
OPEN_HAT = Sound("open_hat", "hat_open")
RIM = Sound("rim", "rim")
COWBELL = Sound("cowbell", "cowbell")
PAD = Sound("pad", "saw", (("attack", 0.3), ("release", 1.0)))
SAMPLE = Sound("sample", "sampler")
KEYS = Sound("keys", "fm", (("preset", "keys"),))
TOM = Sound("tom", "tom")
SHAKER = Sound("shaker", "shaker")
CRASH = Sound("crash", "crash")
SNAP = Sound("snap", "snap")
BLOCK = Sound("perc", "perc")
REESE = Sound("reese", "reese")
CHOIR = Sound("choir", "vox", (("vowel", 3.0), ("attack", 0.8), ("release", 2.0)))


def _lead(instrument: str, *params: tuple[str, str | float], library: str | None = None) -> Sound:
    return Sound("lead", instrument, params, library)


# the leads a genre draws one from; the sampled ones are the CC0 instruments of library.json
FM_BELL = _lead("fm", ("preset", "bell"))
FM_KEYS = _lead("fm", ("preset", "keys"))
PLUCK = _lead("pluck")
SAW_LEAD = _lead("saw", ("voices", 1.0), ("cutoff", 2500.0))
SUPERSAW = _lead("saw", ("voices", 9.0), ("detune", 35.0), ("cutoff", 7000.0))
STRINGS = _lead(
    "saw", ("voices", 7.0), ("detune", 18.0), ("cutoff", 2200.0), ("attack", 0.25), ("release", 0.8)
)
BRASS = _lead("saw", ("voices", 3.0), ("detune", 10.0), ("cutoff", 1800.0), ("attack", 0.03))
PIANO = _lead("sampler", library="vsco-upright-piano")
EPIANO = _lead("sampler", library="vcsl-fm-piano")
HARP = _lead("sampler", library="vsco-harp")
PIZZ = _lead("sampler", library="vsco-violin-pizz")
CELLO_PIZZ = _lead("sampler", library="vsco-cello-pizz")
KALIMBA = _lead("sampler", library="vcsl-kalimba")
GLOCK = _lead("sampler", library="vcsl-glock")
VIBES = _lead("sampler", library="vcsl-vibes-hard")
MARIMBA = _lead("sampler", library="vcsl-marimba")
TUBULAR = _lead("sampler", library="vcsl-tubular-bells")
FLUTE = _lead("flute")
VOX_LEAD = _lead("vox", ("vowel", 0.0), ("voices", 3.0))
FM_BRASS = _lead("fm", ("preset", "brass"), ("ratio", 1.0), ("index", 4.0))
FM_GLASS = _lead("fm", ("preset", "glass"), ("ratio", 3.5), ("index", 2.5), ("decay", 1.5))


def _parts(*pairs: tuple[str, int]) -> tuple[Part, ...]:
    return tuple(Part(name, bars) for name, bars in pairs)


SONG = _parts(("intro", 8), ("hook", 8), ("verse", 16), ("hook", 8), ("verse", 16), ("hook", 8))
SHORT_INTRO = _parts(
    ("intro", 4), ("verse", 16), ("hook", 8), ("verse", 16), ("hook", 8), ("outro", 4)
)
TRAP_BRIDGE = _parts(
    ("intro", 8),
    ("verse", 16),
    ("hook", 8),
    ("verse", 16),
    ("hook", 8),
    ("bridge", 8),
    ("hook", 8),
)
DARK = _parts(("intro", 8), ("hook", 16), ("verse", 16), ("hook", 16), ("bridge", 8), ("outro", 8))
DRILL = _parts(
    ("intro", 8),
    ("hook", 8),
    ("verse", 16),
    ("hook", 8),
    ("verse", 16),
    ("bridge", 8),
    ("hook", 8),
    ("outro", 8),
)
RAGE = _parts(("intro", 4), ("hook", 16), ("verse", 16), ("hook", 16), ("verse", 16), ("hook", 16))
PLUGGNB = _parts(("intro", 8), ("build", 16), ("hook", 16), ("verse", 16), ("hook", 16))
MEMPHIS = _parts(
    ("intro", 8), ("verse", 16), ("hook", 8), ("verse", 16), ("break", 8), ("hook", 16)
)
DRIFT = _parts(("intro", 4), ("build", 8), ("drop", 16), ("break", 8), ("drop", 16), ("outro", 8))
DRIFT_SWITCH = _parts(
    ("intro", 8),
    ("drop", 16),
    ("switch", 8),
    ("drop", 16),
    ("switch", 16),
    ("outro", 4),
)
JERSEY = _parts(("intro", 8), ("a", 8), ("b", 8), ("a", 8), ("break", 8), ("b", 8), ("outro", 8))
BOOM_BAP = _parts(
    ("intro", 8), ("verse", 16), ("hook", 8), ("verse", 16), ("hook", 8), ("outro", 4)
)
LOFI = _parts(("intro", 4), ("a", 8), ("break", 4), ("b", 8), ("outro", 4))
LOOP = _parts(("intro", 4), ("a", 8), ("b", 8), ("a", 8), ("b", 8), ("outro", 4))
# the sections a move speaks of; a structure without one does not draw it
NEEDS = {
    "drop_808": ("hook",),
    "stop_before_hook": ("hook",),
    "thin_verse": ("verse", "hook"),
    "ghost_snares": ("verse", "hook"),
    "kick_switch": ("verse",),
    "build_by_subtraction": ("hook",),
    "sparse_808": ("verse",),
    "no_hats_verse": ("verse",),
    "silent_beat": ("hook",),
    "counter_melody": ("hook",),
    "chopped_lead": ("hook",),
}
# a move the drawn 808 style (or, for a device, a drawn character move) contradicts is not
# drawn with it
CLASHES = {
    "glide_sparingly": ("constant_glide", "lead_808", "blown_out", "short_sub", "kick_double"),
    "hat_rolls": ("triplet_hats",),
    "bass_answers": ("kick_double", "lead_808", "constant_glide", "short_sub", "staccato_slides"),
}
ALL_DEVICES = tuple(DEVICES)
NO_ROLLS = tuple(d for d in ALL_DEVICES if d not in ("hat_rolls", "glide_sparingly"))
NO_SPARING_GLIDE = tuple(d for d in ALL_DEVICES if d != "glide_sparingly")
ALL_CHARACTER = tuple(CHARACTER)
CALM_CHARACTER = tuple(
    c for c in ALL_CHARACTER if c not in ("triplet_hats", "pitched_rolls", "chopped_lead")
)

GENRES: dict[str, Genre] = {
    "trap": Genre(
        bpm=(130, 160),
        half_time=True,
        swing=(50.0, 55.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(
            PIANO,
            FM_KEYS,
            PLUCK,
            SAW_LEAD,
            STRINGS,
            HARP,
            PIZZ,
            FM_BELL,
            GLOCK,
            FLUTE,
            FM_GLASS,
        ),
        extras=(OPEN_HAT, CLAP, PAD, RIM, SAMPLE, CRASH, BLOCK),
        hats="eighths or sixteenths; the last two hats of a phrase become a 32nd or triplet "
        "roll with its middle hits softer; one open hat off the beat, choked",
        bass="the 808 follows the melody's roots, mostly within an octave: notes just after "
        "the kicks plus a few in the gaps",
        kick="kick on step 0 and one or two syncopated hits (around steps 6, 10 or 14); snare "
        "or clap on beat 3",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(SONG, SHORT_INTRO, TRAP_BRIDGE),
        hats_per_bar=(8, 40),
        kicks_per_bar=(1, 6),
        devices=ALL_DEVICES,
        bass_styles=("clean_long", "distorted", "constant_glide", "staccato_slides"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "dark_trap": Genre(
        bpm=(130, 148),
        half_time=True,
        swing=(50.0, 53.0),
        sounds=(KICK, BASS, CLAP, HAT),
        leads=(
            FM_BELL,
            TUBULAR,
            GLOCK,
            KALIMBA,
            PIANO,
            STRINGS,
            SAW_LEAD,
            VOX_LEAD,
            FM_GLASS,
            FLUTE,
        ),
        extras=(PAD, OPEN_HAT, SNARE, RIM, SAMPLE, CHOIR, CRASH),
        hats="eighths with the middle hats turned down, the last two of a phrase as a "
        "32nd-triplet roll; hats drop out for the bar before a hook",
        bass="a long 808 on the chord roots, grit above a clean sub; it slides down into the "
        "root at a phrase start",
        kick="sparse kicks; clap (or snare) on beat 3; keep the drums sparse so the space stays",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(DARK, SONG),
        hats_per_bar=(6, 32),
        kicks_per_bar=(1, 4),
        devices=ALL_DEVICES,
        bass_styles=("distorted", "clean_long", "lead_808"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("swing", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "rage": Genre(
        bpm=(150, 170),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, CLAP, HAT),
        leads=(SUPERSAW, SUPERSAW, SAW_LEAD, GLOCK),
        extras=(OPEN_HAT, SNARE, PAD, SAMPLE, REESE, CRASH),
        hats="sparse in some sections and rapid in others; triplet or 32nd rolls only at the end "
        "of a 2- or 4-bar phrase, velocity ramping up",
        bass="a distorted, clipped 808 that follows the chords and glides; a clean sub under a "
        "distorted layer",
        kick="kick on step 0 (a punchy layered kick), clap on beat 3; at least one beat a bar "
        "where almost nothing sounds",
        modes=("minor", "phrygian"),
        structures=(RAGE,),
        hats_per_bar=(4, 40),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        bass_styles=("lead_808", "distorted", "constant_glide"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("structures", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "plugg": Genre(
        bpm=(130, 150),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, CLAP, HAT),
        leads=(FM_BELL, FM_KEYS, VIBES, GLOCK, MARIMBA, SAW_LEAD, FM_GLASS, FLUTE),
        extras=(PAD, KEYS, SAMPLE, SNARE, CRASH, SNAP),
        hats="restrained hats, few rolls; a couple of snares as a counter-rhythm",
        bass="a melodic 808 that follows the keys and rings out; foldback grit optional",
        kick="sparse kicks, clap on beat 3, beat skips",
        modes=("major", "minor", "dorian"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(4, 20),
        kicks_per_bar=(1, 4),
        devices=NO_ROLLS,
        bass_styles=("clean_long", "octave_bounce"),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "pluggnb": Genre(
        bpm=(125, 145),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, CLAP, OPEN_HAT),
        leads=(EPIANO, FM_KEYS, HARP, VIBES, PLUCK, STRINGS, FLUTE, VOX_LEAD),
        extras=(PAD, HAT, SNARE, SAMPLE, SHAKER, SNAP),
        hats="open hats on eighths with 16th and 32nd rolls, no closed hat; a snare with rolls",
        bass="a clean 808 with octave jumps: the root on chords 1 and 3, the third on 2 and 4",
        kick="a bouncy four-bar kick groove; clap on beat 3",
        modes=("major", "dorian", "lydian", "minor"),
        structures=(PLUGGNB, SONG),
        hats_per_bar=(4, 24),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        bass_styles=("octave_bounce", "clean_long"),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("swing", "hats_per_bar", "kicks_per_bar"),
    ),
    "uk_drill": Genre(
        bpm=(138, 146),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(PIANO, PIZZ, CELLO_PIZZ, STRINGS, PLUCK, FM_BELL, VOX_LEAD, FLUTE),
        extras=(RIM, OPEN_HAT, PAD, CLAP, SAMPLE, TOM, SHAKER, CRASH, REESE),
        hats="the 16th grid 'x..x..x.' (two spaces, two spaces, one) with a ghost hat, holes "
        "and short bursts; a 32nd roll into beat 4",
        bass="sliding 808: plain roots for three bars, slides in the fourth, to the root, fifth "
        "or octave",
        kick="two-bar pocket: kick 'x.............x.' then 'x..x..x.......x.'; snare on beat 3 "
        "of bar 1 and beat 4 (step 12) of bar 2",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(DRILL,),
        hats_per_bar=(4, 24),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        bass_styles=("staccato_slides", "constant_glide", "distorted"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("swing", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "ny_drill": Genre(
        bpm=(140, 148),
        half_time=True,
        swing=(50.0, 54.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(PIANO, STRINGS, SAW_LEAD, PIZZ, FM_BELL, FLUTE, FM_BRASS),
        extras=(SAMPLE, PAD, OPEN_HAT, CLAP, TOM, CRASH),
        hats="a bouncy pattern with holes, triplet rolls with velocity changes, sparse open hats",
        bass="sliding 808 that answers the melody, a slide on every 4th or 8th note, short decay",
        kick="the drill two-bar pocket with more kicks after the snare; a kick just before an 808 "
        "note, never on its start",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(DRILL,),
        hats_per_bar=(4, 24),
        kicks_per_bar=(1, 6),
        devices=NO_SPARING_GLIDE,
        bass_styles=("staccato_slides", "constant_glide"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("swing", "hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "chicago_drill": Genre(
        bpm=(135, 150),
        half_time=True,
        swing=(50.0, 55.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(BRASS, FM_BRASS, FM_BELL, TUBULAR, PIANO, VOX_LEAD),
        extras=(OPEN_HAT, CLAP, PAD, RIM, CRASH, TOM),
        hats="skippy triplet hats with deleted hits and bursts at phrase ends; open hats rare",
        bass="a heavy 808 with less sliding than UK drill, harmonics kept audible",
        kick="sparse syncopated kicks; snare on beat 3 with busy snare fills",
        modes=("minor", "harmonic_minor", "phrygian"),
        structures=(SONG, SHORT_INTRO),
        hats_per_bar=(6, 32),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        bass_styles=("distorted", "clean_long"),
        character=ALL_CHARACTER,
        source=BLOG,
        invented=("hats_per_bar", "kicks_per_bar", "extras"),
    ),
    "jersey_club": Genre(
        bpm=(130, 145),
        half_time=False,
        swing=(50.0, 52.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(SAW_LEAD, PLUCK, FM_KEYS, MARIMBA),
        extras=(SAMPLE, OPEN_HAT, PAD, CLAP, CRASH, SNAP, BLOCK),
        hats="closed hats on beats 2 and 4 only; busy hats turn it to mud",
        bass="the 808 doubles the kick rhythm on a held pitch",
        kick="five kicks a bar on steps 0, 4, 8, 11, 14, the 4th and 5th nudged and softer; "
        "snare on steps 8 and 11 (and 10 in alternate bars)",
        modes=("minor", "major"),
        structures=(JERSEY,),
        hats_per_bar=(0, 8),
        kicks_per_bar=(4, 8),
        devices=NO_ROLLS,
        bass_styles=("kick_double",),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "structures"),
        feel="full time: the snare follows the kick bounce on steps 8 and 11",
    ),
    "memphis": Genre(
        bpm=(80, 100),
        half_time=False,
        swing=(54.0, 60.0),
        sounds=(KICK, BASS, SNARE, HAT, COWBELL),
        leads=(FM_BELL, PIANO, STRINGS, TUBULAR, VOX_LEAD, FM_BRASS),
        extras=(OPEN_HAT, RIM, CLAP, PAD, SAMPLE, CHOIR),
        hats="simple swung eighths, one open hat accent, a 32nd or triplet burst into the snare",
        bass="an 808 note right after most kicks, mostly the root, octave glides, distorted",
        kick="kick on step 0 with one or two syncopated hits; snare on 2 and 4, the clap a few ms "
        "late",
        modes=("minor", "phrygian"),
        structures=(MEMPHIS,),
        hats_per_bar=(4, 20),
        kicks_per_bar=(1, 5),
        devices=ALL_DEVICES,
        bass_styles=("blown_out", "distorted"),
        character=ALL_CHARACTER,
        source=BLOG,
        # the blogs disagree on its tempo; the range kept is one tutorial's
        invented=("bpm", "hats_per_bar", "kicks_per_bar"),
    ),
    "phonk": Genre(
        bpm=(130, 160),
        half_time=True,
        swing=(50.0, 52.0),
        sounds=(KICK, BASS, CLAP, HAT, COWBELL),
        leads=(SAW_LEAD, STRINGS, PIANO, FM_BELL, VOX_LEAD),
        extras=(OPEN_HAT, SNARE, PAD, SAMPLE, REESE, CHOIR),
        hats="the cowbell carries the melody: a 2-4 note motif on eighths or sixteenths (its "
        "pitch lane), the last note an octave up; hats on straight eighths with 16th, 32nd or "
        "triplet bursts at the end of a four-bar phrase",
        bass="a blown-out 808 under a clean sub, gliding on the last note of a bar; the line holds "
        "the root and touches the flat second",
        kick="sparse kicks, clap on beat 3; a later drop may go four on the floor",
        modes=("phrygian", "minor"),
        structures=(DRIFT, DRIFT_SWITCH),
        hats_per_bar=(4, 32),
        kicks_per_bar=(1, 6),
        devices=ALL_DEVICES,
        bass_styles=("blown_out", "constant_glide"),
        character=ALL_CHARACTER,
        source=BLOG,
        # the blogs disagree on its tempo; the range kept counts at full time
        invented=("bpm", "hats_per_bar", "kicks_per_bar"),
    ),
    "cloud": Genre(
        bpm=(65, 85),
        half_time=False,
        swing=(50.0, 56.0),
        sounds=(KICK, BASS, SNARE, HAT, PAD),
        leads=(STRINGS, PIANO, HARP, FM_KEYS, VIBES, GLOCK, FLUTE, VOX_LEAD, FM_GLASS),
        extras=(SAMPLE, RIM, OPEN_HAT, CHOIR, SHAKER),
        hats="simple soft eighths with an occasional roll or triplet",
        bass="a soft 808 or sine sub, understated",
        kick="a soft deep kick, snare or clap on 2 and 4",
        modes=("major", "minor", "lydian", "dorian"),
        structures=(LOOP, SHORT_INTRO),
        hats_per_bar=(2, 16),
        kicks_per_bar=(1, 4),
        devices=NO_ROLLS,
        bass_styles=("clean_long", "short_sub"),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("swing", "modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "lofi": Genre(
        bpm=(70, 90),
        half_time=False,
        swing=(54.0, 62.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(EPIANO, PIANO, FM_KEYS, VIBES, HARP, PLUCK, FLUTE),
        extras=(SAMPLE, RIM, PAD, SHAKER, SNAP),
        hats="swung eighths alternating soft and medium (about 0.4 and 0.7)",
        bass="a soft round bass in C1..G2, an octave jump every 4 bars",
        kick="kick on 1 and 3 with a ghost kick on the 'and' of 4; snare (or rim) on 2 and 4",
        modes=("major", "dorian", "minor"),
        structures=(LOFI, LOOP),
        hats_per_bar=(4, 16),
        kicks_per_bar=(2, 5),
        devices=NO_ROLLS,
        bass_styles=("short_sub", "clean_long"),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("modes", "hats_per_bar", "kicks_per_bar"),
    ),
    "boom_bap": Genre(
        bpm=(85, 95),
        half_time=False,
        swing=(54.0, 64.0),
        sounds=(KICK, BASS, SNARE, HAT),
        leads=(PIANO, EPIANO, VIBES, CELLO_PIZZ, HARP, FM_KEYS, FLUTE, FM_BRASS),
        extras=(SAMPLE, RIM, OPEN_HAT, SHAKER, BLOCK),
        hats="straight eighths with swing, an open hat on the 'and' of 2 or 4",
        bass="a short bass right after the kick in C1..C2, no glide",
        kick="kicks on beat 1, the 'and' of 2, sometimes beat 3, a ghost on the 'and' of 4; "
        "snare on 2 and 4, in the verses with ghost snares (0.5-0.6) on the 16ths before them",
        modes=("minor", "dorian"),
        structures=(BOOM_BAP, SHORT_INTRO),
        hats_per_bar=(4, 16),
        kicks_per_bar=(2, 6),
        devices=NO_ROLLS,
        bass_styles=("short_sub",),
        character=CALM_CHARACTER,
        source=BLOG,
        invented=("modes", "hats_per_bar", "kicks_per_bar"),
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
    # recipes saved before the 808 styles, the character moves and the kits read back without
    bass_style: str = ""
    character: tuple[str, ...] = ()
    # "pack" when drums or the 808 come from the owner's pack, else ""
    kit: str = ""


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
    # always one draw from a fixed range, so the manifest's libraries never shift later draws
    draw = int(rng.integers(1 << 30))
    drawn = draw % len(samplers) if samplers else 0
    out = list(spec.sounds)
    for i in picked:
        sound = spec.extras[i]
        if sound.instrument == "sampler" and samplers:
            sound = Sound(sound.name, sound.instrument, sound.params, samplers[drawn])
        out.append(sound)
    return tuple(out)


def make_recipe(
    seed: int,
    genre: str | None = None,
    bpm: float | None = None,
    key: str | None = None,
    pack: dict[str, list[PackSound]] | None = None,
) -> Recipe:
    """Draw a beat's recipe; the same seed and options draw the same.

    Every draw is taken whatever the options, so an option changes only what it names (and,
    for a genre, what the genre's tables hold).

    Args:
        seed: The draw's seed, 0..2**32-1 (the score's seed is a u32).
        genre: A genre of `GENRES`, or None to draw one.
        bpm: The tempo, or None to draw one inside the genre's range.
        key: A key like `F# minor`, or None to draw one from the genre's modes.
        pack: The owner's pack sounds by category (see `pack_kit`), or None for a synthesised
            kit. Its sounds replace the drums and the 808 they have a category for.

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
    sections = {part.name for part in structure}
    lead = spec.leads[int(rng.integers(len(spec.leads)))]
    style = spec.bass_styles[int(rng.integers(len(spec.bass_styles)))]
    character = _draw(rng, _fitting(spec.character, sections, (style,)), CHARACTER_COUNT)
    drawn = (style, *character)
    devices = _draw(rng, _fitting(spec.devices, sections, drawn), DEVICE_COUNT)
    total = sum(part.bars for part in structure)
    sounds = _with_lead(_styled(sounds, style), lead, len(spec.sounds))
    kit = ""
    if pack:
        sounds = _from_pack(rng, sounds, pack, name)
        kit = "pack" if any(library.is_pack(s.library or "") for s in sounds) else ""
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
        devices=devices,
        hats_per_bar=spec.hats_per_bar,
        kicks_per_bar=spec.kicks_per_bar,
        bass_style=style,
        character=character,
        kit=kit,
    )


# the pack categories a synthesised instrument's sound may come from instead
PACK_FOR = {
    "kick": ("kick",),
    "snare": ("snare",),
    "clap": ("clap",),
    "hat_closed": ("hat_closed",),
    "hat_open": ("hat_open",),
    "rim": ("perc",),
    "perc": ("perc",),
    "tom": ("perc",),
    "shaker": ("perc",),
    "snap": ("perc",),
    "cowbell": ("perc",),
    "crash": ("crash",),
    "bass808": ("808",),
}


def pack_kit(sounds: Iterable[PackSound]) -> dict[str, list[PackSound]]:
    """The pack's one-shots by category, each list in id order; an 808 needs a known root."""
    kit: dict[str, list[PackSound]] = {}
    for s in sorted(sounds, key=lambda s: s.id):
        if s.category == "808" and s.note is None:
            continue
        kit.setdefault(s.category, []).append(s)
    return kit


def _from_pack(
    rng: np.random.Generator,
    sounds: tuple[Sound, ...],
    kit: dict[str, list[PackSound]],
    genre: str,
) -> tuple[Sound, ...]:
    """The sounds with each drum and the 808 drawn from the pack where it has the category.

    Sounds whose path names the genre are drawn first; one draw per sound whatever the pack
    holds, so a different pack changes only the pack's choices.
    """
    words = set(genre.split("_"))
    out = []
    for sound in sounds:
        draw = int(rng.integers(1 << 30))
        pool = [s for c in PACK_FOR.get(sound.instrument, ()) for s in kit.get(c, [])]
        if not pool:
            out.append(sound)
            continue
        named = [s for s in pool if words & set(re.split(r"[^a-z0-9]+", s.path.lower()))]
        chosen = (named or pool)[draw % len(named or pool)]
        instrument = "sample808" if sound.instrument == "bass808" else "oneshot"
        out.append(Sound(sound.name, instrument, (), f"{library.PACK_PREFIX}{chosen.id}"))
    return tuple(out)


def _fitting(moves: tuple[str, ...], sections: set[str], drawn: tuple[str, ...]) -> tuple[str, ...]:
    """The moves the structure has sections for and nothing drawn contradicts."""
    return tuple(
        m
        for m in moves
        if set(NEEDS.get(m, ())) <= sections and not set(CLASHES.get(m, ())) & set(drawn)
    )


def _draw(
    rng: np.random.Generator, pool: tuple[str, ...], count: tuple[int, int]
) -> tuple[str, ...]:
    """Between `count` moves of the pool, in the pool's order; fewer when the pool is short."""
    want = int(rng.integers(count[0], count[1] + 1))
    picked = rng.choice(len(pool), size=min(want, len(pool)), replace=False).tolist()
    return tuple(pool[i] for i in sorted(picked))


def _styled(sounds: tuple[Sound, ...], style: str) -> tuple[Sound, ...]:
    params = BASS_STYLES[style][1]
    return tuple(replace(s, params=params) if s.instrument == "bass808" else s for s in sounds)


def _with_lead(sounds: tuple[Sound, ...], lead: Sound, core: int) -> tuple[Sound, ...]:
    return (*sounds[:core], lead, *sounds[core:])


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
