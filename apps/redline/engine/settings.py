"""Every setting of the chain, with its range, default and a plain-words label for the studio.

The defaults are the picks made by ear so far, so a run with no settings file renders what
the stage rounds chose.
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from redline.core import native

EQ_BANDS_HZ = (200, 400, 1000, 2500, 5000, 8000)
PRESET_NAME = re.compile(r"[\w \-]{1,40}")
# version 2 added the effects rack; a file without a version is version 1
VERSION = 2


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
    stage: str = field(kw_only=True)


@dataclass(frozen=True)
class AutomationLane:
    """One param of a rack effect moving over the track: (seconds, value) points, linear between.

    Before the first point the param holds the first value, after the last the last one.
    """

    param: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class RackEffect:
    """One effect of the rack: its params as sorted (name, value) pairs, and its lanes.

    A choice param's value is the choice's name or its index.
    """

    effect: str
    on: bool = True
    params: tuple[tuple[str, float | str], ...] = ()
    automation: tuple[AutomationLane, ...] = ()


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
    rack: tuple[RackEffect, ...] = ()

    @property
    def eq_offsets_db(self) -> tuple[float, ...]:
        """Manual EQ offsets per band, on top of the fit."""
        return tuple(getattr(self, f"eq_{hz}") for hz in EQ_BANDS_HZ)


def _onoff(group: str, label: str, help: str, stage: str) -> Spec:
    return Spec(group, label, help, 0, 1, 1, "", (0, 1), stage=stage)


SPECS: dict[str, Spec] = {
    "chain_on": _onoff(
        "chain",
        "Цепочка",
        "Сжатие, перегруз и клип голоса. Выключи, чтобы услышать голос до обработки",
        stage="chain",
    ),
    "comp_threshold_db": Spec(
        "chain",
        "Порог компрессора",
        "С какой громкости голос начинает прижиматься. Ниже — голос ровнее и плотнее",
        -60,
        0,
        1,
        "дБ",
        stage="chain",
    ),
    "comp_ratio": Spec(
        "chain",
        "Сила сжатия",
        "Во сколько раз гасится громкость выше порога. 30 — почти стена, 2 — мягко",
        1,
        40,
        0.5,
        ":1",
        stage="chain",
    ),
    "comp_attack_ms": Spec(
        "chain",
        "Атака",
        "Как быстро компрессор хватает удар. Меньше — мягче начала слогов, больше — щелчки",
        0.1,
        50,
        0.1,
        "мс",
        stage="chain",
    ),
    "comp_release_ms": Spec(
        "chain",
        "Отпускание",
        "Как быстро компрессор отпускает. Меньше — голос дышит и качает, больше — держится ровно",
        5,
        500,
        5,
        "мс",
        stage="chain",
    ),
    "drive_db": Spec(
        "chain",
        "Перегруз",
        "Сколько голос перегружается после сжатия. Больше — грязнее и злее",
        0,
        40,
        1,
        "дБ",
        stage="chain",
    ),
    "clip_on": _onoff("chain", "Клип", "Жёстко срезает пики после перегруза", stage="chain"),
    "clip_below_peak_db": Spec(
        "chain",
        "Глубина клипа",
        "Насколько ниже пика режется голос. Больше — сильнее хрустит",
        0,
        24,
        0.5,
        "дБ",
        stage="chain",
    ),
    "vocal_db": Spec(
        "chain",
        "Голос против бита",
        "Громкость голоса относительно бита. 0 — как в Suno, минус — голос глубже в бите",
        -24,
        6,
        0.5,
        "дБ",
        stage="voice",
    ),
    "eq_fit": _onoff(
        "eq",
        "Подгонка под референсы",
        "Эквалайзер сам подгоняет окраску голоса под голос референсов",
        stage="chain",
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
            stage="chain",
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
        stage="voice",
    ),
    "formant": Spec(
        "voice",
        "Форманты",
        "Тембр без смены нот. Больше 1 — моложе и тоньше, меньше 1 — крупнее и грубее",
        0.6,
        1.8,
        0.01,
        "\u00d7",
        stage="voice",
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
        stage="voice",
    ),
    "nightcore": _onoff(
        "voice",
        "Найткор",
        "Голос на октаву выше вместе с тембром, как ускоренная запись",
        stage="voice",
    ),
    "octave_down_on": _onoff(
        "layers", "Октава вниз", "Слой голоса на октаву ниже под основным", stage="layers"
    ),
    "octave_down_db": Spec(
        "layers",
        "Громкость октавы вниз",
        "Насколько слой тише основного голоса",
        -30,
        0,
        0.5,
        "дБ",
        stage="layers",
    ),
    "octave_up_on": _onoff(
        "layers", "Октава вверх", "Слой голоса на октаву выше под основным", stage="layers"
    ),
    "octave_up_db": Spec(
        "layers",
        "Громкость октавы вверх",
        "Насколько слой тише основного голоса",
        -30,
        0,
        0.5,
        "дБ",
        stage="layers",
    ),
    "doubles_on": _onoff(
        "layers", "Даблы", "Две копии голоса по сторонам, как второй дубль", stage="layers"
    ),
    "doubles_db": Spec(
        "layers",
        "Громкость даблов",
        "Насколько даблы тише основного голоса",
        -24,
        0,
        0.5,
        "дБ",
        stage="layers",
    ),
    "doubles_left_ms": Spec(
        "layers",
        "Задержка левого",
        "Насколько левый дабл отстаёт. Больше — шире, но слышно как эхо",
        5,
        60,
        1,
        "мс",
        stage="layers",
    ),
    "doubles_right_ms": Spec(
        "layers",
        "Задержка правого",
        "Насколько правый дабл отстаёт",
        5,
        60,
        1,
        "мс",
        stage="layers",
    ),
    "doubles_wander_ms": Spec(
        "layers",
        "Плавание даблов",
        "Насколько задержка гуляет. Больше — даблы расстроенее, живее",
        0,
        6,
        0.1,
        "мс",
        stage="layers",
    ),
    "bitcrush_on": _onoff(
        "layers", "Биткраш", "Цифровая грязь: голос как из старой игры", stage="layers"
    ),
    "bitcrush_bits": Spec(
        "layers", "Биты", "Меньше бит — грубее ступеньки", 3, 16, 1, "бит", stage="layers"
    ),
    "bitcrush_hold": Spec(
        "layers",
        "Удержание",
        "Больше — ниже частота дискретизации, звонче и грязнее",
        1,
        8,
        1,
        "\u00d7",
        stage="layers",
    ),
    "stutter_on": _onoff(
        "fx",
        "Статтер",
        "В паузе после фразы повторяет её последний кусочек, как заевшая пластинка",
        stage="fx",
    ),
    "stutter_ms": Spec(
        "fx",
        "Длина кусочка",
        "Сколько от конца фразы повторяется. Меньше — дробнее, как глитч",
        30,
        250,
        5,
        "мс",
        stage="fx",
    ),
    "stutter_repeats": Spec(
        "fx",
        "Повторы",
        "Сколько раз кусочек повторяется, если влезает в паузу",
        1,
        8,
        1,
        "\u00d7",
        stage="fx",
    ),
    "throw_on": _onoff(
        "fx",
        "Дилей на концах фраз",
        "Последние слова фразы улетают эхом то влево, то вправо",
        stage="fx",
    ),
    "throw_db": Spec(
        "fx", "Громкость эха", "Насколько первое эхо тише голоса", -24, 0, 0.5, "дБ", stage="fx"
    ),
    "throw_ms": Spec(
        "fx",
        "Шаг эха",
        "Через сколько приходит каждое эхо. Под бит: 60000 / BPM на долю",
        60,
        1000,
        5,
        "мс",
        stage="fx",
    ),
    "throw_feedback": Spec(
        "fx",
        "Хвост эха",
        "Сколько остаётся от каждого следующего эха. Больше — длиннее хвост",
        0,
        0.85,
        0.05,
        "\u00d7",
        stage="fx",
    ),
    "throw_tone_hz": Spec(
        "fx", "Тембр эха", "Ниже — эхо темнее и уходит назад", 1000, 12000, 100, "Гц", stage="fx"
    ),
    "reverb_on": _onoff("fx", "Ревер", "Голос в комнате или зале", stage="fx"),
    "reverb_mix_db": Spec(
        "fx",
        "Громкость ревера",
        "Насколько хвост тише голоса. Ближе к нулю — голос дальше",
        -40,
        0,
        0.5,
        "дБ",
        stage="fx",
    ),
    "reverb_decay_s": Spec(
        "fx",
        "Длина хвоста",
        "За сколько хвост затихает. Больше — зал, меньше — комната",
        0.3,
        6,
        0.1,
        "с",
        stage="fx",
    ),
    "reverb_predelay_ms": Spec(
        "fx",
        "Предзадержка",
        "Пауза перед хвостом: голос остаётся чётким впереди",
        0,
        150,
        1,
        "мс",
        stage="fx",
    ),
    "reverb_tone_hz": Spec(
        "fx",
        "Тембр ревера",
        "Ниже — хвост темнее и не мешает голосу",
        1000,
        12000,
        100,
        "Гц",
        stage="fx",
    ),
    "beat_db": Spec(
        "beat",
        "Громкость бита",
        "Весь бит громче или тише, до мастера",
        -12,
        6,
        0.5,
        "дБ",
        stage="beat",
    ),
    "bass_freq_hz": Spec(
        "beat",
        "Граница 808",
        "Что считать 808: всё ниже этой частоты",
        60,
        250,
        5,
        "Гц",
        stage="beat",
    ),
    "bass_drive_db": Spec(
        "beat",
        "Перегруз 808",
        "Насколько грязнее 808. Громкость низа остаётся прежней",
        0,
        30,
        1,
        "дБ",
        stage="beat",
    ),
    "master_on": _onoff("master", "Мастер", "Поднимает громкость трека клиппингом", stage="master"),
    "master_auto": _onoff(
        "master", "Громкость как у референсов", "Цель громкости: медиана референсов", stage="master"
    ),
    "master_lufs": Spec(
        "master",
        "Громкость мастера",
        "Цель громкости, если не как у референсов. Громче — плотнее и грязнее",
        -16,
        -5,
        0.1,
        "LUFS",
        stage="master",
    ),
    "rack": Spec(
        "rack",
        "Рэк эффектов",
        "Эффекты по порядку, после встроенных эффектов и до бита",
        stage="rack",
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


def _number(what: str, raw: Any, low: float, high: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"{what} takes a number")
    if not low <= raw <= high:
        raise ValueError(f"{what} takes {low:g} to {high:g}")
    return float(raw)


def _param(what: str, spec: dict[str, Any], raw: Any) -> float | str:
    choices = spec.get("choices", [])
    if choices and isinstance(raw, str):
        if raw not in choices:
            raise ValueError(f"{what} takes {', '.join(choices)}")
        return raw
    value = _number(what, raw, spec["low"], spec["high"])
    if choices and value != int(value):
        raise ValueError(f"{what} takes a whole number or one of {', '.join(choices)}")
    return value


def _lane(effect: str, specs: dict[str, dict[str, Any]], raw: Any) -> AutomationLane:
    if not isinstance(raw, dict):
        raise ValueError("an automation lane is an object")
    extra = sorted(set(raw) - {"param", "points"})
    if extra:
        raise ValueError(f"a lane takes param and points, not {', '.join(extra)}")
    name = raw.get("param")
    if not isinstance(name, str) or name not in specs:
        raise ValueError(f"{effect} has no param {name}")
    what = f"{effect}.{name}"
    if specs[name].get("choices"):
        raise ValueError(f"{what} is a choice and cannot be automated")
    points = raw.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError(f"{what}: a lane needs points, [[seconds, value], ...]")
    out: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{what}: a point is [seconds, value]")
        seconds = point[0]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not 0 <= seconds < math.inf
        ):
            raise ValueError(f"{what}: points take seconds from 0, not {seconds!r}")
        if out and seconds < out[-1][0]:
            raise ValueError(f"{what}: points go in order of time")
        out.append((float(seconds), float(_param(what, specs[name], point[1]))))
    return AutomationLane(name, tuple(out))


def _slot(raw: Any, catalogue: dict[str, dict[str, dict[str, Any]]]) -> RackEffect:
    if not isinstance(raw, dict):
        raise ValueError("is an object with an effect name")
    extra = sorted(set(raw) - {"effect", "on", "params", "automation"})
    if extra:
        raise ValueError(f"takes effect, on, params and automation, not {', '.join(extra)}")
    name = raw.get("effect")
    if not isinstance(name, str):
        raise ValueError("needs an effect name")
    if name not in catalogue:
        raise ValueError(f"no effect {name}; `redline effects` lists them")
    on = raw.get("on", True)
    if not isinstance(on, bool):
        raise ValueError(f"{name}: on takes true or false")
    specs = catalogue[name]
    params = _params(name, specs, raw.get("params", {}))
    lanes_raw = raw.get("automation", [])
    if not isinstance(lanes_raw, list):
        raise ValueError(f"{name}: automation is a list of lanes")
    lanes = tuple(_lane(name, specs, lane) for lane in lanes_raw)
    names = [lane.param for lane in lanes]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"{name}: {', '.join(twice)} automated twice")
    return RackEffect(name, on, params, lanes)


def _params(
    effect: str, specs: dict[str, dict[str, Any]], given: Any
) -> tuple[tuple[str, float | str], ...]:
    if not isinstance(given, dict):
        raise ValueError(f"{effect}: params are an object of name: value")
    params = []
    for key, value in given.items():
        if key not in specs:
            raise ValueError(f"{effect} has no param {key}")
        params.append((key, _param(f"{effect}.{key}", specs[key], value)))
    return tuple(sorted(params))


def _rack(raw: Any) -> tuple[RackEffect, ...]:
    if not isinstance(raw, list):
        raise ValueError("the rack is a list of effects")
    if not raw:
        return ()
    effects = native.rack_effects()
    if effects is None:
        raise ValueError(
            "the effects rack needs the Rust module redline_dsp, which is not built here; "
            "build it (uv sync) or leave the rack empty"
        )
    catalogue = {e["effect"]: {p["name"]: p for p in e["params"]} for e in effects}
    out = []
    for i, slot in enumerate(raw):
        try:
            out.append(_slot(slot, catalogue))
        except ValueError as error:
            raise ValueError(f"rack effect {i + 1} {error}") from None
    return tuple(out)


def from_dict(data: dict[str, Any]) -> Settings:
    """Settings from a mapping; keys left out keep their defaults, unknown keys are refused.

    A mapping without "version" is version 1 and has no rack; version 2 may carry one.
    """
    data = dict(data)
    version = data.pop("version", 1)
    if type(version) is not int or version not in (1, VERSION):
        raise ValueError(f"version takes 1 or {VERSION}")
    rack = data.pop("rack", None)
    # an empty rack is what version 1 means anyway; records of runs spell it out
    if rack and version != VERSION:
        raise ValueError(f'a rack needs "version": {VERSION}')
    unknown = sorted(set(data) - set(KINDS))
    if unknown:
        raise ValueError(f"unknown setting {', '.join(unknown)}")
    scalars = {key: _value(key, raw) for key, raw in data.items()}
    return Settings(**scalars, rack=_rack([] if rack is None else rack))


def _no_twice(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # json.loads would keep the last of a repeated key without a word
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"{key} is given twice")
        out[key] = value
    return out


def load(path: Path) -> Settings:
    """Settings from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_twice)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: settings are a JSON object")
    return from_dict(data)


