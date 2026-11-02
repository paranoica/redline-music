"""The owner's sample pack- a catalogue of the files dropped into `input/sounds/`.

Each file is sorted into a category (kick, 808, snare, hat, loop, ...) by the words of its
path first and by its sound when the words say nothing, and gets the MIDI root of a pitched
one-shot and the tempo and key of a loop when its name or its sound gives them. The catalogue
lives under `work/` and is never committed: the paths are the owner's.
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from redline.core.private import write_private

PACK = Path("input/sounds")
CATALOG = Path("work/sounds/pack/catalog.json")
FORMAT = 1
AUDIO = (".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3")
PREFIX = "pack-"
# a sound longer than this with no word for it is taken for a loop
ONESHOT_MAX_S = 2.5
# the longest recording a kit plays or the page and the render decode
ZONE_MAX_S = 30.0
# what is read of a file to judge its sound
ANALYSE_S = 4.0
CATEGORIES = (
    "kick",
    "808",
    "snare",
    "clap",
    "hat_closed",
    "hat_open",
    "crash",
    "perc",
    "fx",
    "vox",
    "melodic",
    "loop",
)
# first match wins: a kick named after the 808 ("808 Kick", "Sub Kick") is still a kick
WORDS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(pattern))
    for category, pattern in (
        ("loop", r"\bloops?\b|\b\d{2,3} ?bpm\b|\bbpm ?\d{2,3}\b"),
        ("kick", r"\bkicks?\b|\bkck\b|\bbd\b|\bbass ?drum"),
        ("808", r"808|\bsub ?bass\b|\bsubs?\b|\bbass\b"),
        ("hat_open", r"\bopen ?(hi ?-? ?)?hats?\b|\boh\b|\bohh\b|\bopenhat"),
        ("hat_closed", r"\b(hi ?-? ?)?hats?\b|\bhh\b|\bchh?\b|\bclosed\b|\bhihats?\b"),
        ("clap", r"\bclaps?\b|\bclp\b"),
        ("snare", r"\bsnares?\b|\bsnr\b|\bsd\b|\brims?\b|\brimshot"),
        ("crash", r"\bcrash|\bcymbals?\b|\brides?\b|\bsplash"),
        (
            "perc",
            r"\bperc|\bshakers?\b|\btamb|\bconga|\bbongo|\btoms?\b|\bcowbell|\bwood|\bblock"
            r"|\bsnaps?\b|\bclick",
        ),
        ("fx", r"\bfx\b|\bsfx\b|\briser|\bsweep|\bimpact|\blifter|\bwhoosh|\bnoise|\btransition"),
        ("vox", r"\bvox\b|\bvocals?\b|\bchants?\b|\badlibs?\b|\bvoices?\b|\bshouts?\b"),
        (
            "melodic",
            r"\bbells?\b|\bpiano|\bkeys\b|\bplucks?\b|\bsynths?\b|\bleads?\b|\bguitar|\bflute"
            r"|\bstrings?\b|\bpads?\b|\bchoir|\bbrass|\bmelod|\bchords?\b|\bmallet|\bbox\b",
        ),
    )
)
NOTE = re.compile(r"(?<![A-Za-z0-9])([A-G])([#b]|sharp|flat)?(-?\d)?(?![A-Za-z])")
KEY = re.compile(r"(?<![A-Za-z0-9])([A-G])([#b]|sharp|flat)? ?(minor|min|maj|major|m)?(?![A-Za-z])")
BPM = re.compile(r"(?<!\d)(\d{2,3}) ?bpm|bpm ?(\d{2,3})(?!\d)")
LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP = {"#": 1, "sharp": 1, "b": -1, "flat": -1}


class PackError(Exception):
    """The pack or its catalogue cannot be read."""


@dataclass(frozen=True)
class PackSound:
    """One file of the pack.

    Attributes:
        id: A short hash of its path, stable while the file stays where it is.
        path: Its path under the pack folder, with forward slashes.
        category: One of `CATEGORIES`.
        by: ``name`` when its words decided the category, ``sound`` when the audio did.
        note: The MIDI root of a pitched one-shot (808, melodic), else None.
        bpm: A loop's tempo from its name, else None.
        key: A loop's key from its name, like ``F# minor``, else None.
        duration_s: Its length.
        peak_db: Its sample peak in dBFS.
        size: Its size in bytes, to see a changed file.
        mtime_ns: Its modification time, to see a changed file.
    """

    id: str
    path: str
    category: str
    by: str
    note: int | None
    bpm: float | None
    key: str | None
    duration_s: float
    peak_db: float
    size: int
    mtime_ns: int


def sound_id(relative: str) -> str:
    """The id of a pack file by its path under the pack folder."""
    return hashlib.sha1(relative.encode("utf-8")).hexdigest()[:10]


def _words(relative: str) -> str:
    """The path's words, lower case: separators, digits glued to letters and camel case split."""
    text = Path(relative).with_suffix("").as_posix()
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[_/\\\-\.\(\)\[\]{},+&]+", " ", text)
    return " ".join(text.lower().split())


