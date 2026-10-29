"""Whether a Russian word looks like a verb, by its ending."""

import re

from redline.lyrics.text.normalize import words

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
