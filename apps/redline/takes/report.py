"""The takes ranking as a table, a report.md and a ranking.json."""

import json
import math
import shlex
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from redline.takes.rank import (
    BLEED_FLAG,
    BLEED_POINTS,
    BLEED_TOLERANCE,
    CLIP_LEVEL,
    CLIP_MAX_POINTS,
    CLIP_POINTS,
    CLIP_RUN,
    DEEP_POINTS,
    DROPOUT_MAX_POINTS,
    DROPOUT_POINTS,
    FLOOR_STEP_DB,
    FLOOR_TOLERANCE_DB,
    LONGER_S,
    TIMING_STEP_MS,
    TIMING_TOLERANCE_MS,
    RankedTake,
    Ranking,
    Skip,
)

SCORE = (
    "penalty points, fewer rank higher; ties go to the tighter timing, then the name. "
    f"timing: 1 per {TIMING_STEP_MS:g} ms of median onset deviation from the median of all "
    f"takes past {TIMING_TOLERANCE_MS:g} ms; clipping: {CLIP_POINTS:g} per run of "
    f"{CLIP_RUN}+ samples at {CLIP_LEVEL} of full scale or more, at most "
    f"{CLIP_MAX_POINTS:g}; noise: 1 per {FLOOR_STEP_DB:g} dB the gap floor sits above "
    f"{FLOOR_TOLERANCE_DB:g} dB under the voice; dropouts: {DROPOUT_POINTS:g} each, at most "
    f"{DROPOUT_MAX_POINTS:g}; bleed: {BLEED_POINTS:g} per unit of take-beat correlation "
    f"above {BLEED_TOLERANCE:g}; deep: {DEEP_POINTS:g} per unit of auto-search distance. "
    "Level spread and the grid offset are not scored."
)
SCORE_RU = [
    "Баллы штрафные: меньше — выше место; при равенстве — точнее тайминг, потом имя.",
    "",
    f"- тайминг: 1 балл за каждые {TIMING_STEP_MS:g} мс медианного отклонения атак от "
    f"медианы всех дублей сверх {TIMING_TOLERANCE_MS:g} мс;",
    f"- клип: {CLIP_POINTS:g} за серию из {CLIP_RUN}+ сэмплов на уровне {CLIP_LEVEL} от "
    f"полной шкалы и выше, не больше {CLIP_MAX_POINTS:g};",
    f"- шум: 1 балл за каждые {FLOOR_STEP_DB:g} дБ, на которые пол в паузах выше "
    f"{FLOOR_TOLERANCE_DB:g} дБ под голосом;",
    f"- выпадения: {DROPOUT_POINTS:g} за каждое из лога записи, не больше "
    f"{DROPOUT_MAX_POINTS:g};",
    f"- бит в микрофоне: {BLEED_POINTS:g} за единицу корреляции дубля с битом сверх "
    f"{BLEED_TOLERANCE:g};",
    f"- с --deep: {DEEP_POINTS:g} за единицу расстояния автоподбора до референсов.",
    "",
    "Разброс громкости и сдвиг от сетки в баллы не входят.",
]


def _num(value: float | int | None, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def _row(take: RankedTake, deep: bool) -> list[str]:
    m = take.metrics
    cells = [
        _num(take.rank),
        take.name,
        _num(take.total),
        _num(m.timing_ms),
        _num(m.grid_ms),
        _num(m.clip_runs),
        _num(m.floor_below_voice_db),
        "?" if m.dropouts is None else f"{m.dropouts} / {m.dropout_ms:.0f} ms",
        f"{m.bleed:.2f}{' !' if take.headphones else ''}",
        _num(m.level_spread_lu),
    ]
    return [*cells, _num(m.deep_distance, 2)] if deep else cells


def _deep(ranking: Ranking) -> bool:
    return any(t.metrics.deep_distance is not None for t in ranking.takes)


def table(ranking: Ranking) -> list[str]:
    """The ranked table for the terminal, one string per line."""
    head = ["rank", "take", "points", "timing ms", "grid ms", "clips", "floor dB"]
    head += ["dropouts", "bleed", "spread LU"]
    deep = _deep(ranking)
    rows = [head + (["deep"] if deep else [])]
    rows += [_row(take, deep) for take in ranking.takes]
    widths = [max(len(row[c]) for row in rows) for c in range(len(rows[0]))]
    return ["  ".join(cell.rjust(w) for cell, w in zip(row, widths, strict=True)) for row in rows]


def voice_command(take: Path, beat: Path, refs: Sequence[Path]) -> str:
    """The `redline voice` command on a take, with the references or a placeholder for them."""
    line = shlex.join(["uv", "run", "redline", "voice", "--vocal", str(take), "--beat", str(beat)])
    refs_part = shlex.join(["--ref", *map(str, refs)]) if refs else "--ref <references>"
    return f"{line} {refs_part}"


def _finite(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(v) for v in value]
    return value


def ranking_json(ranking: Ranking, voice: str | None) -> object:
    """Everything the ranking found, as JSON-ready data; non-finite numbers become None."""
    return _finite(
        {
            "beat": str(ranking.beat),
            "rate": ranking.rate,
            "ranked": ranking.ranked,
            "score": SCORE,
            "grid": {"bpm": ranking.grid.bpm, "first_beat_s": next(iter(ranking.grid.beats), None)},
            "takes": [
                {
                    "name": t.name,
                    "file": str(t.path),
                    "rank": t.rank,
                    "total": t.total,
                    "points": t.points,
                    "headphones": t.headphones,
                    "metrics": asdict(t.metrics),
                }
                for t in ranking.takes
            ],
            "skipped": [
                {"name": s.name, "code": s.code, "reason": s.reason, "values": s.values}
                for s in ranking.skips
            ],
            "voice": voice,
        }
    )


def _summary(ranking: Ranking) -> list[str]:
    if not ranking.takes:
        return ["Ни один дубль не подошёл для замеров — причины ниже."]
    if not ranking.ranked:
        return [
            f"Один дубль ({ranking.takes[0].name}) — сравнивать не с чем, места нет. "
            "Тайминг без других дублей не считается."
        ]
    lines = [
        f"Первый по баллам: **{ranking.takes[0].name}** — чище и ровнее по таймингу с другими "
        "дублями по замерам. Цифры говорят «чище» и «ближе», а не о том, как дубль звучит: "
        "решают уши."
    ]
    if len(ranking.takes) == 2:
        lines.append(
            "Дублей два: по таймингу не понять, какой из них сбился, — расхождение делится "
            "поровну."
        )
    return lines


def _markdown_table(ranking: Ranking) -> list[str]:
    head = [
        "Место",
        "Дубль",
        "Баллы",
        "Тайминг к другим, мс",
        "К сетке, мс",
        "Клип, серий",
        "Шум в паузах к голосу, дБ",
        "Выпадения",
        "Бит в микрофоне",
        "Разброс громкости, LU",
    ]
    deep = _deep(ranking)
    if deep:
        head.append("Расстояние до референсов")
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_row(t, deep)) + " |" for t in ranking.takes]
    return lines


