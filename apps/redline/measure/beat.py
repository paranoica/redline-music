"""Beat-only measuring: a clean beat render against the beat stems of the references.

No vocal metrics. The references go through the separator and its stem cache; the render is
measured as it is, so the report says the two sides differ in kind.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redline.core.audio import Audio, Samples, decode, to_analysis
from redline.core.dynamics import low_end_kurtosis
from redline.core.private import write_private
from redline.core.spectrum import low_end
from redline.measure.compare import (
    FEW_REFS,
    IN_RANGE,
    METRICS,
    NO_DATA,
    REFS_HIGHER,
    REFS_LOWER,
    SPREAD_MARGIN,
    Metric,
    metric_verdict,
)
from redline.measure.metrics import finite
from redline.measure.run import _band_limits, _cache_dir, _stem_values
from redline.measure.separate import Separator, separate

BEAT_METRICS: dict[str, Metric] = {
    "beat.lufs": Metric("громкость бита", "LUFS", METRICS["mix.lufs"].floor),
    "beat.crest_db": METRICS["beat.crest_db"],
    "beat.tilt_db_per_oct": METRICS["beat.tilt_db_per_oct"],
    "beat.side_mid_low_db": Metric(
        "стерео бита, низ (side/mid)", "dB", METRICS["mix.side_mid_low_db"].floor
    ),
    "beat.side_mid_mid_db": Metric(
        "стерео бита, середина (side/mid)", "dB", METRICS["mix.side_mid_mid_db"].floor
    ),
    "beat.correlation": Metric("корреляция L/R бита", "", METRICS["mix.correlation"].floor),
    "beat.low_share_db": METRICS["beat.low_share_db"],
    "beat.low_crest_db": METRICS["beat.low_crest_db"],
    "beat.low_kurtosis": METRICS["beat.low_kurtosis"],
}
STATUS = {
    IN_RANGE: "inside",
    REFS_HIGHER: "refs_higher",
    REFS_LOWER: "refs_lower",
    FEW_REFS: "few_refs",
    NO_DATA: "no_data",
}
SHOWN = {
    "inside": "внутри",
    "refs_higher": "вне, у референсов выше",
    "refs_lower": "вне, у референсов ниже",
    "few_refs": "мало референсов",
    "no_data": "мало данных",
}
TRENDS = {"closer": "ближе", "further": "дальше", "same": "так же", None: "—"}


def beat_values(
    samples: Samples, rate: int, upper: float | None = None, side_upper: float | None = None
) -> dict[str, dict[str, Any]]:
    """The beat stem values and info of one signal.

    Args:
        samples: The beat, (frames, 2).
        rate: Its rate.
        upper: The highest frequency compared (default: half the rate).
        side_upper: The highest frequency of the side compared (default: `upper`).

    Returns:
        `{"values": {...}, "info": {...}}` keyed `beat.*`.
    """
    upper = rate / 2 if upper is None else upper
    side_upper = upper if side_upper is None else side_upper
    stem = _stem_values("beat", samples, rate, upper, side_upper)
    low = low_end(samples, rate)
    values = {
        **stem["values"],
        "beat.low_share_db": low.share_db,
        "beat.low_crest_db": low.crest_db,
        "beat.low_kurtosis": low_end_kurtosis(samples, rate),
    }
    return {
        "values": {k: finite(v) for k, v in values.items()},
        "info": {k: finite(v) if isinstance(v, float) else v for k, v in stem["info"].items()},
    }


@dataclass(frozen=True)
class BeatMeasured:
    """The render's beat values and each reference's beat stem values."""

    values: dict[str, float | None]
    info: dict[str, Any]
    refs: tuple[tuple[str, dict[str, float | None]], ...]
    upper: float
    separator: str


def measure_beat(
    render: Path, refs: Sequence[Path], work_dir: Path, separator: Separator
) -> BeatMeasured:
    """Measure a clean beat and the beat stems of the references.

    Args:
        render: The beat, unseparated.
        refs: The reference tracks; separated through the stem cache under `work_dir`.
        work_dir: The folder of the caches.
        separator: Separates the references.

    Raises:
        ValueError: A file cannot be measured.
    """
    decoded = [decode(p) for p in [render, *refs]]
    analysed: list[Audio] = [to_analysis(d) for d in decoded]
    upper, side_upper = _band_limits(analysed)
    cache_dir = _cache_dir(work_dir)
    own = beat_values(analysed[0].samples, analysed[0].rate, upper, side_upper)
    measured = []
    for path, audio in zip(refs, analysed[1:], strict=True):
        try:
            stems = separate(audio, separator, cache_dir)
            values = beat_values(stems.beat, stems.rate, upper, side_upper)["values"]
        except ValueError as error:
            raise ValueError(f"{path.name}: {error}") from error
        measured.append((path.name, values))
    return BeatMeasured(own["values"], own["info"], tuple(measured), upper, separator.id)


def _outside(value: float | None, refs: list[float]) -> float | None:
    if value is None or not refs:
        return None
    return max(min(refs) - value, value - max(refs), 0.0)


def _tolerance(refs: list[float], floor: float) -> float | None:
    """How far past [min, max] a value may sit and still count as inside, as metric_verdict."""
    if len(refs) < 2:
        return None
    return SPREAD_MARGIN * (max(refs) - min(refs)) + floor


def _trend(now: float | None, before: Any, floor: float) -> str | None:
    if now is None or not isinstance(before, int | float) or not math.isfinite(before):
        return None
    if abs(now - before) < floor:
        return "same"
    return "closer" if now < before else "further"


def compare_beat(measured: BeatMeasured, previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Per metric: the render's value, the refs' range, where it sits and its trend.

    The trend says whether the render moved closer to the refs' range than the previous
    measure (only one taken against the same references), never whether it sounds better.
    """
    before = {}
    if previous is not None:
        before = {row["key"]: row.get("outside") for row in previous.get("metrics", [])}
    rows = []
    for key, metric in BEAT_METRICS.items():
        value = measured.values.get(key)
        refs = [v.get(key) for _, v in measured.refs]
        known = [r for r in refs if r is not None and math.isfinite(r)]
        verdict = metric_verdict(value, refs, metric.floor)
        outside = _outside(value, known)
        rows.append(
            {
                "key": key,
                "label": metric.label,
                "unit": metric.unit,
                "render": value,
                "refs": refs,
                "refs_min": min(known, default=None),
                "refs_max": max(known, default=None),
                "tolerance": _tolerance(known, metric.floor),
                "status": STATUS[verdict.status],
                "outside": outside,
                "trend": _trend(outside, before.get(key), metric.floor),
            }
        )
    return rows


