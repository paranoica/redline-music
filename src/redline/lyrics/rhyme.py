"""Rhymes of line-final words by their endings, without knowing the stress."""

import re

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


def fold(word: str) -> str:
    """A word as its ending sounds: Cyrillic, vowels and final consonants folded."""
    word = (
        re.sub(r"[^a-zа-яё]", "", word.lower().translate(LATIN)).replace("ь", "").replace("ъ", "")
    )
    word = REPEAT.sub(r"\1", word.translate(VOWEL_FOLD))
    return word[:-1] + word[-1:].translate(DEVOICE) if word else word


def _vowels(word: str) -> list[int]:
    return [i for i, c in enumerate(word) if c in VOWELS]


def rhymes(a: str, b: str) -> bool:
    """Whether two words rhyme by their endings.

    They rhyme with the same ending from the second-to-last vowel, or the same last two
    vowels; a one-syllable word needs the same ending from its vowel.
    """
    fa, fb = fold(a), fold(b)
    va, vb = _vowels(fa), _vowels(fb)
    if not va or not vb:
        return False
    k = min(TAIL_VOWELS, len(va), len(vb))
    if fa[va[-k] :] == fb[vb[-k] :]:
        return True
    if k < TAIL_VOWELS:
        return False
    return [fa[i] for i in va[-k:]] == [fb[i] for i in vb[-k:]]


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