def load_preset(folder: Path, name: str) -> Settings:
    """A preset the studio saved in the folder, by its name."""
    path = folder / f"{name}.json"
    if not PRESET_NAME.fullmatch(name) or name.strip() != name or not path.is_file():
        saved = sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []
        raise ValueError(f"no preset {name!r}; saved: {', '.join(saved) or 'none'}")
    return load(path)


def _slot_dict(effect: RackEffect) -> dict[str, Any]:
    return {
        "effect": effect.effect,
        "on": effect.on,
        "params": dict(effect.params),
        "automation": [
            {"param": lane.param, "points": [list(p) for p in lane.points]}
            for lane in effect.automation
        ],
    }


def to_dict(settings: Settings) -> dict[str, Any]:
    """The settings as their file holds them: the version, every key, the rack as a list."""
    scalars = {key: getattr(settings, key) for key in KINDS}
    return {"version": VERSION, **scalars, "rack": [_slot_dict(e) for e in settings.rack]}


def rack_json(rack: tuple[RackEffect, ...]) -> str:
    """The rack as `redline_dsp.rack_process` takes it; the automation goes separately."""
    effects = [{"effect": e.effect, "on": e.on, "params": dict(e.params)} for e in rack]
    return json.dumps({"effects": effects})


def save(settings: Settings, path: Path) -> None:
    """Write the settings as JSON, every key spelled out."""
    path.write_text(json.dumps(to_dict(settings), indent=1), encoding="utf-8")


def schema() -> dict[str, Any]:
    """Groups, specs and defaults of the plain settings, for the studio page.

    The rack is left out: the page builds its card from `/api/effects`.
    """
    defaults = asdict(Settings())
    return {
        "groups": GROUPS,
        "settings": [
            {"key": key, "default": defaults[key], "kind": KINDS[key].__name__, **asdict(spec)}
            for key, spec in SPECS.items()
            if key in KINDS
        ],
    }