def category_by_name(relative: str) -> str | None:
    """The category the file's own name gives, else its folders', else None.

    The file name wins over its folders, so a kick in a folder named "808 kit" stays a kick.
    """
    path = Path(relative)
    for part in (path.name, *reversed(path.parent.parts)):
        words = _words(part)
        for category, pattern in WORDS:
            if pattern.search(words):
                return category
    return None


def note_by_name(name: str) -> tuple[int, int | None] | None:
    """The pitch class (0..11) and octave a file name gives, like ``Bell C4``; else None.

    The octave is None when the name has none, as in ``808 Boom (F#).wav``. The last note
    named wins.
    """
    stem = Path(name).stem
    found = None
    for match in NOTE.finditer(stem):
        found = match
    if found is None:
        return None
    letter, accidental, octave = found.groups()
    return (LETTERS[letter] + SHARP.get(accidental or "", 0)) % 12, (
        int(octave) if octave is not None else None
    )


def root_of(named: tuple[int, int | None] | None, heard: int | None, fallback: int) -> int | None:
    """The sample's MIDI root, which the sampler shifts every note from.

    The name's note in the octave it names; else in the octave nearest to what is heard; with
    no name, what is heard; with neither octave, the name's note in the fallback octave. Of
    two octaves as near, the lower.
    """
    if named is None:
        return heard
    pitch, octave = named
    if octave is not None:
        return 12 * (octave + 1) + pitch
    near = heard if heard is not None else 12 * (fallback + 1) + pitch
    return min(range(pitch, 128, 12), key=lambda n: (abs(n - near), n))


def key_by_name(name: str) -> str | None:
    """A loop's key from its name, like ``F# minor``, when it names a mode; else None."""
    stem = Path(name).stem
    for match in KEY.finditer(stem):
        letter, accidental, mode = match.groups()
        if mode is None:
            continue
        sign = {1: "#", -1: "b"}.get(SHARP.get(accidental or "", 0), "")
        minor = mode in ("m", "min", "minor")
        return f"{letter}{sign} {'minor' if minor else 'major'}"
    return None


def bpm_by_name(name: str) -> float | None:
    """A loop's tempo from its name, inside 40..220; else None."""
    match = BPM.search(Path(name).stem.lower())
    if match is None:
        return None
    value = float(match.group(1) or match.group(2))
    return value if 40 <= value <= 220 else None


def _mono(path: Path) -> tuple[np.ndarray, int, float]:
    """The first seconds of a file as mono float, its rate and its whole length."""
    try:
        info = sf.info(str(path))
        frames = min(info.frames, int(ANALYSE_S * info.samplerate)) if info.frames > 0 else -1
        data, rate = sf.read(str(path), frames=frames, dtype="float32", always_2d=True)
    except (sf.LibsndfileError, RuntimeError, OSError) as error:
        raise PackError(f"{path}: cannot read: {error}") from error
    duration = info.frames / info.samplerate if info.frames > 0 else len(data) / rate
    return data.mean(axis=1), int(rate), float(duration)


def _band_share(x: np.ndarray, rate: int, low: float, high: float) -> float:
    spectrum = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    total = float(spectrum.sum()) or 1.0
    return float(spectrum[(freqs >= low) & (freqs < high)].sum()) / total


