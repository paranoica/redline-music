"""Suno versus references: per-metric verdicts and the spec's reference table, row by row."""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

REFS_HIGHER = "у референсов выше"
REFS_LOWER = "у референсов ниже"
IN_RANGE = "в диапазоне"
FEW_REFS = "мало референсов"
NO_DATA = "мало данных"

CONFIRMED = "подтверждено"
REFUTED = "опровергнуто"
UNCONFIRMED = "не подтверждено"
DIFFERENT = "отличается"
NOT_MEASURED = "не измеряется на этапе 1"

SPREAD_MARGIN = 0.5

Values = Mapping[str, float | None]
Expected = Literal["higher", "lower"]


@dataclass(frozen=True)
class Metric:
    """A measured quantity: report label, unit and the smallest difference that counts."""

    label: str
    unit: str
    floor: float


METRICS: dict[str, Metric] = {
    "mix.lufs": Metric("громкость микса", "LUFS", 1.0),
    "mix.plr_db": Metric("пик к громкости (PLR) микса", "dB", 1.0),
    "mix.hot_share": Metric("доля сэмплов выше -1 dBFS (только WAV)", "доля", 0.005),
    "mix.duration_s": Metric("длина", "с", 10.0),
    "mix.band_limit_hz": Metric("верхняя граница спектра", "Hz", 500.0),
    "mix.side_mid_low_db": Metric("стерео микса, низ (side/mid)", "dB", 1.0),
    "mix.side_mid_mid_db": Metric("стерео микса, середина (side/mid)", "dB", 1.0),
    "mix.correlation": Metric("корреляция L/R микса", "", 0.05),
    "mix.tilt_db_per_oct": Metric("наклон спектра микса", "dB/oct", 0.5),
    "vocal.short_term_spread_lu": Metric("разброс мгновенной громкости вокала", "LU", 1.0),
    "vocal.side_mid_mid_db": Metric("стерео вокала (side/mid)", "dB", 1.0),
    "vocal.crest_db": Metric("крест-фактор вокала", "dB", 1.0),
    "vocal.tilt_db_per_oct": Metric("наклон спектра вокала", "dB/oct", 0.5),
    "vocal.kurtosis": Metric("эксцесс волны вокала (перегруз ниже)", "", 0.3),
    "vocal.share_within_10_cents": Metric("доля кадров вокала в ±10 центов от ноты", "доля", 0.05),
    "vocal.median_deviation_cents": Metric("медианное отклонение вокала от ноты", "cents", 3.0),
    "vocal.stable_share": Metric("доля кадров вокала с неподвижной высотой", "доля", 0.05),
    "vocal.tail_decay_db_per_s": Metric("спад хвостов вокала", "dB/s", 5.0),
    "pair.vocal_minus_beat_lu": Metric("громкость вокала минус бит", "LU", 1.0),
    "beat.tilt_db_per_oct": Metric("наклон спектра бита", "dB/oct", 0.5),
    "beat.crest_db": Metric("крест-фактор бита", "dB", 1.0),
    "beat.low_share_db": Metric("доля низа бита (< 100 Hz)", "dB", 1.0),
    "beat.low_crest_db": Metric("крест-фактор низа бита", "dB", 1.0),
    "beat.low_kurtosis": Metric("эксцесс низа бита (перегруз ниже)", "", 0.3),
}


@dataclass(frozen=True)
class MetricVerdict:
    """Where Suno sits against the references for one metric."""

    status: str
    suno: float | None
    refs: tuple[float | None, ...]


def metric_verdict(suno: float | None, refs: Sequence[float | None], floor: float) -> MetricVerdict:
    """Suno is outside the references when it is past [min, max] by half the spread plus floor."""
    known = [r for r in refs if r is not None]
    status = IN_RANGE
    if suno is None or not known:
        status = NO_DATA
    elif len(known) < 2:
        status = FEW_REFS
    else:
        low, high = min(known), max(known)
        margin = SPREAD_MARGIN * (high - low) + floor
        if suno < low - margin:
            status = REFS_HIGHER
        elif suno > high + margin:
            status = REFS_LOWER
    return MetricVerdict(status, suno, tuple(refs))


@dataclass(frozen=True)
class Row:
    """A row of the spec's reference table and the metrics that test it."""

    label: str
    metrics: tuple[tuple[str, Expected], ...] = ()
    rule: Literal["majority", "bimodal", "difference", "none"] = "majority"
    note: str = ""


