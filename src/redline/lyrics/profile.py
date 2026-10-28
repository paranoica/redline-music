"""Style profiles: mergeable counts per artist, merged into groups, the scene and the owner's."""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from redline.lyrics.normalize import words
from redline.lyrics.parse import Artist, LyricsError, Track
from redline.lyrics.rhyme import last_word, rhymes, scheme
from redline.lyrics.syllables import count_syllables

SCHEMA_VERSION = 1
KINDS = ("intro", "hook", "prehook", "verse", "bridge", "outro", "other")
MAX_SYLLABLES = 30
TOP_PATTERNS = 20
TOP_ADLIBS = 50
TOP_WORDS = 300
TOP_LATIN = 100
DUPLICATE_SHARE = 0.6
MIN_TRACKS = 5
MINE_WEIGHT = 0.5
KEYS = {
    "meta",
    "structure",
    "line_syllables",
    "lines_per_section",
    "rhyme",
    "hook",
    "adlibs",
    "vocabulary",
    "latin",
    "themes",
}
# counted against the same total as their family's first field, so shares inside a family
# survive a weighted merge
FAMILIES = {
    "words_start": "words",
    "words_end": "words",
    "words_hook": "words",
    "words_verse": "words",
    "latin_start": "latin",
    "latin_end": "latin",
    "latin_hook": "latin",
    "latin_verse": "latin",
}
RAW = {"totals", "presence"}
STOP_TEXT = (
    "и в во не на я ты он она оно мы вы они что как а но да нет то это так же ли бы с со "
    "к ко по из за от до у о об для при над под про без через мой моя мое мои твой твоя "
    "твое твои его ее их наш ваш свой себя меня тебя мне тебе нас вас им ему ей все всё "
    "вот там тут где кто когда если или уже еще только даже ну эй е "
    "the a an and or but to of in on at for is are was it i you me my your we he she they"
)
STOP_WORDS = set(STOP_TEXT.split())
LATIN_WORD = re.compile(r"^[a-z]+$")
ALPHA_WORD = re.compile(r"^[a-zа-я]+$")

Counts = dict[str, Counter]
Stresser = Callable[[str], str]


def _sung(track: Track) -> list[tuple[str, Any]]:
    return [(s.kind, line) for s in track.sections for line in s.lines if line.plain]


def _is_host(who: str, names: Sequence[str]) -> bool:
    if not who or not names:
        return True
    said = " ".join(words(who))
    return any(" ".join(words(n)) in said for n in names if words(n))


def _count_words(counts: Counts, kind: str, text: str) -> None:
    found = [w for w in words(text) if ALPHA_WORD.match(w)]
    counts["script"].update("latin" if LATIN_WORD.match(w) else "cyrillic" for w in found)
    content = [w for w in found if w not in STOP_WORDS]
    for i, word in enumerate(content):
        family = "latin" if LATIN_WORD.match(word) else "words"
        counts[family][word] += 1
        if i == 0:
            counts[f"{family}_start"][word] += 1
        if i == len(content) - 1:
            counts[f"{family}_end"][word] += 1
        if kind == "hook":
            counts[f"{family}_hook"][word] += 1
        elif kind == "verse":
            counts[f"{family}_verse"][word] += 1


def _count_section_lines(counts: Counts, kind: str, lines: list[Any], mark: Stresser) -> None:
    ends = [last_word(mark(line.plain)) for line in lines]
    for i in range(0, len(ends) - 3, 4):
        found = scheme(ends[i : i + 4])
        if found:
            counts[f"rhyme_schemes/{kind}"][found] += 1
    for i, end in enumerate(ends):
        later = ends[i + 1 : i + 3]
        counts[f"rhyme_pairs/{kind}"]["yes" if any(rhymes(end, e) for e in later) else "no"] += 1
    if kind == "hook":
        seen = Counter(" ".join(words(line.plain)) for line in lines)
        for line in lines:
            repeated = seen[" ".join(words(line.plain))] > 1
            counts["hook_repeat"]["yes" if repeated else "no"] += 1


def _count_track(
    counts: Counts, guests: Counts, track: Track, names: Sequence[str], mark: Stresser
) -> None:
    own_sections = []
    for section in track.sections:
        target = counts if _is_host(section.who, names) else guests
        sung = [line for line in section.lines if line.plain]
        for line in section.lines:
            if line.plain:
                syllables = min(count_syllables(line.text), MAX_SYLLABLES)
                target[f"line_syllables/{section.kind}"][str(syllables)] += 1
                _count_words(target, section.kind, line.plain)
            target["adlib_lines"]["yes" if line.adlibs else "no"] += 1
            target["adlib_end"]["yes" if line.text.rstrip().endswith(")") else "no"] += 1
            for adlib in line.adlibs:
                target["adlibs"].update(w for w in words(adlib) if ALPHA_WORD.match(w))
        if track.headerless:
            continue
        _count_section_lines(target, section.kind, sung, mark)
        if target is counts:
            own_sections.append(section)
            counts[f"lines_per_section/{section.kind}"][str(len(sung))] += 1
    counts["totals"]["tracks"] += 1
    if track.headerless or not own_sections:
        return
    counts["structure"]["-".join(s.kind for s in own_sections)] += 1
    counts["sections_per_track"][str(len(own_sections))] += 1
    counts["hooks_per_track"][str(sum(s.kind == "hook" for s in own_sections))] += 1


