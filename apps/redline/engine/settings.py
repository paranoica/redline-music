"""Every setting of the chain, with its range, default and a plain-words label for the studio.

The defaults are the picks made by ear so far, so a run with no settings file renders what
the stage rounds chose.
"""

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

EQ_BANDS_HZ = (200, 400, 1000, 2500, 5000, 8000)


@dataclass(frozen=True)
class Spec:
    """How one setting is shown and checked."""

    group: str
    label: str
    help: str
    low: float = 0.0
    high: float = 1.0
    step: float = 1.0
    unit: str = ""
    choices: tuple[float, ...] = ()


GROUPS = {
    "chain": "Цепочка голоса",
    "eq": "Эквалайзер голоса",
    "voice": "Сам голос",
    "layers": "Слои и эффекты",
    "beat": "Бит",
    "master": "Мастер",
}


@dataclass(frozen=True)
class Settings:
    """One full set of settings; field names are the keys of the settings file."""

    chain_on: bool = True
    comp_threshold_db: float = -38.0
    comp_ratio: float = 30.0
    comp_attack_ms: float = 1.0
    comp_release_ms: float = 40.0
    drive_db: float = 30.0
    clip_on: bool = True
    clip_below_peak_db: float = 12.0
    vocal_db: float = -6.0
    eq_fit: bool = True
    eq_200: float = 0.0
    eq_400: float = 0.0
    eq_1000: float = 0.0
    eq_2500: float = 0.0
    eq_5000: float = 0.0
    eq_8000: float = 0.0
    hard_tune: float = 0.0
    formant: float = 1.0
    voice_octave: int = 0
    nightcore: bool = False
    octave_down_on: bool = True
    octave_down_db: float = -9.5
    octave_up_on: bool = False
    octave_up_db: float = -9.5
    doubles_on: bool = True
    doubles_db: float = -4.0
    doubles_left_ms: float = 18.0
    doubles_right_ms: float = 27.0
    doubles_wander_ms: float = 2.0
    bitcrush_on: bool = False
    bitcrush_bits: int = 8
    bitcrush_hold: int = 3
    beat_db: float = 0.0
    bass_freq_hz: float = 120.0
    bass_drive_db: float = 12.0
    master_on: bool = True
    master_auto: bool = True
    master_lufs: float = -9.0

    @property
    def eq_offsets_db(self) -> tuple[float, ...]:
        """Manual EQ offsets per band, on top of the fit."""
        return tuple(getattr(self, f"eq_{hz}") for hz in EQ_BANDS_HZ)


def _onoff(group: str, label: str, help: str) -> Spec:
    return Spec(group, label, help, 0, 1, 1, "", (0, 1))


