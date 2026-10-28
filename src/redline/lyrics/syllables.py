"""Syllables of a sung line, counted by vowels."""

import re

from redline.lyrics.normalize import DIGIT_GROUP, clean
from redline.lyrics.parse import ADLIB

CYRILLIC_VOWELS = set("аеёиоуыэюя")
LATIN_VOWEL_GROUP = re.compile(r"[aeiouy]+")
TOKEN = re.compile(r"[0-9]+|[A-Za-zА-Яа-яЁё'\u2019]+(?:-[A-Za-zА-Яа-яЁё'\u2019]+)*")
# a letter held three times or more is one stretched sound; two can be two syllables
REPEAT = re.compile(r"([a-zа-яё])\1{2,}")
ACRONYM_LETTERS = 3

UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = [
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
]
TENS = [
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
]
HUNDREDS = [
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
]


def _below_thousand(n: int, feminine: bool = False) -> list[str]:
    out = [HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest < 20:
        return [*out, TEENS[rest - 10]]
    unit = UNITS[rest % 10]
    if feminine and rest % 10 in (1, 2):
        unit = "одна" if rest % 10 == 1 else "две"
    return [*out, TENS[rest // 10], unit]


def number_words(n: int) -> str:
    """A whole number in Russian words, as it is read out."""
    if n == 0:
        return "ноль"
    parts: list[str] = []
    for scale, forms, feminine in (
        (10**9, ("миллиард", "миллиарда", "миллиардов"), False),
        (10**6, ("миллион", "миллиона", "миллионов"), False),
        (10**3, ("тысяча", "тысячи", "тысяч"), True),
    ):
        count, n = divmod(n, scale)
        if count:
            last = count % 100
            form = forms[2]
            if not 10 <= last < 20 and last % 10 == 1:
                form = forms[0]
            elif not 10 <= last < 20 and 2 <= last % 10 <= 4:
                form = forms[1]
            parts += [*_below_thousand(count, feminine), form]
    parts += _below_thousand(n)
    return " ".join(p for p in parts if p)


def _latin(part: str, original: str) -> int:
    letters = re.sub(r"[^a-z]", "", part)
    groups = LATIN_VOWEL_GROUP.findall(letters)
    if original.isupper() and 1 < len(letters) <= ACRONYM_LETTERS:
        return len(letters)
    if not groups:
        # a vowelless word read out: an interjection (hmm, brr) unless it is capitals
        return len(letters) if original.isupper() else 1
    count = len(groups)
    if count > 1 and letters.endswith("e") and not letters.endswith(("le", "ee")):
        count -= 1
    return count


def _part(part: str) -> int:
    original = part.replace("'", "").replace("\u2019", "")
    lowered = REPEAT.sub(r"\1", original.lower())
    cyrillic = sum(c in CYRILLIC_VOWELS for c in lowered)
    latin = re.sub(r"[^a-z]", "", lowered)
    return cyrillic + (_latin(latin, re.sub(r"[^A-Za-z]", "", original)) if latin else 0)


def _chain(token: str) -> int:
    # a part that is only the vowel the previous part ended on is a held note
    total = 0
    last_vowel = ""
    for part in token.split("-"):
        lowered = REPEAT.sub(r"\1", part.lower())
        if lowered and set(lowered) == {last_vowel}:
            continue
        total += _part(part)
        vowels = [c for c in lowered if c in CYRILLIC_VOWELS]
        last_vowel = vowels[-1] if vowels else ""
    return total


def count_syllables(line: str) -> int:
    """Syllables of a line without its adlibs; digits are read as Russian numbers."""
    total = 0
    for token in TOKEN.findall(DIGIT_GROUP.sub("", ADLIB.sub(" ", clean(line)))):
        if token.isdigit():
            words = number_words(int(token))
            total += sum(c in CYRILLIC_VOWELS for c in words)
        else:
            total += _chain(token)
    return total
