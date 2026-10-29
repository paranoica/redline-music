"""Words the last texts have worn out: in four of the last fifteen, they are a habit."""

from collections import Counter
from pathlib import Path

from redline.lyrics.base.profile import STOP_WORDS
from redline.lyrics.text.normalize import words
from redline.lyrics.write.problem import Problem

RECENT = 15
MIN_TEXTS = 4
# words no text can do without; a habit is an image or a name, not grammar
PLAIN_WORDS = frozenset(
    [
        "снова",
        "опять",
        "весь",
        "этот",
        "будто",
        "вместо",
        "помню",
        "больше",
        "будет",
        "всех",
        "мимо",
        "сказал",
        "одного",
        "только",
        "теперь",
        "тоже",
        "сейчас",
        "просто",
        "потом",
        "здесь",
        "всегда",
        "никогда",
        "могу",
        "знаю",
        "хочу",
        "надо",
        "нужно",
        "даже",
        "пока",
        "лишь",
        "себе",
        "тебя",
        "меня",
        "кому",
        "чтобы",
        "который",
        "чем",
    ]
)
MIN_LETTERS = 4
# a draft may still say a few of them; past this it leans on them
MAX_USED = 3
# endings move, the start stays: стакан, стакане, стаканы
STEM = 5


def stem(word: str) -> str:
    """The part of a word its forms share, near enough."""
    return word[:STEM]


def _sung_words(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if not line.strip().startswith("[")]
    return [
        w
        for w in words("\n".join(lines))
        if len(w) >= MIN_LETTERS and w.isalpha() and w not in STOP_WORDS | PLAIN_WORDS
    ]


def _newest_text(folder: Path) -> Path | None:
    found = list(folder.glob("text*.txt"))
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def worn_words(generated: Path, skip: int | None = None) -> tuple[str, ...]:
    """Words in MIN_TEXTS or more of the newest RECENT texts, most worn first.

    Args:
        generated: The folder of generated texts, one folder per seed.
        skip: The seed being written, which does not count against itself.
    """
    texts = []
    for folder in generated.glob("*") if generated.is_dir() else []:
        if not folder.name.isdigit() or (skip is not None and folder.name == str(skip)):
            continue
        newest = _newest_text(folder)
        if newest is not None:
            texts.append((newest.stat().st_mtime, newest))
    texts.sort(reverse=True)
    stems: Counter[str] = Counter()
    forms: Counter[str] = Counter()
    for _, path in texts[:RECENT]:
        sung = _sung_words(path.read_text(encoding="utf-8"))
        forms.update(sung)
        stems.update({stem(w) for w in sung})
    worn = []
    for found, texts_with in stems.items():
        if texts_with >= MIN_TEXTS:
            shown = min(
                (w for w in forms if stem(w) == found), key=lambda w: (-forms[w], len(w), w)
            )
            worn.append((-texts_with, shown))
    return tuple(shown for _, shown in sorted(worn))


def worn_problems(text: str, worn: tuple[str, ...]) -> list[Problem]:
    """A problem when the draft uses more than MAX_USED of the worn words."""
    wanted = {stem(w): w for w in worn}
    used = sorted({wanted[stem(w)] for w in _sung_words(text) if stem(w) in wanted})
    if len(used) <= MAX_USED:
        return []
    detail = f"{len(used)} worn-out words ({', '.join(used)}); keep to {MAX_USED} at most"
    return [Problem("worn", "the draft", detail)]