SPECS: dict[str, Spec] = {
    "chain_on": _onoff(
        "chain",
        "Цепочка",
        "Сжатие, перегруз и клип голоса. Выключи, чтобы услышать голос до обработки",
    ),
    "comp_threshold_db": Spec(
        "chain",
        "Порог компрессора",
        "С какой громкости голос начинает прижиматься. Ниже — голос ровнее и плотнее",
        -60,
        0,
        1,
        "дБ",
    ),
    "comp_ratio": Spec(
        "chain",
        "Сила сжатия",
        "Во сколько раз гасится громкость выше порога. 30 — почти стена, 2 — мягко",
        1,
        40,
        0.5,
        ":1",
    ),
    "comp_attack_ms": Spec(
        "chain",
        "Атака",
        "Как быстро компрессор хватает удар. Меньше — мягче начала слогов, больше — щелчки",
        0.1,
        50,
        0.1,
        "мс",
    ),
    "comp_release_ms": Spec(
        "chain",
        "Отпускание",
        "Как быстро компрессор отпускает. Меньше — голос дышит и качает, больше — держится ровно",
        5,
        500,
        5,
        "мс",
    ),
    "drive_db": Spec(
        "chain",
        "Перегруз",
        "Сколько голос перегружается после сжатия. Больше — грязнее и злее",
        0,
        40,
        1,
        "дБ",
    ),
    "clip_on": _onoff("chain", "Клип", "Жёстко срезает пики после перегруза"),
    "clip_below_peak_db": Spec(
        "chain",
        "Глубина клипа",
        "Насколько ниже пика режется голос. Больше — сильнее хрустит",
        0,
        24,
        0.5,
        "дБ",
    ),
    "vocal_db": Spec(
        "chain",
        "Голос против бита",
        "Громкость голоса относительно бита. 0 — как в Suno, минус — голос глубже в бите",
        -24,
        6,
        0.5,
        "дБ",
    ),
    "eq_fit": _onoff(
        "eq",
        "Подгонка под референсы",
        "Эквалайзер сам подгоняет окраску голоса под голос референсов",
    ),
    **{
        f"eq_{hz}": Spec(
            "eq",
            f"{hz if hz < 1000 else hz / 1000:g} {'Гц' if hz < 1000 else 'кГц'}",
            help,
            -12,
            12,
            0.5,
            "дБ",
        )
        for hz, help in zip(
            EQ_BANDS_HZ,
            (
                "Низ голоса: тело и бубнёж",
                "Нижняя середина: гнусавость и коробка",
                "Середина: напор и читаемость",
                "Верхняя середина: резкость и присутствие",
                "Верх: чёткость согласных и шипение",
                "Воздух: блеск и свистящие",
            ),
            strict=True,
        )
    },
    "hard_tune": Spec(
        "voice",
        "Хард-тюн",
        "Насколько голос прилипает к нотам. 100 — без скольжения между нотами, роботом",
        0,
        100,
        5,
        "%",
    ),
    "formant": Spec(
        "voice",
        "Форманты",
        "Тембр без смены нот. Больше 1 — моложе и тоньше, меньше 1 — крупнее и грубее",
        0.6,
        1.8,
        0.01,
        "\u00d7",
    ),
    "voice_octave": Spec(
        "voice",
        "Голос по октаве",
        "Весь голос на октаву вниз или вверх, тембр остаётся человеческим",
        -1,
        1,
        1,
        "окт",
        (-1, 0, 1),
    ),
    "nightcore": _onoff(
        "voice", "Найткор", "Голос на октаву выше вместе с тембром, как ускоренная запись"
    ),
    "octave_down_on": _onoff("layers", "Октава вниз", "Слой голоса на октаву ниже под основным"),
    "octave_down_db": Spec(
        "layers", "Громкость октавы вниз", "Насколько слой тише основного голоса", -30, 0, 0.5, "дБ"
    ),
    "octave_up_on": _onoff("layers", "Октава вверх", "Слой голоса на октаву выше под основным"),
    "octave_up_db": Spec(
        "layers",
        "Громкость октавы вверх",
        "Насколько слой тише основного голоса",
        -30,
        0,
        0.5,
        "дБ",
    ),
    "doubles_on": _onoff("layers", "Даблы", "Две копии голоса по сторонам, как второй дубль"),
    "doubles_db": Spec(
        "layers", "Громкость даблов", "Насколько даблы тише основного голоса", -24, 0, 0.5, "дБ"
    ),
    "doubles_left_ms": Spec(
        "layers",
        "Задержка левого",
        "Насколько левый дабл отстаёт. Больше — шире, но слышно как эхо",
        5,
        60,
        1,
        "мс",
    ),
    "doubles_right_ms": Spec(
        "layers", "Задержка правого", "Насколько правый дабл отстаёт", 5, 60, 1, "мс"
    ),
    "doubles_wander_ms": Spec(
        "layers",
        "Плавание даблов",
        "Насколько задержка гуляет. Больше — даблы расстроенее, живее",
        0,
        6,
        0.1,
        "мс",
    ),
    "bitcrush_on": _onoff("layers", "Биткраш", "Цифровая грязь: голос как из старой игры"),
    "bitcrush_bits": Spec("layers", "Биты", "Меньше бит — грубее ступеньки", 3, 16, 1, "бит"),
    "bitcrush_hold": Spec(
        "layers",
        "Удержание",
        "Больше — ниже частота дискретизации, звонче и грязнее",
        1,
        8,
        1,
        "\u00d7",
    ),
    "beat_db": Spec(
        "beat", "Громкость бита", "Весь бит громче или тише, до мастера", -12, 6, 0.5, "дБ"
    ),
    "bass_freq_hz": Spec(
        "beat", "Граница 808", "Что считать 808: всё ниже этой частоты", 60, 250, 5, "Гц"
    ),
    "bass_drive_db": Spec(
        "beat",
        "Перегруз 808",
        "Насколько грязнее 808. Громкость низа остаётся прежней",
        0,
        30,
        1,
        "дБ",
    ),
    "master_on": _onoff("master", "Мастер", "Поднимает громкость трека клиппингом"),
    "master_auto": _onoff(
        "master", "Громкость как у референсов", "Цель громкости: медиана референсов"
    ),
    "master_lufs": Spec(
        "master",
        "Громкость мастера",
        "Цель громкости, если не как у референсов. Громче — плотнее и грязнее",
        -16,
        -5,
        0.1,
        "LUFS",
    ),
}

KINDS: dict[str, type] = {f.name: f.type for f in fields(Settings) if isinstance(f.type, type)}


def _value(key: str, raw: Any) -> Any:
    spec, kind = SPECS[key], KINDS[key]
    if kind is bool:
        if not isinstance(raw, bool):
            raise ValueError(f"{key} takes true or false")
        return raw
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{key} takes a number")
    if not spec.low <= raw <= spec.high:
        raise ValueError(f"{key} takes {spec.low:g} to {spec.high:g}")
    if spec.choices and raw not in spec.choices:
        raise ValueError(f"{key} takes one of {', '.join(f'{c:g}' for c in spec.choices)}")
    if kind is int:
        if raw != int(raw):
            raise ValueError(f"{key} takes a whole number")
        return int(raw)
    return float(raw)


def from_dict(data: dict[str, Any]) -> Settings:
    """Settings from a mapping; keys left out keep their defaults, unknown keys are refused."""
    unknown = sorted(set(data) - set(KINDS))
    if unknown:
        raise ValueError(f"unknown setting {', '.join(unknown)}")
    return Settings(**{key: _value(key, raw) for key, raw in data.items()})


def load(path: Path) -> Settings:
    """Settings from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: settings are a JSON object")
    return from_dict(data)


def save(settings: Settings, path: Path) -> None:
    """Write the settings as JSON, every key spelled out."""
    path.write_text(json.dumps(asdict(settings), indent=1), encoding="utf-8")


def schema() -> dict[str, Any]:
    """Groups, specs and defaults, for the studio page."""
    defaults = asdict(Settings())
    return {
        "groups": GROUPS,
        "settings": [
            {"key": key, "default": defaults[key], "kind": KINDS[key].__name__, **asdict(spec)}
            for key, spec in SPECS.items()
        ],
    }
