// Russian names for the instruments, their params and the step lanes.

import type { Lane } from "./model.ts";

const INSTRUMENTS: Record<string, string> = {
  kick: "бочка",
  bass808: "808",
  snare: "малый",
  clap: "хлопок",
  hat_closed: "закр. хэт",
  hat_open: "откр. хэт",
  rim: "римшот",
  cowbell: "ковбелл",
  fm: "FM",
  pluck: "щипок",
  saw: "пила",
  sampler: "сэмплер",
  tom: "том",
  shaker: "шейкер",
  crash: "крэш",
  snap: "щелчок пальцами",
  perc: "перкуссия",
  reese: "риз-бас",
  vox: "хор",
  flute: "флейта",
  oneshot: "сэмпл",
  oneshot_hat: "хэт из сэмпла",
  oneshot_open_hat: "откр. хэт из сэмпла",
  sample808: "808 из сэмпла",
};

const PARAMS: Record<string, string> = {
  tune: "Строй",
  pitch_drop: "Падение высоты",
  drop_ms: "Время падения",
  decay: "Затухание",
  click: "Щелчок",
  drive: "Перегруз",
  release: "Отпускание",
  glide_ms: "Глайд",
  tone: "Тон",
  snap: "Хлёст",
  spread_ms: "Разброс",
  metal: "Металл",
  preset: "Пресет",
  ratio: "Отношение",
  index: "Глубина FM",
  attack: "Атака",
  detune: "Расстройка",
  cutoff: "Срез",
  resonance: "Резонанс",
  voices: "Голоса",
  punch: "Удар высоты",
  punch_ms: "Время удара",
  clip: "Клип",
  fold: "Фолд",
  attack_ms: "Атака",
  sub: "Саб",
  vowel: "Гласная",
  breath: "Дыхание",
  vibrato: "Вибрато",
  vibrato_hz: "Частота вибрато",
  length: "Длина",
};

export const LANE_NAMES: Record<Lane, string> = {
  velocity: "Громкость",
  pitch: "Высота",
  decay: "Затухание",
  chance: "Вероятность",
};

export function instrumentLabel(name: string): string {
  return INSTRUMENTS[name] ?? name;
}

export function paramName(name: string): string {
  return PARAMS[name] ?? name;
}
