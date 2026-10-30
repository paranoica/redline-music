// The shapes the studio server sends and takes (apps/redline/studio/server.py).

export type ParamValue = number | string;

export interface AutomationLane {
  param: string;
  points: [number, number][];
}

export interface UserEffect {
  effect: string;
  on: boolean;
  params: Record<string, ParamValue>;
  automation: AutomationLane[];
}

export type SettingValue = number | boolean;

// the settings file's mapping: every scalar key, the version and the user's rack
export interface Settings {
  [key: string]: SettingValue | UserEffect[] | undefined;
  version?: number;
  rack?: UserEffect[];
}

export interface SettingSpec {
  key: string;
  default: SettingValue;
  kind: "bool" | "int" | "float";
  group: string;
  label: string;
  help: string;
  low: number;
  high: number;
  step: number;
  unit: string;
  choices: number[];
  basic: boolean;
  stage: string;
}

export interface ParamSpec {
  name: string;
  low: number;
  high: number;
  default: number;
  unit: string;
  choices?: string[];
  fixed_while_playing?: boolean;
}

export interface EffectSpec {
  effect: string;
  offline_only: boolean;
  params: ParamSpec[];
}

export interface Schema {
  groups: Record<string, string>;
  settings: SettingSpec[];
  effects: EffectSpec[];
}

// what redline.engine.live.analysis measured; a number is null when its stage was off
export interface Analysis {
  [key: string]: unknown;
  pre_gain_db?: number | null;
  gate_db?: number | null;
  makeup_db?: number | null;
  peak_db?: number | null;
  eq_fit_db?: number[];
  post_gain_db?: number | null;
  tuning_cents?: number | null;
  bitcrush_peak_db?: number | null;
  throw_threshold_db?: number | null;
  master_target_lufs?: number | null;
  master_gain_db?: number | null;
  master_trim_db?: number | null;
  settings?: Settings;
}

export interface Reference {
  index: number;
  name: string;
  seconds: number;
  lufs: number;
}

export type Track = "vocal" | "beat" | "master";
export const TRACKS: readonly Track[] = ["vocal", "beat", "master"];

export interface Session {
  track: { name: string; rate: number; frames: number; seconds: number };
  settings: Settings;
  schema: Schema;
  analysis: Analysis;
  stale: Record<string, string[]>;
  presets: Record<string, Settings>;
  references: Reference[];
  racks: Record<Track, string>;
}

// one effect of a rack as redline_dsp takes it
export interface RackSlot {
  effect: string;
  on: boolean;
  params: Record<string, ParamValue>;
}

export type Racks = Record<Track, RackSlot[]>;

// an automation lane at its slot in the vocal rack
export interface Lane {
  slot: number;
  param: string;
  points: [number, number][];
}
