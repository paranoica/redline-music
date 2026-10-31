// The simple knobs of the effects tab: a position 0..1 on each, drawn from and written to the
// settings keys of redline.engine.settings. The full controls stay in the rack editor.

import { formatValue } from "../core/format.ts";
import type { Settings } from "../types.ts";

export type KnobId = "vocal" | "clean" | "echo" | "reverb" | "tune" | "beat" | "bass";

export interface Knob {
  id: KnobId;
  group: "voice" | "beat";
  name: string;
  keys: readonly string[];
}

export const KNOBS: readonly Knob[] = [
  { id: "vocal", group: "voice", name: "Громкость", keys: ["vocal_db"] },
  { id: "clean", group: "voice", name: "Чистота", keys: ["comp_threshold_db", "drive_db"] },
  { id: "echo", group: "voice", name: "Эхо", keys: ["throw_on", "throw_db"] },
  { id: "reverb", group: "voice", name: "Реверб", keys: ["reverb_on", "reverb_mix_db"] },
  { id: "tune", group: "voice", name: "Тюн", keys: ["hard_tune"] },
  { id: "beat", group: "beat", name: "Громкость", keys: ["beat_db"] },
  { id: "bass", group: "beat", name: "808", keys: ["bass_drive_db"] },
];

export type Knobs = Record<KnobId, number>;

interface Span {
  key: string;
  // the value at knob 0 and at knob 1, both inside the key's range and on its step
  from: number;
  to: number;
  step: number;
}

const VOCAL: Span = { key: "vocal_db", from: -24, to: 6, step: 0.5 };
// 0 is a gentle chain: the compressor barely touches the voice and nothing drives it
const THRESHOLD: Span = { key: "comp_threshold_db", from: -10, to: -50, step: 1 };
const DRIVE: Span = { key: "drive_db", from: 0, to: 40, step: 1 };
const THROW: Span = { key: "throw_db", from: -24, to: 0, step: 0.5 };
const REVERB: Span = { key: "reverb_mix_db", from: -40, to: 0, step: 0.5 };
const TUNE: Span = { key: "hard_tune", from: 0, to: 100, step: 5 };
const BEAT: Span = { key: "beat_db", from: -12, to: 6, step: 0.5 };
const BASS: Span = { key: "bass_drive_db", from: 0, to: 30, step: 1 };

// an effect that is on reads at least this far round, so a knob at 0 always means off
const ON_FLOOR = 0.01;

function num(s: Settings, key: string): number {
  const v = s[key];
  if (typeof v === "number") return v;
  throw new Error(`the settings have no ${key}`);
}

function flag(s: Settings, key: string): boolean {
  const v = s[key];
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0;
  throw new Error(`the settings have no ${key}`);
}

const unit = (v: number): number => Math.min(1, Math.max(0, v));

const position = (s: Settings, span: Span): number => unit((num(s, span.key) - span.from) / (span.to - span.from));

function valueAt(span: Span, v: number): number {
  const raw = span.from + (span.to - span.from) * v;
  const stepped = Math.round(Math.round(raw / span.step) * span.step * 1e6) / 1e6;
  return stepped === 0 ? 0 : stepped;
}

// two keys move together on the knob; settings set elsewhere read as their mean
const cleanAt = (s: Settings): number => (position(s, THRESHOLD) + position(s, DRIVE)) / 2;

const switched = (s: Settings, on: string, span: Span): number => (flag(s, on) ? Math.max(ON_FLOOR, position(s, span)) : 0);

export function knobsFromSettings(s: Settings): Knobs {
  return {
    vocal: position(s, VOCAL),
    clean: cleanAt(s),
    echo: switched(s, "throw_on", THROW),
    reverb: switched(s, "reverb_on", REVERB),
    tune: position(s, TUNE),
    beat: position(s, BEAT),
    bass: position(s, BASS),
  };
}

// at 0 the effect goes off and keeps its level for the next time it is on
function setSwitched(s: Settings, on: string, span: Span, v: number): Settings {
  return v === 0 ? { ...s, [on]: false } : { ...s, [on]: true, [span.key]: valueAt(span, v) };
}

export function settingsFromKnob(s: Settings, knob: KnobId, value: number): Settings {
  if (!Number.isFinite(value)) throw new RangeError(`a knob cannot be at ${value}`);
  const v = unit(value);
  switch (knob) {
    case "vocal":
      return { ...s, [VOCAL.key]: valueAt(VOCAL, v) };
    case "clean":
      return { ...s, [THRESHOLD.key]: valueAt(THRESHOLD, v), [DRIVE.key]: valueAt(DRIVE, v) };
    case "echo":
      return setSwitched(s, "throw_on", THROW, v);
    case "reverb":
      return setSwitched(s, "reverb_on", REVERB, v);
    case "tune":
      return { ...s, [TUNE.key]: valueAt(TUNE, v) };
    case "beat":
      return { ...s, [BEAT.key]: valueAt(BEAT, v) };
    case "bass":
      return { ...s, [BASS.key]: valueAt(BASS, v) };
  }
}

const OFF = "выкл";

const db = (v: number): string => formatValue(v, "dB", Number.isInteger(v) ? 1 : 0.5);

function cleanWord(position: number): string {
  if (position < 1 / 3) return "мягко";
  return position < 2 / 3 ? "средне" : "жёстко";
}

export function knobText(s: Settings, knob: KnobId): string {
  switch (knob) {
    case "vocal":
      return db(num(s, VOCAL.key));
    case "clean":
      return cleanWord(cleanAt(s));
    case "echo":
      return flag(s, "throw_on") ? db(num(s, THROW.key)) : OFF;
    case "reverb":
      return flag(s, "reverb_on") ? db(num(s, REVERB.key)) : OFF;
    case "tune": {
      const v = num(s, TUNE.key);
      return v === 0 ? OFF : formatValue(v, "%", TUNE.step);
    }
    case "beat":
      return db(num(s, BEAT.key));
    case "bass": {
      const v = num(s, BASS.key);
      return v === 0 ? OFF : db(v);
    }
  }
}
