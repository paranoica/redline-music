"""Rhymes of line-final words: by the stressed vowel when the stress is known, else by endings."""

import re

from redline.lyrics.normalize import clean
from redline.lyrics.parse import ADLIB

VOWELS = set("аеиоуыэюяё")
# unstressed vowels sound alike, and so do voiced and voiceless consonants at the end
VOWEL_FOLD = str.maketrans("оеэяюёы", "аииауаи")
DEVOICE = str.maketrans("бвгджз", "пфктшс")
LATIN = str.maketrans(
    dict(zip("abcdefghiklmnopqrstuvyz", "абкдефгхиклмнопкрстувиз", strict=True))
    | {"j": "дж", "w": "у", "x": "кс"}
)
REPEAT = re.compile(r"(.)\1+")
TAIL_VOWELS = 2
# stressed vowels keep their sound; only the iotated letters and ы/и coincide
STRESSED_FOLD = str.maketrans("яёюеы", "аоуэи")
STRESS = "+"
# a held sound: a letter three times or more, or repeated over hyphens
STRETCH = re.compile(r"([а-яё])\1{2,}")
HYPHEN_STRETCH = re.compile(r"(\w)(?:-\1)+", re.IGNORECASE)
WORD = re.compile(r"[\w+'\u2019-]*[^\W\d_][\w+'\u2019-]*")


def fold(word: str) -> str:
    """A word as its ending sounds: Cyrillic, vowels and final consonants folded."""
    word = (
        re.sub(r"[^a-zа-яё]", "", word.lower().translate(LATIN)).replace("ь", "").replace("ъ", "")
    )
    word = REPEAT.sub(r"\1", word.translate(VOWEL_FOLD))
    return word[:-1] + word[-1:].translate(DEVOICE) if word else word


def _vowels(word: str) -> list[int]:
    return [i for i, c in enumerate(word) if c in VOWELS]


def last_word(line: str) -> str:
    """The last word of a line, adlibs left out, stress mark kept."""
    found = WORD.findall(ADLIB.sub(" ", line))
    return found[-1] if found else ""


def _stressed(word: str) -> tuple[str, int] | None:
    """The word in Cyrillic letters and the index of its stressed vowel, if known.

    The stress is known from a "+" mark, a capital vowel inside a lower-case word, ё or a
    single vowel.
    """
    plain = word.replace(STRESS, "")
    inner = [i for i, c in enumerate(plain) if i and c.isupper()]
    if len(inner) == 1 and plain[inner[0]].lower() in VOWELS:
        # the author's capital vowel marks the stress and overrides a model's mark; a
        # capital first letter only starts a sentence, and capitals in a row are a name
        at = inner[0]
        word = plain[:at] + STRESS + plain[at:].lower()
    letters = re.sub(r"[^a-zа-яё+]", "", HYPHEN_STRETCH.sub(r"\1", word).lower()).translate(LATIN)
    letters = STRETCH.sub(r"\1", letters.replace("ь", "").replace("ъ", ""))
    if STRESS in letters:
        at = letters.index(STRESS)
        letters = letters.replace(STRESS, "")
        return (letters, at) if at < len(letters) and letters[at] in VOWELS else None
    vowels = _vowels(letters)
    if "ё" in letters:
        return letters, letters.index("ё")
    return (letters, vowels[0]) if len(vowels) == 1 else None


def _by_stress(a: tuple[str, int], b: tuple[str, int]) -> bool:
    (wa, ia), (wb, ib) = a, b
    if wa[ia].translate(STRESSED_FOLD) != wb[ib].translate(STRESSED_FOLD):
        return False
    after_a, after_b = wa[ia + 1 :], wb[ib + 1 :]
    vowels_a, vowels_b = _vowels(after_a), _vowels(after_b)
    if len(vowels_a) != len(vowels_b):
        return False
    if not vowels_a:
        # stress on the last syllable: the same closing consonant, or none in both
        return after_a[-1:].translate(DEVOICE) == after_b[-1:].translate(DEVOICE)
    # otherwise the consonant that opens the next syllable carries the rhyme; before a
    # vowel voiced and voiceless sound apart
    before_a = after_a[vowels_a[0] - 1 : vowels_a[0]]
    before_b = after_b[vowels_b[0] - 1 : vowels_b[0]]
    return before_a == before_b


def rhymes(a: str, b: str) -> bool:
    """Whether two line-final words rhyme.

    With the stress known: the same stressed vowel; on a last syllable the same closing
    consonant; else the same consonant before the next vowel. Unknown stress: the same
    ending from the second-to-last vowel.
    """
    a, b = clean(a), clean(b)
    sa, sb = _stressed(a), _stressed(b)
    if sa and sb:
        return _by_stress(sa, sb)
    fa, fb = fold(a), fold(b)
    va, vb = _vowels(fa), _vowels(fb)
    if not va or not vb:
        return False
    k = min(TAIL_VOWELS, len(va), len(vb))
    return fa[va[-k] :] == fb[vb[-k] :]


def letters(ends: list[str]) -> str:
    """A rhyme letter per line: the letter of the first earlier line it rhymes with."""
    out: list[str] = []
    for i, word in enumerate(ends):
        earlier = next((out[j] for j in range(i) if rhymes(ends[j], word)), None)
        out.append(earlier or chr(ord("A") + len(set(out))))
    return "".join(out)


def scheme(ends: list[str]) -> str | None:
    """The scheme of four line-final words: AABB, ABAB, ABBA, AAAA or other."""
    if len(ends) != 4:
        return None
    found = letters(ends)
    return found if found in ("AABB", "ABAB", "ABBA", "AAAA") else "other"
