"""Checks of a draft against its recipe: lyrics only, sections, lines, syllables, rhymes."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from redline.lyrics.text.normalize import words
from redline.lyrics.text.parse import HEADER, parse_text
from redline.lyrics.text.rhyme import last_word, rhymes
from redline.lyrics.text.syllables import count_syllables
from redline.lyrics.write.recipe import Recipe

LINE_SLACK = 1
# past about 3000 characters Suno rushes the lyrics or drops sections
MAX_CHARACTERS = 3000
# a floor, not a target: real lines wander off the band (in a quarter of the owner's and the
# favourite artist's texts under 68 % and 61 % of lines sit within the band and one), and
# the owner heard a text held to the band as flat
SYLLABLE_TOLERANCE = 1
SYLLABLE_SHARE = 0.5
# on a beat a line that misses its bar is heard at once, and the flows already differ by section
BEAT_SYLLABLE_SHARE = 0.75
NAMED_LINES = 5
RHYME_SHARE = 0.3
# one rhyme held longer than this reads as a monotone
MAX_RHYME_RUN = 4
WINDOW = 4
PAIRS = {
    "AABB": ((0, 1), (2, 3)),
    "ABAB": ((0, 2), (1, 3)),
    "ABBA": ((0, 3), (1, 2)),
    "AAAA": ((0, 1), (1, 2), (2, 3)),
}
# endings that rarely end anything but a verb; heuristics, so only reported
VERB_ENDINGS = re.compile(
    r"([аяеиуыо]ть|[ауяю]ет|[ауяюе]ют|ит|[аяеи]л|[аяеи]л[аи]|[аяеи]л(ся|ась))$"
)
SHORT_VERBS = 5
NOT_VERBS_TEXT = (
    "мать путь суть сеть нить кровать печать знать ртуть жуть плеть власть "
    "вокал финал канал капитал сериал кристал нахал штурвал подвал скандал идеал причал "
    "бокал вокзал журнал материал сигнал оригинал интервал криминал арсенал карнавал "
    "предел пробел отдел удел крокодил кредит лимит магнит гранит динамит аппетит "
    "бандит транзит визит депозит рубли нули корабли"
)
NOT_VERBS = set(NOT_VERBS_TEXT.split())
StressMark = Callable[[str], str]


@dataclass(frozen=True)
class Problem:
    """Something a draft must fix: what kind, where, and how."""

    kind: str
    where: str
    detail: str


def is_verb(word: str) -> bool:
    """Whether a Russian word looks like a verb by its ending (a heuristic)."""
    found = words(word.replace("+", ""), keep_doubles=True)
    if not found:
        return False
    w = found[-1]
    if w in NOT_VERBS:
        return False
    if w.endswith(("ться", "тся")) or re.search(r"([её]шь|ишь)$", w):
        return True
    return len(w) >= SHORT_VERBS and bool(VERB_ENDINGS.search(w))


def _not_lyrics(text: str) -> list[Problem]:
    if "```" in text:
        return [Problem("not lyrics", "the draft", "code fences: write the lyrics only")]
    for line in text.splitlines():
        if line.strip():
            if not HEADER.match(line.strip()):
                return [Problem("not lyrics", "line 1", "start with a section header")]
            break
    return []


def _longest_run(ends: list[str]) -> int:
    """The most lines in a row that all rhyme with the first of them, from any line on."""
    longest = 0
    for start in range(len(ends)):
        end = start + 1
        while end < len(ends) and (ends[end] == ends[start] or rhymes(ends[start], ends[end])):
            end += 1
        longest = max(longest, end - start)
    return longest


# ordinary places set against the flex, by word stem: the scene uses the trick, but a text
# that puts one in every line reads as a parody of it
PLACE_STEMS = (
    "маршрутк",
    "остановк",
    "подъезд",
    "общаг",
    "пвз",
    "вокзал",
    "ларек",
    "ларьк",
    "шиномонтаж",
    "автомойк",
    "поликлиник",
    "больничк",
    "больниц",
    "военкомат",
    "электричк",
    "рынк",
    "рынок",
    "гараж",
    "промзон",
    "хрущевк",
    "пятерочк",
    "авито",
    "заправк",
    "вахт",
)


def places(text: str) -> int:
    """Ordinary places named in the distinct lines of a text."""
    seen = {line.plain for s in parse_text(text).sections for line in s.lines if line.plain}
    return sum(1 for line in seen for w in words(line) if w.startswith(PLACE_STEMS))


def _distinct(lines: list[Any]) -> list[Any]:
    # a chanted line said again is a repeat, not another rhyme
    seen: list[list[str]] = []
    out = []
    for line in lines:
        said = words(line.plain)
        if said not in seen:
            seen.append(said)
            out.append(line)
    return out


def _sections(track: Any, recipe: Recipe, mark: StressMark) -> tuple[list[Problem], list, list]:
    """Line counts per section, every line's distance from its band, and the scheme's pairs."""
    problems: list[Problem] = []
    lines: list[tuple[int, int, str]] = []
    pairs: list[tuple[str, str]] = []
    for i, (section, plan) in enumerate(zip(track.sections, recipe.sections, strict=True), 1):
        sung = [line for line in section.lines if line.plain]
        # a line past the end of the last section is most likely a note to the reader
        over = 0 if i == len(recipe.sections) else LINE_SLACK
        if len(sung) - plan.lines > over or plan.lines - len(sung) > LINE_SLACK:
            problems.append(
                Problem(
                    "lines", f"section {i} ({plan.kind})", f"{len(sung)} lines, need {plan.lines}"
                )
            )
        low, high = plan.syllables
        for line in sung:
            count = count_syllables(line.text)
            miss = max(low - count, count - high, 0)
            lines.append((miss, len(lines) + 1, f"{count} syllables, need {low}-{high}"))
        for start in range(0, len(sung) - WINDOW + 1, WINDOW):
            window = sung[start : start + WINDOW]
            for a, b in PAIRS[plan.scheme]:
                if words(window[a].plain) != words(window[b].plain):
                    pairs.append(
                        (last_word(mark(window[a].plain)), last_word(mark(window[b].plain)))
                    )
    return problems, lines, pairs


def check_draft(
    text: str,
    recipe: Recipe,
    mark: StressMark = str,
    syllable_share: float | None = None,
    rhyme_share: float = RHYME_SHARE,
) -> tuple[list[Problem], dict[str, Any]]:
    """What the draft must fix to match the recipe, and the numbers behind the verdicts."""
    problems = _not_lyrics(text)
    if problems:
        return problems, {}
    if len(text) > MAX_CHARACTERS:
        detail = f"{len(text)} characters; Suno takes about {MAX_CHARACTERS}, shorten the lines"
        return [Problem("length", "the draft", detail)], {}
    track = parse_text(text)
    got = [s.kind for s in track.sections]
    wanted = [s.kind for s in recipe.sections]
    if got != wanted:
        detail = f"sections should be {'-'.join(wanted)}, got {'-'.join(got) or 'none'}"
        return [Problem("sections", "the draft", detail)], {}
    problems, lines, pairs = _sections(track, recipe, mark)
    if syllable_share is None:
        syllable_share = BEAT_SYLLABLE_SHARE if recipe.bpm else SYLLABLE_SHARE
    off = [line for line in lines if line[0] > SYLLABLE_TOLERANCE]
    share = 1 - len(off) / len(lines) if lines else 1.0
    if share < syllable_share:
        problems.append(
            Problem(
                "syllables",
                "the draft",
                f"{share:.0%} of lines in range, need {syllable_share:.0%}",
            )
        )
        for _, number, detail in sorted(off, key=lambda o: (-o[0], o[1]))[:NAMED_LINES]:
            problems.append(Problem("syllables", f"line {number}", detail))
    run = max(
        (
            _longest_run(
                [last_word(mark(line.plain)) for line in _distinct(section.lines) if line.plain]
            )
            for section in track.sections
        ),
        default=0,
    )
    if run > MAX_RHYME_RUN:
        detail = f"one rhyme held for a run of {run} lines; change the rhyme every 2-4 lines"
        problems.append(Problem("rhymes", "the draft", detail))
    rhymed = sum(rhymes(a, b) for a, b in pairs) / len(pairs) if pairs else 1.0
    if rhymed < rhyme_share:
        problems.append(
            Problem(
                "rhymes", "the draft", f"{rhymed:.0%} of rhyme pairs rhyme, need {rhyme_share:.0%}"
            )
        )
    allowed = max(1, dict(recipe.devices).get("place", 0))
    named = places(text)
    if named > allowed:
        detail = f"{named} ordinary places (stop, minibus, clinic…); keep to {allowed}"
        problems.append(Problem("places", "the draft", detail))
    verbs = sum(is_verb(a) and is_verb(b) for a, b in pairs)
    stats = {
        "syllables_in_range": round(share, 4),
        "rhyme_pairs": len(pairs),
        "rhymed_pairs": round(rhymed, 4),
        "verb_rhymes": round(verbs / len(pairs), 4) if pairs else 0.0,
        "longest_rhyme_run": run,
    }
    return problems, stats
