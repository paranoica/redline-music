"""A text's title: its shape drawn by seed from the scene's titles, written by the writer.

The scene names a track with a word or two, mostly in Latin letters: an image, a brand, a
place, a code. Never the first words of its hook.
"""

import re
from dataclasses import dataclass

import numpy as np

from redline.lyrics.write.problem import Problem

# shares counted on the 2590 titles of the base, Oct 2026; a Cyrillic title on Genius carries
# an English translation in brackets, which is not part of the artist's title
TITLE_WORDS = {1: 0.36, 2: 0.30, 3: 0.13, 4: 0.12}
LATIN_SHARE = 0.63
CASES = {"caps": 0.26, "lower": 0.37, "any": 0.37}
DIGIT_SHARE = 0.15
WORD_SLACK = 1
# the writer may dress the line: **Title:** NEON, `Title: NEON`
TITLE_LINE = re.compile(r"^[\s*`]*title[*`]*\s*:[\s*`]*(.*?)[\s*`]*$", re.IGNORECASE)
QUOTES = "\"'«»“”„`"
CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
LATIN = re.compile(r"[a-z]", re.IGNORECASE)
PLAIN = re.compile(r"[^\w\s]")
HOOK_HEADER = re.compile(r"\s*\[(припев|hook|chorus)", re.IGNORECASE)


@dataclass(frozen=True)
class TitlePlan:
    """The shape a title must take: words, script, case, whether it has a digit."""

    words: int
    script: str
    case: str
    digits: bool


def draw_title(seed: int) -> TitlePlan:
    """The title's shape for a seed, from its own random stream so other draws stay put."""
    rng = np.random.Generator(np.random.PCG64([seed, 1]))
    counts = list(TITLE_WORDS)
    shares = np.asarray(list(TITLE_WORDS.values()))
    words = counts[int(rng.choice(len(counts), p=shares / shares.sum()))]
    script = "latin" if rng.random() < LATIN_SHARE else "cyrillic"
    names = list(CASES)
    case = names[int(rng.choice(len(names), p=np.asarray(list(CASES.values()))))]
    return TitlePlan(words, script, case, bool(rng.random() < DIGIT_SHARE))


def split_title(text: str) -> tuple[str | None, str]:
    """The title from a draft's first line `Title: ...`, and the text without that line."""
    first, _, rest = text.lstrip("\ufeff \t\n").partition("\n")
    found = TITLE_LINE.match(first)
    if not found:
        return None, text
    return found.group(1).strip(QUOTES).strip() or None, rest.lstrip("\n")


def title_request(plan: TitlePlan) -> str:
    """What the writer is asked for, in words."""
    count = f"{plan.words} word" + ("s" if plan.words > 1 else "")
    script = "Latin letters" if plan.script == "latin" else "Cyrillic letters"
    case = {"caps": ", in capitals", "lower": ", all in lower case", "any": ""}[plan.case]
    digit = ", with a digit or a number in it" if plan.digits else ""
    return (
        f"Start the draft with one line `Title: ...`: {count}, {script}{case}{digit}. "
        "An image, a brand, a place or a code from the text's world, the way the scene names "
        "its tracks. Never the first words of the hook, never a sentence."
    )


def _hook_start(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [i + 1 for i, line in enumerate(lines) if HOOK_HEADER.match(line)]
    for i in starts:
        for line in lines[i:]:
            if line.strip() and not line.strip().startswith("["):
                return PLAIN.sub(" ", line.lower()).split()
    return []


def check_title(title: str | None, plan: TitlePlan, text: str) -> list[Problem]:
    """What is wrong with the title against its drawn shape; empty when nothing is."""
    if not title:
        return [Problem("title", "missing", "start the draft with `Title: ...`")]
    found = []
    count = len(title.split())
    if abs(count - plan.words) > WORD_SLACK:
        found.append(Problem("title", "words", f"{count} words, about {plan.words} wanted"))
    has_cyrillic, has_latin = bool(CYRILLIC.search(title)), bool(LATIN.search(title))
    if plan.script == "latin" and (has_cyrillic or not has_latin):
        found.append(Problem("title", "script", "in Latin letters only"))
    if plan.script == "cyrillic" and (has_latin or not has_cyrillic):
        found.append(Problem("title", "script", "in Cyrillic letters only"))
    if plan.case == "caps" and title != title.upper():
        found.append(Problem("title", "case", "all in capitals"))
    if plan.case == "lower" and title != title.lower():
        found.append(Problem("title", "case", "all in lower case"))
    if plan.digits and not re.search(r"\d", title):
        found.append(Problem("title", "digits", "with a digit or a number in it"))
    said = PLAIN.sub(" ", title.lower()).split()
    if said and _hook_start(text)[: len(said)] == said:
        found.append(Problem("title", "hook", "not the first words of the hook"))
    return found