def count_tracks(
    tracks: Sequence[Track], names: Sequence[str] = (), mark: Stresser = str
) -> tuple[Counts, Counts]:
    """Counts of the artist's own sections, and of sections credited to someone else."""
    own: Counts = _DefaultCounts()
    guests: Counts = _DefaultCounts()
    for track in tracks:
        _count_track(own, guests, track, names, mark)
    return {k: v for k, v in own.items() if v}, {k: v for k, v in guests.items() if v}


class _DefaultCounts(dict):
    def __missing__(self, key: str) -> Counter:
        self[key] = Counter()
        return self[key]


def dedupe(tracks: Sequence[Track]) -> list[Track]:
    """Drop a track whose lines are mostly those of a track kept before it."""
    kept: list[Track] = []
    seen: list[set[str]] = []
    for track in tracks:
        lines = {" ".join(words(line.plain)) for _, line in _sung(track)} - {""}
        if lines and any(len(lines & s) > DUPLICATE_SHARE * len(lines) for s in seen):
            continue
        kept.append(track)
        seen.append(lines)
    return kept


def merge(parts: Sequence[Counts], weights: Sequence[float] | None = None) -> Counts:
    """Sum counts as they are, or as shares with a weight per part.

    Totals and word presence always add up as they are.
    """
    out: Counts = {}
    for i, part in enumerate(parts):
        for field, counter in part.items():
            target = out.setdefault(field, Counter())
            if weights is None or field in RAW:
                target.update(counter)
                continue
            base = part.get(FAMILIES.get(field, field), counter)
            total = sum(base.values())
            if total:
                for key, value in counter.items():
                    target[key] += weights[i] * value / total
    return out


def _shares(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    return {k: round(v / total, 4) for k, v in sorted(counter.items())} if total else {}


def _histogram(counter: Counter) -> dict[str, Any]:
    shares = {int(k): v for k, v in counter.items()}
    total = sum(shares.values())
    out: dict[str, Any] = {"hist": _shares(counter)}
    for p in (10, 50, 90):
        running = 0.0
        value = None
        for key in sorted(shares):
            running += shares[key]
            if total and running >= p / 100 * total - 1e-9:
                value = key
                break
        out[f"p{p}"] = value
    return out


def _yes_share(counter: Counter) -> float | None:
    total = sum(counter.values())
    return round(counter.get("yes", 0) / total, 4) if total else None


def _top(counter: Counter, n: int) -> list[tuple[str, float]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def _words(
    counts: Counts, family: str, n: int, presence: Counter, min_presence: int
) -> list[dict[str, Any]]:
    counter = counts.get(family, Counter())
    total = sum(counter.values())
    eligible = counter
    if min_presence > 1:
        eligible = Counter({w: c for w, c in counter.items() if presence.get(w, 0) >= min_presence})
    rows = []
    for word, count in _top(eligible, n):
        row: dict[str, Any] = {"word": word, "per_1000": round(1000 * count / total, 4)}
        for place in ("start", "end", "hook", "verse"):
            row[place] = round(counts.get(f"{family}_{place}", Counter())[word] / count, 4)
        rows.append(row)
    return rows


def to_profile(counts: Counts, level: str, name: str, min_presence: int = 1) -> dict[str, Any]:
    """The profile written to disk: shares, histograms with percentiles, top lists."""
    presence = counts.get("presence", Counter())
    totals = counts.get("totals", Counter())
    profile: dict[str, Any] = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "level": level,
            "name": name,
            "tracks": int(totals.get("tracks", 0)),
            "artists": int(totals.get("artists", 0)),
        },
        "structure": {
            "patterns": [
                [p, round(s, 4)]
                for p, s in _top(Counter(_shares(counts.get("structure", Counter()))), TOP_PATTERNS)
            ],
            "sections_per_track": _histogram(counts.get("sections_per_track", Counter())),
        },
        "line_syllables": {
            k: _histogram(counts[f"line_syllables/{k}"])
            for k in KINDS
            if counts.get(f"line_syllables/{k}")
        },
        "lines_per_section": {
            k: _histogram(counts[f"lines_per_section/{k}"])
            for k in KINDS
            if counts.get(f"lines_per_section/{k}")
        },
        "rhyme": {
            k: {
                "schemes": _shares(counts.get(f"rhyme_schemes/{k}", Counter())),
                "neighbour_share": _yes_share(counts.get(f"rhyme_pairs/{k}", Counter())),
            }
            for k in KINDS
            if counts.get(f"rhyme_pairs/{k}")
        },
        "hook": {
            "per_track": _histogram(counts.get("hooks_per_track", Counter())),
            "repeated_line_share": _yes_share(counts.get("hook_repeat", Counter())),
        },
        "adlibs": {
            "top": [
                [w, round(c / max(sum(counts.get("adlibs", Counter()).values()), 1e-12), 4)]
                for w, c in _top(counts.get("adlibs", Counter()), TOP_ADLIBS)
            ],
            "line_share": _yes_share(counts.get("adlib_lines", Counter())),
            "end_share": _yes_share(counts.get("adlib_end", Counter())),
        },
        "vocabulary": {"top": _words(counts, "words", TOP_WORDS, presence, min_presence)},
        "latin": {
            "top": _words(counts, "latin", TOP_LATIN, presence, min_presence),
            "en_share": _shares(counts.get("script", Counter())).get("latin", 0.0),
        },
        "themes": [],
    }
    profile["meta"]["digest"] = hashlib.sha256(dumps(profile).encode()).hexdigest()[:16]
    return profile


