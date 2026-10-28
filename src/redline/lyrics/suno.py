"""A text as Suno takes it: English section tags, spelled-out numbers, a style box and settings.

The rules come from user reports, not from Suno, which publishes no tag grammar.
"""

import re
from collections import Counter
from collections.abc import Callable

from redline.lyrics.normalize import DIGIT_GROUP
from redline.lyrics.parse import Track
from redline.lyrics.recipe import Recipe
from redline.lyrics.rhyme import STRESS, VOWELS, last_word
from redline.lyrics.syllables import number_words

MAX_CHARACTERS = 5000
TAGS = {
    "intro": "Intro",
    "hook": "Chorus",
    "prehook": "Pre-Chorus",
    "bridge": "Bridge",
    "outro": "Outro",
    "other": "Interlude",
}
# Latin letter names as a Russian speaker says them
LETTER_NAMES = (
    "эй би си ди и эф джи эйч ай джей кей эл эм эн оу пи кью ар эс ти ю ви дабл-ю экс уай зи"
)
LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ", LETTER_NAMES.split(), strict=True))
ACRONYM = re.compile(r"\b[A-Z]{2,4}\b")
NUMBER = re.compile(r"\d+")
# the scene's sound in words Suno knows; no names, which the style box must never hold
STYLE = ["dark russian underground rap", "cloud rap", "autotuned male vocals", "distorted 808"]
FLOW = {
    "short": "fast bouncy flow",
    "middle": "laid-back flow",
    "long": "slow hazy flow",
}
EXCLUDE = ["pop", "acoustic", "female vocals", "clean vocals"]
# starting points from user guides for v6: keep the tags as typed, follow them closely
SETTINGS = {
    "Model": "v6",
    "Weirdness": "30-50 %",
    "Style Influence": "75-85 %",
    "Variety": "0",
}
TITLE_WORDS = 3
Mark = Callable[[str], str]


def respell(line: str) -> str:
    """Numbers as Russian words, Latin acronyms letter by letter; the rest as written."""
    line = NUMBER.sub(lambda m: number_words(int(m.group())), DIGIT_GROUP.sub("", line))
    return ACRONYM.sub(lambda m: "-".join(LETTERS[c] for c in m.group()), line)


def _stress_end(line: str, marked: str) -> str:
    # a wrong stress on the line's last word breaks the rhyme, so only that word is marked
    word = last_word(marked)
    if STRESS not in word:
        return line
    plain = word.replace(STRESS, "")
    at = word.index(STRESS)
    vowels = [i for i, c in enumerate(plain.lower()) if c in VOWELS]
    if len(vowels) < 2 or at >= len(plain) or plain[at].lower() not in VOWELS:
        return line
    capital = plain[:at] + plain[at].upper() + plain[at + 1 :]
    cut = line.rfind(plain)
    return line if cut < 0 else line[:cut] + capital + line[cut + len(plain) :]


def convert(track: Track, mark: Mark | None = None) -> str:
    """The lyrics box: tags alone on their lines, a blank line between sections only."""
    blocks = []
    verses = 0
    for section in track.sections:
        if section.kind == "verse":
            verses += 1
            tag = f"Verse {verses}"
        else:
            tag = TAGS[section.kind]
        lines = [line.text for line in section.lines]
        if mark is not None:
            lines = [
                _stress_end(line.text, mark(line.plain)) if line.plain else line.text
                for line in section.lines
            ]
        if not lines and section.kind == "intro":
            tag = "Instrumental Intro"
        body = [f"[{tag}]", *(respell(line) for line in lines)]
        if section.kind == "outro":
            body.append("[End]")
        blocks.append("\n".join(body))
    text = "\n\n".join(blocks) + "\n"
    if len(text) > MAX_CHARACTERS:
        raise ValueError(f"{len(text)} characters; Suno takes {MAX_CHARACTERS} at most")
    return text


def title_of(track: Track) -> str:
    """The first words of the chorus line said most often."""
    said = Counter(
        line.plain for s in track.sections if s.kind == "hook" for line in s.lines if line.plain
    )
    if not said:
        said = Counter(line.plain for s in track.sections for line in s.lines if line.plain)
    if not said:
        return "Без названия"
    top = max(said.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
    first = " ".join(top.split()[:TITLE_WORDS])
    return first[:1].upper() + first[1:]


def card(track: Track, recipe: Recipe, mark: Mark | None = None) -> str:
    """Everything to fill in Suno for one text, ready to copy field by field."""
    style = ", ".join([*STYLE, FLOW[recipe.band]])
    out = [
        f"Title: {title_of(track)}",
        "",
        f"Style: {style}",
        f"Exclude: {', '.join(EXCLUDE)}",
        "",
        *(f"{name}: {value}" for name, value in SETTINGS.items()),
        "",
        "Lyrics:",
        "",
        convert(track, mark),
    ]
    return "\n".join(out)
