// The rack's pattern heard on its own, one hit of a sound, the sounds that can be added and the
// step the playing position is on: pure parts, so node tests them.

import type { PackSound } from "../beat/api.ts";
import {
  addSound,
  drumSteps,
  ensureRow,
  isNoteRow,
  noteName,
  patternScore,
  SAMPLED,
  sectionStarts,
  soundMuted,
  STEPS_PER_BAR,
  usesFlats,
  type InstrumentSpec,
  type NamedEdit,
  type NoteJson,
  type ParamValue,
  type Score,
  type SoundMixJson,
} from "../beat/model.ts";

const own = (o: object, key: string): boolean => Object.hasOwn(o, key);

// ---------- the pattern on its own ----------

// long enough that the loop's end is rarely heard, short enough to stay well under the song limit
export const PATTERN_LOOP_BARS = 64;

export interface Preview {
  score: Score;
  bars: number;
}

// The pattern alone, repeated to PATTERN_LOOP_BARS. The rack's mute holds in every section, so a
// sound muted everywhere stays muted here; a mute of some sections only belongs to those.
export function patternPreview(score: Score, pattern: string): Preview | null {
  const p = own(score.patterns, pattern) ? score.patterns[pattern] : undefined;
  if (!p) return null;
  const bars = Math.max(1, Math.round(p.bars));
  const loops = Math.max(1, Math.floor(PATTERN_LOOP_BARS / bars));
  const made = patternScore(score, pattern, loops);
  if (!made.ok) return null;
  const muted = Object.keys(score.sounds).filter((s) => soundMuted(score, s));
  const section = made.score.sections[0];
  if (muted.length && section) section.mute = muted;
  return { score: made.score, bars: bars * loops };
}

// ---------- the playing step ----------

export interface StepAt {
  // the pattern's own bar, from 0
  bar: number;
  step: number;
}

// The pattern's bar and step at a song position in bars, when a section playing it holds that
// position; a fill counts on its section's last bar.
export function stepAt(score: Score, pattern: string, bars: number): StepAt | null {
  if (!Number.isFinite(bars) || bars < 0) return null;
  const p = own(score.patterns, pattern) ? score.patterns[pattern] : undefined;
  if (!p) return null;
  const starts = sectionStarts(score);
  for (const [i, s] of score.sections.entries()) {
    const start = starts[i] as number;
    const length = typeof s.bars === "number" ? s.bars : 0;
    if (bars < start || bars >= start + length) continue;
    const within = bars - start;
    const whole = Math.floor(within);
    const step = Math.min(STEPS_PER_BAR - 1, Math.floor((within - whole) * STEPS_PER_BAR));
    if ((s.play ?? []).includes(pattern)) return { bar: whole % Math.max(1, p.bars), step };
    if (s.fill === pattern && whole === length - 1) return { bar: 0, step };
    return null;
  }
  return null;
}

// the endless loop pattern mode plays, for stepAt
export function loopedScore(score: Score, pattern: string): Score {
  return { ...score, sections: [{ name: "loop", bars: Number.MAX_SAFE_INTEGER, play: [pattern] }] };
}

export function cursorColumn(at: StepAt | null, shownBar: number): number | null {
  return at !== null && at.bar === shownBar ? at.step : null;
}

// ---------- one hit of a sound ----------

const BASS = new Set(["bass808", "sample808", "reese"]);

function firstNote(score: Score, pattern: string | null, sound: string): string | null {
  const names = Object.keys(score.patterns);
  const order = pattern !== null && own(score.patterns, pattern) ? [pattern, ...names.filter((n) => n !== pattern)] : names;
  for (const name of order) {
    const rows = score.patterns[name]?.rows ?? {};
    const row = own(rows, sound) ? rows[sound] : undefined;
    if (row === undefined || !isNoteRow(row)) continue;
    const first = [...row].filter((n) => typeof n.note === "string" && typeof n.step === "number").sort((x, y) => x.step - y.step)[0];
    if (first) return first.note;
  }
  return null;
}