def dumps(profile: Mapping[str, Any]) -> str:
    """Byte-stable JSON of a profile."""
    return json.dumps(profile, ensure_ascii=False, sort_keys=True, indent=1) + "\n"


def load_profile(path: Path) -> dict[str, Any]:
    """Read a profile and refuse one with a missing or unknown top-level key."""
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LyricsError(f"{path}: {error}") from error
    if not isinstance(profile, dict):
        raise LyricsError(f"{path}: a profile is a JSON object")
    missing, unknown = KEYS - set(profile), set(profile) - KEYS
    if missing or unknown:
        parts = [f"missing {', '.join(sorted(missing))}"] if missing else []
        parts += [f"unknown {', '.join(sorted(unknown))}"] if unknown else []
        raise LyricsError(f"{path}: {'; '.join(parts)}")
    if profile["meta"].get("schema_version") != SCHEMA_VERSION:
        raise LyricsError(f"{path}: schema version {profile['meta'].get('schema_version')}")
    return profile


def _with_presence(counts: Counts, weight: int = 1) -> Counts:
    out = dict(counts)
    out["presence"] = Counter({w: weight for f in ("words", "latin") for w in counts.get(f, {})})
    out["totals"] = counts.get("totals", Counter()) + Counter({"artists": 1})
    return out


def build_profiles(
    base: Mapping[str, Sequence[Track]],
    artists: Mapping[str, Artist],
    mine: Sequence[Track],
    min_tracks: int = MIN_TRACKS,
    mark: Stresser = str,
) -> dict[str, dict[str, Any]]:
    """Profiles per artist, per group, of the scene, the owner's and the working one.

    Groups and the scene give each artist with min_tracks tracks or more an equal say;
    their words must come from two artists at least. Guest sections go to the scene as one
    more voice. The working profile is the owner's and the scene's, half and half.
    """
    profiles: dict[str, dict[str, Any]] = {}
    own: dict[str, Counts] = {}
    guests: list[Counts] = []
    for slug, tracks in sorted(base.items()):
        artist = artists.get(slug)
        counts, guest = count_tracks(dedupe(tracks), artist.names if artist else (), mark)
        own[slug] = _with_presence(counts)
        if guest:
            guests.append(guest)
        profiles[f"artist-{slug}"] = to_profile(own[slug], "artist", slug)
    eligible = {s: c for s, c in own.items() if c["totals"]["tracks"] >= min_tracks}
    groups = sorted({artists[s].profile for s in eligible if s in artists and artists[s].profile})
    for group in groups:
        parts = [c for s, c in eligible.items() if s in artists and artists[s].profile == group]
        profiles[f"group-{group}"] = to_profile(merge(parts, [1.0] * len(parts)), "group", group, 2)
    scene_parts = list(eligible.values())
    if guests:
        scene_parts.append(merge(guests))
    scene = merge(scene_parts, [1.0] * len(scene_parts)) if scene_parts else {}
    profiles["scene"] = to_profile(scene, "scene", "scene", 2)
    # the owner's words always pass the two-artist floor
    owner = _with_presence(count_tracks(mine, (), mark)[0], weight=2)
    profiles["mine"] = to_profile(owner, "mine", "mine")
    working = merge([owner, scene], [MINE_WEIGHT, 1 - MINE_WEIGHT]) if scene else owner
    profiles["working"] = to_profile(working, "working", "working", 2)
    return profiles
