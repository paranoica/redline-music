"""Text to comparable words: case, letters, punctuation."""

import re
import unicodedata

APOSTROPHES = "'\u2019`\u02bc\u2018"
CYRILLIC = re.compile(r"[а-я]")
LATIN = re.compile(r"[a-z]")
# Latin letters that look like Cyrillic ones, both ways
LATIN_TO_CYRILLIC = str.maketrans("aeopcxykmthb", "аеорсхукмтнв")
CYRILLIC_TO_LATIN = str.maketrans("аеорсхукмтнв", "aeopcxykmthb")
DIGIT_GROUP = re.compile(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))")
TOKEN = re.compile(r"[a-zа-я0-9]+")
INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
REPEAT = re.compile(r"([a-zа-я])\1+")


def _fold_script(token: str) -> str:
    cyrillic = len(CYRILLIC.findall(token))
    latin = len(LATIN.findall(token))
    if cyrillic and latin:
        table = LATIN_TO_CYRILLIC if cyrillic >= latin else CYRILLIC_TO_LATIN
        return token.translate(table)
    return token


def clean(text: str) -> str:
    """The text in composed Unicode form, without invisible characters."""
    return unicodedata.normalize("NFC", text).translate(INVISIBLE)


def words(text: str, keep_doubles: bool = False) -> list[str]:
    """Lower-case words with ё as е, look-alike letters folded and stretched letters collapsed.

    Apostrophes join a word, any other punctuation splits it; digit groups split by a space
    ("100 000") read as one number. keep_doubles leaves repeated letters as written, for word
    lists people read rather than for matching.
    """
    text = clean(text).lower().replace("ё", "е")
    for mark in APOSTROPHES:
        text = text.replace(mark, "")
    text = DIGIT_GROUP.sub("", text)
    raw = re.split(r"[^a-zа-я0-9]+", text)
    found = [_fold_script(t) for t in raw if TOKEN.fullmatch(t)]
    return found if keep_doubles else [REPEAT.sub(r"\1", t) for t in found]
