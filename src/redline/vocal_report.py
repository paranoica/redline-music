"""Stage 2 report: what each variant did to the vocal, against the references' range."""

import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt

from redline.chain import FIT_FROM_HZ, Target, curve_distance
from redline.compare import FEW_REFS, IN_RANGE, METRICS, NO_DATA, SPREAD_MARGIN
from redline.dsp import block_levels_db
from redline.dynamics import waveform_kurtosis
from redline.loudness import measure_loudness
from redline.spectrum import tonal_curve

Samples = npt.NDArray[np.floating]

OUT_OF_RANGE = "вне диапазона"
CLOSER = "ближе"
FARTHER = "дальше"
SAME = "так же"
GAP_PERCENTILE = 10
LOUD_PERCENTILE = 95

# key in the stage 1 measurements, label, unit
COLUMNS = (
    ("vocal.tilt_db_per_oct", "наклон спектра", "дБ/окт"),
    ("vocal.crest_db", "пик к среднему", "дБ"),
    ("vocal.kurtosis", "острота пиков", ""),
    ("vocal.short_term_spread_lu", "разброс громкости", "LU"),
)


def gap_level_db(samples: Samples, rate: int) -> float:
    """Level of the quiet part of a vocal (pauses, residue) under its loud part, in dB."""
    levels = block_levels_db(samples, rate)
    return float(np.percentile(levels, GAP_PERCENTILE) - np.percentile(levels, LOUD_PERCENTILE))


def _distance(value: float, low: float, high: float) -> float:
    return max(low - value, 0.0, value - high)


def against_refs(
    value: float | None, original: float | None, refs: Sequence[float | None], floor: float
) -> str:
    """Where a value sits against the references' range, and whether it moved toward it.

    The range is stage 1's: half the references' spread plus the metric's floor past either
    end. A move smaller than the floor reads as no move. With no original, only in or out.
    """
    known = [r for r in refs if r is not None and math.isfinite(r)]
    if len(known) < 2:
        return FEW_REFS
    if value is None or not math.isfinite(value):
        return NO_DATA
    margin = SPREAD_MARGIN * (max(known) - min(known)) + floor
    low, high = min(known) - margin, max(known) + margin
    distance = _distance(value, low, high)
    if distance == 0:
        return IN_RANGE
    if original is None or not math.isfinite(original):
        return OUT_OF_RANGE
    before = _distance(original, low, high)
    if abs(distance - before) < floor:
        return SAME
    return CLOSER if distance < before else FARTHER


def _vocal_values(
    vocal: Samples, rate: int, upper: float, target: Target
) -> dict[str, float | None]:
    loud = measure_loudness(vocal, rate)
    curve = tonal_curve(vocal, rate, upper)
    return {
        "vocal.tilt_db_per_oct": curve.tilt_db_per_oct,
        "vocal.crest_db": loud.crest_db,
        "vocal.kurtosis": waveform_kurtosis(vocal, rate),
        "vocal.short_term_spread_lu": loud.short_term_spread_lu,
        "distance": curve_distance(curve, target),
        "gap": gap_level_db(vocal, rate),
    }


def _number(value: float | None) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value:.2f}"


def write_vocal_report(
    path: Path,
    vocals: Mapping[str, Samples],
    rate: int,
    upper: float,
    target: Target,
    refs: Sequence[Mapping[str, float | None]],
    native_rate: int,
) -> None:
    """Write the report; the first entry of vocals is the original."""
    values = {name: _vocal_values(v, rate, upper, target) for name, v in vocals.items()}
    original = next(iter(values.values()))
    head = [label + (f", {unit}" if unit else "") for _, label, unit in COLUMNS]
    lines = [
        "# Вокал: что сделал каждый вариант",
        "",
        "Цифры показывают «ближе / дальше от референсов», а не «лучше»: это решает слух.",
        "Слово в ячейке: где вариант против диапазона референсов (тот же, что на этапе 1) и "
        "сдвинулся ли он к нему по сравнению с оригиналом.",
        "",
        "| вариант | " + " | ".join(head) + " | расстояние кривой, дБ | паузы, дБ |",
        "|---" * (len(COLUMNS) + 3) + "|",
    ]
    for name, row in values.items():
        cells = []
        for key, _, _ in COLUMNS:
            ref_values = [r.get(key) for r in refs]
            base = None if row is original else original[key]
            word = against_refs(row[key], base, ref_values, METRICS[key].floor)
            cells.append(f"{_number(row[key])} ({word})")
        cells += [_number(row["distance"]), _number(row["gap"])]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    ranges = []
    for key, _, _ in COLUMNS:
        known = [v for r in refs if (v := r.get(key)) is not None and math.isfinite(v)]
        ranges.append(f"{min(known):.2f} … {max(known):.2f}" if len(known) >= 2 else "—")
    lines.append("| диапазон референсов | " + " | ".join(ranges) + " | 0 | — |")
    lines += [
        "",
        f"Расстояние кривой: среднее по полосам от {FIT_FROM_HZ:.0f} Гц отличие тональной кривой "
        "вокала от медианы вокала референсов, меньше = ближе.",
        f"Паузы: уровень тихой части вокала ({GAP_PERCENTILE}-й процентиль) под громкой "
        f"({LOUD_PERCENTILE}-й); ниже = тише паузы под вокалом.",
    ]
    if native_rate != rate:
        lines.append(f"Трек пришёл в {native_rate} Гц и обработан в {rate} Гц.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
