"""The report of a full run: what was done, in words, and the numbers before and after."""

from collections.abc import Mapping, Sequence
from pathlib import Path

from redline.master.clip import MasterResult
from redline.measure.compare import METRICS
from redline.vocal.chain import ChainResult
from redline.vocal.report import against_refs, stage_1_range

CREST_FLOOR = 1.0
ROWS = (
    ("mix.lufs", "громкость трека", "LUFS"),
    ("mix.plr_db", "пик к громкости", "дБ"),
    ("mix.crest_db", "пик к среднему", "дБ"),
    ("pair.vocal_minus_beat_lu", "голос минус бит", "LU"),
)


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


TIMBRE_WORDS = {
    "octave down": "подложил под голос его же копию на октаву ниже",
    "octave up": "подложил под голос его же копию на октаву выше",
    "doubles": "добавил два «плавающих» дубля по бокам",
    "bitcrush": "пропустил голос через бит-крашер",
    "voice up": "поднял весь голос на октаву",
    "voice down": "опустил весь голос на октаву",
    "nightcore": "поднял голос на октаву вместе с тембром, как найткор",
}


def _timbre_words(part: str) -> str:
    if part.startswith("formant x"):
        ratio = float(part.removeprefix("formant x"))
        return f"сдвинул форманты в {ratio:g} раза ({'моложе' if ratio > 1 else 'грубее'})"
    if part.startswith("hard tune"):
        return f"притянул к нотам хард-тюном ({part.removeprefix('hard tune').strip()})"
    return TIMBRE_WORDS[part]


def _master_line(master: MasterResult | None, before_lufs: float | None, final_lufs: float) -> str:
    if master is None:
        return "- **Мастер:** выключен, громкость не поднимал."
    direction = "поднял" if before_lufs is None or final_lufs > before_lufs else "опустил"
    line = (
        f"- **Мастер:** {direction} громкость до {final_lufs:.1f} LUFS "
        f"(цель {master.target_lufs:.1f}: медианная громкость референсов)"
    )
    if master.clipped_share > 0:
        line += f" клиппингом; срезано {master.clipped_share * 100:.1f} % отсчётов"
    line += "."
    if not master.reached:
        line += " Цель не достигнута: дальше клиппинг только развалит звук."
    return line


def _beat_line(bass_drive_db: float) -> str:
    if bass_drive_db <= 0:
        return "- **Бит:** не трогал."
    return f"- **Бит:** перегрузил 808 на {bass_drive_db:.0f} дБ, громкость низа оставил прежней."


def write_final_report(
    path: Path,
    chain: ChainResult,
    vocal_level: str,
    vocal_db: float,
    timbre: str,
    bass_drive_db: float,
    master: MasterResult | None,
    final_lufs: float,
    before: Mapping[str, float | None],
    after: Mapping[str, float | None],
    refs: Sequence[Mapping[str, float | None]],
) -> None:
    """Write the run's report in Russian; numbers say closer or further, never which is good."""
    eq = ", ".join(f"{g:+.1f}" for g in chain.eq_gains_db)
    timbre_words = "".join(f"{_timbre_words(p.strip())}, " for p in timbre.split("+") if p.strip())
    lines = [
        "# Что сделано с треком",
        "",
        f"- **Вокал:** сжал и перегрузил (уровень `{vocal_level}`), подогнал окраску под вокал "
        f"референсов (эквалайзер, дБ по полосам 200 Гц … 8 кГц: {eq}), {timbre_words}"
        f"{'утопил' if vocal_db < 0 else 'поднял'} на {abs(vocal_db):.0f} дБ "
        f"{'под бит' if vocal_db < 0 else 'против бита'}.",
        _beat_line(bass_drive_db),
        _master_line(master, before.get("mix.lufs"), final_lufs),
        "",
        "## Цифры до и после",
        "",
        "Слово в скобках: где итог против диапазона референсов и сдвинулся ли к нему от "
        "оригинала: «ближе», «дальше», «так же», «в диапазоне», «вне диапазона»; «мало "
        "референсов» и «мало данных», когда сравнивать не с чем. Это не оценка звука: её "
        "даёт только слух.",
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