def _number(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.2f}"


def write_beat_report(folder: Path, measured: BeatMeasured, label: str) -> tuple[Path, Path]:
    """Write `measure.md` and `measure.json` into the folder, trends against the last ones.

    Args:
        folder: The render's folder; a `measure.json` there is the previous measure.
        measured: The measurements.
        label: What was rendered, e.g. the score file name.

    Returns:
        The Markdown and the JSON paths.
    """
    names = [name for name, _ in measured.refs]
    json_path, md_path = folder / "measure.json", folder / "measure.md"
    previous = None
    if json_path.is_file():
        try:
            old = json.loads(json_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            old = None
        # a trend only means something against the same references
        if (
            isinstance(old, dict)
            and old.get("refs") == names
            and old.get("separator") == measured.separator
        ):
            previous = old
    rows = compare_beat(measured, previous)
    payload = {
        "render": label,
        "previous": None if previous is None else previous.get("render"),
        "separator": measured.separator,
        "compared_up_to_hz": measured.upper,
        "refs": names,
        "metrics": rows,
        "info": measured.info,
    }
    write_private(json_path, json.dumps(payload, ensure_ascii=False, indent=1))
    against = f" (против прошлого замера: {payload['previous']})" if previous else ""
    lines = [
        f"# Бит против референсов: {label}",
        "",
        f"Референсы ({len(names)}): {', '.join(names)}.",
        f"Сравнение до {measured.upper:.0f} Hz.",
        "",
        f"Референсы — бит, отделённый от вокала сепаратором ({measured.separator}); рендер — "
        "чистый бит без сепарации. Сепарация срезает часть верха и низа, так что наклон "
        "спектра и числа по низу у референсов смещены, и сравнивать их надо с этой поправкой.",
        "",
        "Числа показывают только, ближе ли бит к референсам или дальше от них.",
        "",
        f"| Метрика | Рендер | Референсы, мин…макс | Допуск, ± | Где | Тренд{against} |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        unit = f", {row['unit']}" if row["unit"] else ""
        span = f"{_number(row['refs_min'])}…{_number(row['refs_max'])}"
        tolerance = "—" if row["tolerance"] is None else f"±{_number(row['tolerance'])}"
        lines.append(
            f"| {row['label']}{unit} | {_number(row['render'])} | {span} | {tolerance} | "
            f"{SHOWN[row['status']]} | {TRENDS[row['trend']]} |"
        )
    lines += [
        "",
        "«Внутри» значит, что рендер выходит за мин…макс референсов не дальше допуска: "
        "допуск — половина разброса референсов (макс - мин) плюс порог метрики, так что "
        "значение чуть ниже минимума или выше максимума тоже считается внутри.",
    ]
    write_private(md_path, "\n".join(lines) + "\n")
    return md_path, json_path
