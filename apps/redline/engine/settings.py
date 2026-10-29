"""Every setting of the chain, with its range, default and a plain-words label for the studio.

The defaults are the picks made by ear so far, so a run with no settings file renders what
the stage rounds chose.
"""

import json
import math
from dataclasses import asdict, dataclass, fields, replace
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
    basic: bool = False


GROUPS = {
    "chain": "Цепочка голоса",
    "eq": "Эквалайзер голоса",
    "voice": "Сам голос",
    "layers": "Слои",
    "fx": "Эффекты",
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
    stutter_on: bool = False
    stutter_ms: float = 90.0
    stutter_repeats: int = 3
    throw_on: bool = False
    throw_db: float = -6.0
    throw_ms: float = 375.0
    throw_feedback: float = 0.4
    throw_tone_hz: float = 4000.0
    reverb_on: bool = False
    reverb_mix_db: float = -16.0
    reverb_decay_s: float = 1.8
    reverb_predelay_ms: float = 25.0
    reverb_tone_hz: float = 5000.0
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
    "stutter_on": _onoff(
        "fx", "Статтер", "В паузе после фразы повторяет её последний кусочек, как заевшая пластинка"
    ),
    "stutter_ms": Spec(
        "fx",
        "Длина кусочка",
        "Сколько от конца фразы повторяется. Меньше — дробнее, как глитч",
        30,
        250,
        5,
        "мс",
    ),
    "stutter_repeats": Spec(
        "fx", "Повторы", "Сколько раз кусочек повторяется, если влезает в паузу", 1, 8, 1, "\u00d7"
    ),
    "throw_on": _onoff(
        "fx", "Дилей на концах фраз", "Последние слова фразы улетают эхом то влево, то вправо"
    ),
    "throw_db": Spec("fx", "Громкость эха", "Насколько первое эхо тише голоса", -24, 0, 0.5, "дБ"),
    "throw_ms": Spec(
        "fx",
        "Шаг эха",
        "Через сколько приходит каждое эхо. Под бит: 60000 / BPM на долю",
        60,
        1000,
        5,
        "мс",
    ),
    "throw_feedback": Spec(
        "fx",
        "Хвост эха",
        "Сколько остаётся от каждого следующего эха. Больше — длиннее хвост",
        0,
        0.85,
        0.05,
        "\u00d7",
    ),
    "throw_tone_hz": Spec(
        "fx", "Тембр эха", "Ниже — эхо темнее и уходит назад", 1000, 12000, 100, "Гц"
    ),
    "reverb_on": _onoff("fx", "Ревер", "Голос в комнате или зале"),
    "reverb_mix_db": Spec(
        "fx",
        "Громкость ревера",
        "Насколько хвост тише голоса. Ближе к нулю — голос дальше",
        -40,
        0,
        0.5,
        "дБ",
    ),
    "reverb_decay_s": Spec(
        "fx",
        "Длина хвоста",
        "За сколько хвост затихает. Больше — зал, меньше — комната",
        0.3,
        6,
        0.1,
        "с",
    ),
    "reverb_predelay_ms": Spec(
        "fx", "Предзадержка", "Пауза перед хвостом: голос остаётся чётким впереди", 0, 150, 1, "мс"
    ),
    "reverb_tone_hz": Spec(
        "fx", "Тембр ревера", "Ниже — хвост темнее и не мешает голосу", 1000, 12000, 100, "Гц"
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

# the settings the studio shows first; the rest wait behind "all settings"
BASIC = (
    "vocal_db",
    "comp_threshold_db",
    "drive_db",
    "eq_fit",
    "hard_tune",
    "formant",
    "octave_down_on",
    "octave_down_db",
    "doubles_on",
    "doubles_db",
    "throw_on",
    "reverb_on",
    "bass_drive_db",
    "master_auto",
    "master_lufs",
)
SPECS = {key: replace(spec, basic=key in BASIC) for key, spec in SPECS.items()}

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