def skip_reason_ru(skip: Skip) -> str:
    """Why a take was left out, in plain Russian."""
    v = skip.values
    match skip.code:
        case "other_rate":
            return f"частота {v['rate']} Гц, у остальных дублей {v['others']} Гц"
        case "silent":
            return "дубль пустой или почти без звука"
        case "too_long":
            return (
                f"дубль {v['seconds']:.1f} с, бит {v['beat_seconds']:.1f} с: дубль длиннее "
                f"бита больше чем на {LONGER_S:g} с"
            )
        case "unreadable":
            return f"файл не удалось прочитать ({skip.reason})"
        case _:
            return skip.reason


def _notes(ranking: Ranking) -> list[str]:
    lines = []
    for take in ranking.takes:
        if take.headphones:
            lines.append(
                f"- {take.name}: бит слышен в микрофоне (корреляция {take.metrics.bleed:.2f}, "
                f"порог {BLEED_FLAG:g}) — нужны наушники."
            )
        if take.metrics.dropouts is None:
            lines.append(
                f"- {take.name}: выпадения неизвестны — нет журнала дубля, он не читается или "
                f"запись не завершилась. Засчитано как худшее: {DROPOUT_MAX_POINTS:g} баллов."
            )
        if take.metrics.short:
            beat = f", бит {ranking.beat_seconds:.1f} с" if ranking.beat_seconds else ""
            lines.append(
                f"- {take.name}: дубль короткий, {take.metrics.seconds:.1f} с{beat}. "
                "Замерен только по участку, где он совпадает с битом и другими дублями, и "
                "стоит ниже всех полных дублей: на коротком куске меньше событий, и он "
                "выглядит чище, чем есть."
            )
        lines += [f"- {take.name}: {note}" for note in take.metrics.notes]
    lines += [f"- {s.name}: пропущен — {skip_reason_ru(s)}" for s in ranking.skips]
    return lines


def report_markdown(ranking: Ranking, song: str, voice: str | None) -> str:
    """The report a person reads: summary, table, notes, how the points are counted."""
    grid = ranking.grid
    tempo = f"{grid.bpm:.1f} BPM" if grid.bpm else "не найден"
    lines = [
        f"# Дубли: {song}",
        "",
        f"Бит: {ranking.beat.name}, темп {tempo}. Сетка — только для справки: тайминг "
        "считается против других дублей, так что одинаково оттянутый флоу не штрафуется.",
        "",
        *_summary(ranking),
        "",
        *_markdown_table(ranking),
        "",
        "«!» у бита в микрофоне — выше порога, нужны наушники. «К сетке» — средний сдвиг "
        "атак от ближайшей шестнадцатой, плюс — позже. Разброс громкости — межквартильный "
        "размах моментальной громкости, в баллы не входит.",
    ]
    notes = _notes(ranking)
    if notes:
        lines += ["", "## Заметки", "", *notes]
    lines += ["", "## Как считаются баллы", "", *SCORE_RU]
    if voice:
        lines += ["", "## Дальше", "", "```", voice, "```"]
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: Path, ranking: Ranking, song: str, voice: str | None) -> Path:
    """Write report.md and ranking.json into the folder and return the report's path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "report.md"
    report.write_text(report_markdown(ranking, song, voice), encoding="utf-8")
    (out_dir / "ranking.json").write_text(
        json.dumps(ranking_json(ranking, voice), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report