def _pitch(x: np.ndarray, rate: int) -> int | None:
    """The MIDI note a pitched sound holds, from the median of its steady frames."""
    import librosa

    head = x[: int(rate * 1.0)]
    if len(head) < rate // 10:
        return None
    f0 = librosa.yin(head, fmin=25.0, fmax=2000.0, sr=rate, frame_length=4096)
    f0 = f0[np.isfinite(f0)]
    # skip the attack, where a punchy 808 sweeps from above
    f0 = f0[len(f0) // 4 :] if len(f0) > 8 else f0
    if len(f0) == 0:
        return None
    note = 69 + 12 * float(np.log2(float(np.median(f0)) / 440.0))
    return round(note) if 0 <= note <= 127 else None


def category_by_sound(x: np.ndarray, rate: int, duration_s: float) -> str:
    """A best guess from the audio alone, for a file whose words say nothing."""
    if duration_s > ONESHOT_MAX_S:
        return "loop"
    if len(x) < 64 or float(np.max(np.abs(x))) < 1e-4:
        return "fx"
    low = _band_share(x, rate, 20.0, 150.0)
    high = _band_share(x, rate, 5000.0, rate / 2)
    if low > 0.6:
        return "808" if duration_s > 0.6 else "kick"
    if high > 0.5:
        return "hat_open" if duration_s > 0.4 else "hat_closed"
    if high > 0.25:
        return "snare"
    return "perc"


def describe(root: Path, path: Path) -> PackSound:
    """Catalogue one file of the pack."""
    relative = path.relative_to(root).as_posix()
    stat = path.stat()
    x, rate, duration = _mono(path)
    named = category_by_name(relative)
    category = named or category_by_sound(x, rate, duration)
    if category not in ("loop", "fx") and duration > 2 * ONESHOT_MAX_S and named is None:
        category = "loop"
    note = None
    if category in ("808", "melodic") and duration <= 2 * ONESHOT_MAX_S:
        named = note_by_name(path.name)
        heard = None if named is not None and named[1] is not None else _pitch(x, rate)
        note = root_of(named, heard, 1 if category == "808" else 4)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    return PackSound(
        id=sound_id(relative),
        path=relative,
        category=category,
        by="name" if named else "sound",
        note=note,
        bpm=bpm_by_name(path.name) if category == "loop" else None,
        key=key_by_name(path.name) if category == "loop" else None,
        duration_s=round(duration, 3),
        peak_db=round(20 * np.log10(max(peak, 1e-9)), 1),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(p.startswith(".") or p == "__MACOSX" for p in parts):
            continue
        # a link could point anywhere on the machine
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in AUDIO:
            out.append(path)
    return out


def load(catalog: Path = CATALOG) -> dict[str, PackSound]:
    """The catalogue by id; empty when there is none yet."""
    if not catalog.is_file():
        return {}
    try:
        raw = json.loads(catalog.read_text(encoding="utf-8"))
        if raw.get("format") != FORMAT:
            raise PackError(f"{catalog}: format {raw.get('format')!r}, expected {FORMAT}")
        return {item["id"]: PackSound(**item) for item in raw["sounds"]}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PackError(f"{catalog}: cannot read: {error}") from error


def scan(root: Path = PACK, catalog: Path = CATALOG) -> tuple[list[PackSound], list[str]]:
    """Catalogue every audio file under the pack folder and save the catalogue.

    A file whose size and modification time match the saved entry is not read again, so a
    rescan of a large pack takes seconds.

    Returns:
        The sounds, by path, and one line for each file that could not be read.
    """
    if not root.is_dir():
        raise PackError(f"no pack folder {root}: put the pack's files there")
    try:
        before = {s.path: s for s in load(catalog).values()}
    except PackError:
        before = {}
    sounds: list[PackSound] = []
    problems: list[str] = []
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        old = before.get(relative)
        if old is not None and (old.size, old.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
            sounds.append(old)
            continue
        try:
            sounds.append(describe(root, path))
        except PackError as error:
            problems.append(str(error))
    body = {"format": FORMAT, "root": str(root), "sounds": [asdict(s) for s in sounds]}
    catalog.parent.mkdir(parents=True, exist_ok=True)
    write_private(catalog, json.dumps(body, ensure_ascii=False, indent=1))
    return sounds, problems


def file_of(library: str, root: Path = PACK, catalog: Path = CATALOG) -> tuple[Path, PackSound]:
    """The file and entry of a ``pack-<id>`` library, checked against the catalogue.

    Raises:
        PackError: Not a pack library, an id the catalogue lacks, or a file that moved or
            changed since the scan.
    """
    if not library.startswith(PREFIX):
        raise PackError(f"{library} is not a pack sound")
    entry = load(catalog).get(library[len(PREFIX) :])
    if entry is None:
        raise PackError(f"{library}: not in the pack's catalogue; run `redline beat sounds scan`")
    path = root / entry.path
    try:
        stat = path.stat()
    except OSError as error:
        raise PackError(f"{library}: {entry.path} is gone; scan the pack again") from error
    if (stat.st_size, stat.st_mtime_ns) != (entry.size, entry.mtime_ns):
        raise PackError(f"{library}: {entry.path} changed since the scan; scan the pack again")
    return path, entry


def display_name(sound: PackSound) -> str:
    """A pack file's name fit to quote to a person or an agent.

    Its stem, letters, digits and a few marks only, at most 48 characters: a downloaded pack's
    names are not ours to trust.
    """
    stem = Path(sound.path).stem
    spaced = re.sub(r"\s+", " ", stem)
    kept = re.sub(r"[^\w ()#+.,'-]", "", spaced, flags=re.ASCII)
    return " ".join(kept.split())[:48] or sound.id


def counts(sounds: list[PackSound]) -> dict[str, int]:
    """How many sounds each category holds, in `CATEGORIES` order."""
    found = {c: 0 for c in CATEGORIES}
    for s in sounds:
        found[s.category] += 1
    return found


def to_json(sound: PackSound) -> dict[str, Any]:
    """An entry as the page and the request show it."""
    return asdict(sound)
