"""The run report in Russian: the spec's table row by row, then every measurement."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from redline.measure.compare import METRICS, SPREAD_MARGIN, Disagreement, RowVerdict, metric_verdict

INFO_LABELS = {
    "mix.lra_lu": ("диапазон громкости микса (LRA)", "LU"),
    "mix.short_term_spread_lu": ("разброс мгновенной громкости микса", "LU"),
    "mix.sample_peak_db": ("пик сэмпла микса", "dBFS"),
    "mix.true_peak_db": ("true peak микса", "dBTP"),
    "mix.tempo_candidates": ("темп, кандидаты", "BPM"),
    "mix.key": ("тональность (ненадёжно)", ""),
    "vocal.lra_lu": ("диапазон громкости вокала (LRA)", "LU"),
    "beat.lra_lu": ("диапазон громкости бита (LRA)", "LU"),
    "vocal.tuning_cents": ("строй вокала относительно A440", "cents"),
    "vocal.voiced_frames": ("кадров вокала с высотой", ""),
    "vocal.tails": ("концов фраз найдено", ""),
    "vocal.tail_decay_all_db_per_s": ("спад хвостов по всем найденным", "dB/s"),
}

STEMS = {"mix": "микса", "vocal": "вокала", "beat": "бита"}
QUANTITIES = {
    "lufs": ("громкость", "LUFS"),
    "crest_db": ("крест-фактор", "dB"),
    "tilt_db_per_oct": ("наклон спектра", "dB/oct"),
    "side_mid_low_db": ("стерео, низ (side/mid)", "dB"),
    "side_mid_mid_db": ("стерео, середина (side/mid)", "dB"),
    "correlation": ("корреляция L/R", ""),
}


class Measured(Protocol):
    """What the report needs from one measured file."""

    name: str
    codec: str
    native_rate: int
    cached: bool
    cpu_fallback: bool
    values: dict[str, float | None]
    info: dict[str, Any]


def _number(value: Any, unit: str) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(_number(v, unit) for v in value) or "—"
    if isinstance(value, bool | str | int):
        return str(value)
    if unit == "доля":
        percent = value * 100
        return f"{percent:.1f}%" if 0 < abs(percent) < 10 else f"{percent:.0f}%"
    if unit in ("Hz", "с", "BPM", "") and abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}" if unit else f"{value:.2f}"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _rows_section(rows: Sequence[RowVerdict]) -> list[str]:
    body = []
    for row in rows:
        basis = "; ".join(f"{METRICS[key].label}: {v.status}" for key, v in row.metrics)
        if row.note:
            basis = f"{basis} ({row.note})" if basis else row.note
        body.append([row.label, row.status, basis or "—"])
    return _table(["Строка из ТЗ", "Вердикт", "На чём основано"], body)


def _metrics_section(track: Measured, refs: Sequence[Measured]) -> list[str]:
    body = []
    for key, metric in METRICS.items():
        suno, values = track.values.get(key), [r.values.get(key) for r in refs]
        verdict = metric_verdict(suno, values, metric.floor)
        cells = [_number(v, metric.unit) for v in (suno, *values)]
        unit = f", {metric.unit}" if metric.unit and metric.unit != "доля" else ""
        floor = _number(metric.floor, metric.unit)
        spread = _range(values, metric.unit)
        body.append([f"{metric.label}{unit}", *cells, spread, floor, verdict.status])
    header = ["Замер", track.name, *(r.name for r in refs), "диапазон референсов"]
    return _table([*header, "порог", "вердикт"], body)


def _range(values: Sequence[float | None], unit: str) -> str:
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return "—"
    return f"{_number(min(known), unit)} … {_number(max(known), unit)}"


def _other_values_section(track: Measured, refs: Sequence[Measured]) -> list[str]:
    body = []
    for stem, stem_label in STEMS.items():
        for quantity, (label, unit) in QUANTITIES.items():
            key = f"{stem}.{quantity}"
            if key in METRICS:
                continue
            values = [r.values.get(key) for r in refs]
            cells = [_number(v, unit) for v in (track.values.get(key), *values)]
            name = f"{label} {stem_label}" + (f", {unit}" if unit else "")
            body.append([name, *cells, _range(values, unit)])
    header = ["Без вердикта", track.name, *(r.name for r in refs), "диапазон референсов"]
    return _table(header, body)


def _info_section(track: Measured, refs: Sequence[Measured]) -> list[str]:
    body = []
    for key, (label, unit) in INFO_LABELS.items():
        cells = [_number(f.info.get(key), unit) for f in (track, *refs)]
        body.append([f"{label}, {unit}" if unit else label, *cells])
    return _table(["Справочно", track.name, *(r.name for r in refs)], body)


def _header(track: Measured, refs: Sequence[Measured], upper: float) -> list[str]:
    files = ", ".join(f"{r.name} ({r.codec}, {r.native_rate} Hz)" for r in refs)
    cached = [f.name for f in (track, *refs) if f.cached]
    lines = [
        "# Redline: трек против референсов",
        "",
        f"- Трек: {track.name} ({track.codec}, {track.native_rate} Hz).",
        f"- Референсы: {files}.",
        f"- Спектры сравниваются до {upper:.0f} Hz: самая низкая граница среди файлов.",
    ]
    if cached:
        lines.append(f"- Из кеша, без пересчёта: {', '.join(cached)}.")
    fell_back = [f.name for f in (track, *refs) if f.cpu_fallback]
    if fell_back:
        lines.append(
            f"- Видеокарта не справилась, разделение шло на процессоре: {', '.join(fell_back)}."
        )
    if any(r.codec in ("mp3", "ogg") for r in refs):
        lines.append(
            "- Референсы сжатые: доля громких сэмплов у них не считается, "
            "true peak после декодирования завышен."
        )
    return lines


def write_report(
    path: Path,
    track: Measured,
    refs: Sequence[Measured],
    rows: Sequence[RowVerdict],
    warning: Disagreement | None,
    upper: float,
) -> None:
    """Write the Markdown report of one run."""
    lines = _header(track, refs, upper)
    if warning is not None:
        lines.append(
            f"- **Референсы расходятся между собой**: на {warning.metrics} из {warning.of} "
            f"замеров разброс между ними больше, чем расстояние до трека. Сильнее всех "
            f"выбивается {refs[warning.outlier].name}: попробуй без него или с одним референсом."
        )
    lines += ["", "## Таблица из ТЗ", "", *_rows_section(rows)]
    lines += ["", "## Все замеры", "", *_metrics_section(track, refs)]
    lines += ["", "## Прочие замеры", "", *_other_values_section(track, refs)]
    lines += ["", "## Справочно", "", *_info_section(track, refs)]
    lines += ["", "## Как читать", "", *_reading_notes()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reading_notes() -> list[str]:
    return [
        f"- Трек «вне диапазона», если он дальше от крайнего референса, чем половина "
        f"разброса референсов ({SPREAD_MARGIN}) плюс порог замера.",
        "- Строка «подтверждено», когда большинство её замеров показывает в сторону из ТЗ "
        "и ни один не против; «опровергнуто», когда большинство против.",
        "- Цифры говорят «ближе или дальше от референсов», а не «лучше». "
        "Лучше или нет, решают уши, и слушать надо на одинаковой громкости.",
    ]