// The sound alone for one bar: a drum hit on the first step, a note sound one beat long at
// `midi`, else its first note (the pattern shown first), else C2 for a bass and C4 for others.
export function hitScore(score: Score, specs: readonly InstrumentSpec[], sound: string, pattern: string | null, midi?: number): Score | null {
  const s = own(score.sounds, sound) ? score.sounds[sound] : undefined;
  if (!s) return null;
  const kind = specs.find((i) => i.instrument === s.instrument)?.kind;
  if (!kind) return null;
  let row: string | NoteJson[];
  if (kind === "drum") row = "x" + ".".repeat(STEPS_PER_BAR - 1);
  else {
    const note = midi !== undefined && Number.isInteger(midi) && midi >= 0 && midi <= 127 ? noteName(midi, usesFlats(score)) : (firstNote(score, pattern, sound) ?? (BASS.has(s.instrument) ? "C2" : "C4"));
    row = [{ step: 0, len: 4, note, vel: 0.8 }];
  }
  const out: Score = {
    ...score,
    sounds: { [sound]: structuredClone(s) },
    patterns: { hit: { bars: 1, rows: { [sound]: row } } },
    sections: [{ name: "hit", bars: 1, play: ["hit"] }],
  };
  delete out.mix;
  const mixed = score.mix?.sounds?.[sound];
  if (mixed || score.mix?.master) {
    out.mix = {};
    if (mixed) {
      const kept: SoundMixJson = structuredClone(mixed);
      // the sound it ducks under is not in the hit
      delete kept.sidechain;
      out.mix.sounds = { [sound]: kept };
    }
    if (score.mix?.master) out.mix.master = structuredClone(score.mix.master);
  }
  return out;
}

// ---------- adding and taking away ----------

export interface Pick {
  name: string;
  instrument: string;
  library?: string;
  params?: Record<string, ParamValue>;
}

const PACK_INSTRUMENT: Record<string, string> = {
  hat_closed: "oneshot_hat",
  hat_open: "oneshot_open_hat",
  "808": "sample808",
  melodic: "sampler",
};

// a pack one-shot as a sound: hats in the hats' choke group, the 808 and melodic ones playing notes
export function packSound(category: string, item: PackSound): Pick {
  return { name: item.name, instrument: PACK_INSTRUMENT[category] ?? "oneshot", library: item.library };
}

export const PACK_CATEGORY_NAMES: Record<string, string> = {
  kick: "бочки",
  "808": "808",
  snare: "малые",
  clap: "хлопки",
  hat_closed: "закрытые хэты",
  hat_open: "открытые хэты",
  crash: "тарелки",
  perc: "перкуссия",
  fx: "эффекты",
  vox: "голоса",
  melodic: "мелодические",
};

export function synthMenu(specs: readonly InstrumentSpec[]): { drums: InstrumentSpec[]; notes: InstrumentSpec[] } {
  const synths = specs.filter((s) => !SAMPLED.includes(s.instrument));
  return { drums: synths.filter((s) => s.kind === "drum"), notes: synths.filter((s) => s.kind === "note") };
}

// the sound, with an empty row in the pattern shown when there is one
export function addPicked(score: Score, specs: readonly InstrumentSpec[], pattern: string | null, pick: Pick): NamedEdit {
  const made = addSound(score, specs, pick.name, pick.instrument, pick.params, pick.library);
  if (!made.ok || pattern === null) return made;
  const row = ensureRow(made.score, specs, pattern, made.name);
  return row.ok ? { ok: true, score: row.score, name: made.name } : row;
}

// some pattern has a hit or a note of it
export function soundUsed(score: Score, sound: string): boolean {
  for (const [name, p] of Object.entries(score.patterns)) {
    const rows = p.rows ?? {};
    if (!own(rows, sound)) continue;
    const row = rows[sound];
    if (row === undefined) continue;
    if (isNoteRow(row)) {
      if (row.length) return true;
      continue;
    }
    try {
      if (drumSteps(score, name, sound).some((h) => h !== ".")) return true;
    } catch {
      return true;
    }
  }
  return false;
}