ROWS: tuple[Row, ...] = (
    Row("Подача вокала", (("vocal.short_term_spread_lu", "higher"),)),
    Row(
        "Обработка вокала",
        (
            ("vocal.side_mid_mid_db", "lower"),
            ("vocal.crest_db", "lower"),
            ("vocal.tilt_db_per_oct", "lower"),
            ("vocal.kurtosis", "lower"),
        ),
    ),
    Row(
        "Автотюн",
        (
            ("vocal.share_within_10_cents", "higher"),
            ("vocal.median_deviation_cents", "lower"),
            ("vocal.stable_share", "higher"),
        ),
    ),
    Row("Голос и бит", (("pair.vocal_minus_beat_lu", "higher"),), rule="bimodal"),
    Row("Бит", (("beat.tilt_db_per_oct", "lower"), ("beat.crest_db", "lower"))),
    Row(
        "Мастер",
        (("mix.lufs", "higher"), ("mix.plr_db", "lower"), ("mix.hot_share", "higher")),
    ),
    Row("Длина трека", (("mix.duration_s", "lower"),)),
    Row(
        "808",
        (
            ("beat.low_share_db", "higher"),
            ("beat.low_crest_db", "lower"),
            ("beat.low_kurtosis", "lower"),
        ),
    ),
    Row(
        "Стерео",
        (
            ("mix.side_mid_low_db", "lower"),
            ("mix.side_mid_mid_db", "lower"),
            ("mix.correlation", "higher"),
        ),
        note="верх не сравнивается: mp3 сам сужает стерео наверху",
    ),
    Row("Эффекты на вокале", (("vocal.tail_decay_db_per_s", "higher"),), rule="difference"),
    Row(
        "Шум и артефакты",
        (("mix.band_limit_hz", "lower"),),
        note="референсы — mp3-рипы, срез верха у них от кодека по определению",
    ),
    Row("Структура", rule="none"),
    Row("Адлибы", rule="none"),
    Row("Тембр вокала", rule="none"),
    Row("Мелодия бита", rule="none"),
    Row("Ударные", rule="none"),
    Row("Лексика и образы", rule="none"),
)


@dataclass(frozen=True)
class RowVerdict:
    """The verdict on one row with the metric verdicts behind it."""

    label: str
    status: str
    metrics: tuple[tuple[str, MetricVerdict], ...]
    note: str


def _verdicts(
    row: Row, suno: Values, refs: Sequence[Values]
) -> tuple[tuple[str, MetricVerdict], ...]:
    return tuple(
        (key, metric_verdict(suno.get(key), [r.get(key) for r in refs], METRICS[key].floor))
        for key, _ in row.metrics
    )


def _majority(row: Row, verdicts: tuple[tuple[str, MetricVerdict], ...]) -> str:
    agree = oppose = available = 0
    for (_, expected), (_, verdict) in zip(row.metrics, verdicts, strict=True):
        if verdict.status in (NO_DATA, FEW_REFS):
            continue
        available += 1
        toward = REFS_HIGHER if expected == "higher" else REFS_LOWER
        away = REFS_LOWER if expected == "higher" else REFS_HIGHER
        agree += verdict.status == toward
        oppose += verdict.status == away
    if available == 0:
        return NO_DATA
    need = available // 2 + 1
    if agree >= need and oppose == 0:
        return CONFIRMED
    if oppose >= need:
        return REFUTED
    return UNCONFIRMED


def _bimodal(key: str, suno: Values, refs: Sequence[Values]) -> str:
    value = suno.get(key)
    known = [r.get(key) for r in refs if r.get(key) is not None]
    if value is None or len(known) < 2:
        return NO_DATA
    far = [abs(r - value) > METRICS[key].floor for r in known if r is not None]
    if all(far):
        return CONFIRMED
    if not any(far):
        return REFUTED
    return UNCONFIRMED


def row_verdicts(suno: Values, refs: Sequence[Values]) -> list[RowVerdict]:
    """Every row of the spec's table judged from the metric values."""
    result = []
    for row in ROWS:
        verdicts = _verdicts(row, suno, refs)
        if row.rule == "none":
            status = NOT_MEASURED
        elif row.rule == "bimodal":
            status = _bimodal(row.metrics[0][0], suno, refs)
        elif row.rule == "difference":
            status = verdicts[0][1].status
            status = DIFFERENT if status in (REFS_HIGHER, REFS_LOWER) else status
        else:
            status = _majority(row, verdicts)
        result.append(RowVerdict(row.label, status, verdicts, row.note))
    return result


@dataclass(frozen=True)
class Disagreement:
    """References spread wider than their distance to Suno on most metrics."""

    metrics: int
    of: int
    outlier: int


def disagreement(suno: Values, refs: Sequence[Values]) -> Disagreement | None:
    """Warn when references disagree more than they differ from Suno; name the odd one out."""
    wide = compared = 0
    distance = [0.0] * len(refs)
    for key, metric in METRICS.items():
        value, known = suno.get(key), [r.get(key) for r in refs]
        values = [v for v in known if v is not None]
        if value is None or len(values) < 2 or len(values) != len(refs):
            continue
        compared += 1
        spread = max(values) - min(values)
        wide += spread > abs(value - statistics.fmean(values))
        middle = statistics.median(values)
        for i, v in enumerate(values):
            distance[i] += abs(v - middle) / max(spread, metric.floor)
    if compared == 0 or wide * 2 <= compared:
        return None
    return Disagreement(wide, compared, max(range(len(refs)), key=distance.__getitem__))
