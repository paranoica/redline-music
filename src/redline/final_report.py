"""The report of a full run: what was done, in words, and the numbers before and after."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from redline.chain import ChainResult
from redline.compare import METRICS
from redline.master import MasterResult
from redline.vocal_report import against_refs, stage_1_range

CREST_FLOOR = 1.0
ROWS = (
    ("mix.lufs", "громкость трека", "LUFS"),
    ("mix.plr_db", "пик к громкости", "дБ"),
    ("mix.crest_db", "пик к среднему", "дБ"),
    ("pair.vocal_minus_beat_lu", "голос минус бит", "LU"),
)


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def write_final_report(
    path: Path,
    chain: ChainResult,
    vocal_level: str,
    vocal_db: float,
    master: MasterResult,
    before: Mapping[str, float | None],
    after: Mapping[str, float | None],
    refs: Sequence[Mapping[str, float | None]],
) -> None:
    """Write the run's report in Russian; numbers say closer or further, never better."""
    eq = ", ".join(f"{g:+.1f}" for g in chain.eq_gains_db)
    lines = [
        "# Что сделано с треком",
        "",
        f"- **Вокал:** сжал и перегрузил (уровень `{vocal_level}`), подогнал окраску под вокал "
        f"референсов (эквалайзер, дБ по полосам 200 Гц … 8 кГц: {eq}), "
        f"утопил на {abs(vocal_db):.0f} дБ под бит.",
        "- **Бит:** не трогал.",
        f"- **Мастер:** поднял громкость до {master.target_lufs:.1f} LUFS (средняя громкость "
        f"референсов) клиппингом; срезано {master.clipped_share * 100:.1f} % отсчётов."
        + ("" if master.reached else " Цель не достигнута: дальше только развал звука."),
        "",
        "## Цифры до и после",
        "",
        "Слово в скобках: где итог против референсов и сдвинулся ли к ним от оригинала "
        "(«ближе / дальше / так же / в диапазоне»). Это не «лучше»: это решает слух.",
        "",
        "| замер | оригинал | итог | референсы с запасом этапа 1 |",
        "|---|---|---|---|",
    ]
    for key, label, unit in ROWS:
        floor = METRICS[key].floor if key in METRICS else CREST_FLOOR
        ref_values = [r.get(key) for r in refs]
        word = against_refs(after.get(key), before.get(key), ref_values, floor)
        bounds = stage_1_range(ref_values, floor)
        span = "—" if bounds is None else f"{bounds[0]:.1f} … {bounds[1]:.1f}"
        lines.append(
            f"| {label}, {unit} | {_number(before.get(key))} | "
            f"{_number(after.get(key))} ({word}) | {span} |"
        )
    lines += [
        "",
        "`original.wav` приведён к громкости `final.wav` и может заходить выше 0 дБ: "
        "слушай его не на полной громкости. Пара в `compare/` — та же, но на безопасной "
        "одинаковой громкости, для честного «до и после».",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
