// The beat score as the editor holds it: the JSON object itself, so every field the page does
// not touch goes back exactly as it came. Each operation returns a new score and leaves the
// one it was given alone, so a score can go straight into the history.

import type { ParamSpec } from "../types.ts";

export const STEPS_PER_BAR = 16;
export const MAX_RACK = 4;
const GAIN_DB: [number, number] = [-60, 12];
const PAN: [number, number] = [-1, 1];
const NAME = /^[a-z0-9_]{1,24}$/;

export type Hit = "." | "x" | "X" | "g" | "2" | "3" | "4" | "6";
const HITS = new Set<string>([".", "x", "X", "g", "2", "3", "4", "6"]);
export const ROLLS = [2, 3, 4, 6] as const;

export type Lane = "velocity" | "pitch" | "decay" | "chance";
export const LANES: readonly Lane[] = ["velocity", "pitch", "decay", "chance"];

export interface LaneSpec {
  low: number;
  high: number;
  step: number;
}

// the ranges the core checks a lane against
export const LANE_SPECS: Record<Lane, LaneSpec> = {
  velocity: { low: 0, high: 1, step: 0.01 },
  pitch: { low: -24, high: 24, step: 1 },
  decay: { low: 0.1, high: 4, step: 0.01 },
  chance: { low: 0, high: 1, step: 0.01 },
};

export type ParamValue = number | string;

export interface NoteJson {
  step: number;
  len: number;
  note: string;
  vel: number;
  passing?: boolean;
}

export interface DrumJson {
  steps: string;
  velocity?: number[];
  pitch?: number[];
  decay?: number[];
  chance?: number[];
}

export type RowJson = string | DrumJson | NoteJson[];

export interface PatternJson {
  bars: number;
  rows: Record<string, RowJson>;
}

export interface SectionJson {
  name: string;
  bars: number;
  play: string[];
  mute?: string[];
  fill?: string;
}

export interface SoundJson {
  instrument: string;
  params?: Record<string, ParamValue>;
  swing?: number;
  library?: string;
}

export interface RackSlotJson {
  effect: string;
  on?: boolean;
  params?: Record<string, ParamValue>;
}

export interface SoundMixJson {
  gain_db?: number;
  pan?: number;
  rack?: RackSlotJson[];
  sidechain?: string;
}

export interface MixJson {
  sounds?: Record<string, SoundMixJson>;
  master?: RackSlotJson[];
}

export interface Score {
  version: number;
  bpm: number;
  swing?: number;
  seed: number;
  key: string;
  sounds: Record<string, SoundJson>;
  patterns: Record<string, PatternJson>;
  sections: SectionJson[];
  mix?: MixJson;
}

export interface InstrumentSpec {
  instrument: string;
  kind: "drum" | "note";
  mono: boolean;
  max_voices: number;
  choke?: string;
  choked?: boolean;
  max_life_s: number;
  params: ParamSpec[];
}

export class ModelError extends Error {}

const isObject = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);

export function parseScore(text: string): Score {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (error) {
    throw new ModelError(`the score is not JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!isObject(value)) throw new ModelError("the score is not a JSON object");
  if (!isObject(value["sounds"])) throw new ModelError("the score has no sounds object");
  if (!isObject(value["patterns"])) throw new ModelError("the score has no patterns object");
  if (!Array.isArray(value["sections"])) throw new ModelError("the score has no sections list");
  return value as unknown as Score;
}

export function serialize(score: Score): string {
  return JSON.stringify(score);
}

const copy = (score: Score): Score => structuredClone(score);

// ---------- rows ----------

export function patternSteps(score: Score, pattern: string): number {
  const p = score.patterns[pattern];
  if (!p) throw new ModelError(`no pattern ${pattern}`);
  return p.bars * STEPS_PER_BAR;
}

function rowOf(score: Score, pattern: string, sound: string): RowJson {
  const p = score.patterns[pattern];
  if (!p) throw new ModelError(`no pattern ${pattern}`);
  const row = p.rows[sound];
  if (row === undefined) throw new ModelError(`no row ${sound} in pattern ${pattern}`);
  return row;
}

export function isNoteRow(row: RowJson): row is NoteJson[] {
  return Array.isArray(row);
}

function stepsText(row: RowJson): string {
  if (typeof row === "string") return row;
  if (!Array.isArray(row) && typeof row.steps === "string") return row.steps;
  throw new ModelError("not a drum row");
}

// where each step sits in the text: "|" and spaces are not steps
function stepIndices(text: string): number[] {
  const out: number[] = [];
  for (let i = 0; i < text.length; i++) if (text[i] !== "|" && text[i] !== " ") out.push(i);
  return out;
}

export function drumSteps(score: Score, pattern: string, sound: string): Hit[] {
  const text = stepsText(rowOf(score, pattern, sound));
  return stepIndices(text).map((i) => {
    const c = text[i] as string;
    return (HITS.has(c) ? c : ".") as Hit;
  });
}

export function hitAt(score: Score, pattern: string, sound: string, step: number): Hit {
  const steps = drumSteps(score, pattern, sound);
  const hit = steps[step];
  if (hit === undefined) throw new ModelError(`step ${step} is outside the row`);
  return hit;
}

// a hit's velocity when the row has no velocity lane, as the core plays it
export function hitVelocity(hit: Hit): number {
  if (hit === ".") return 0;
  if (hit === "X") return 1;
  if (hit === "g") return 0.4;
  return 0.8;
}

export function laneDefault(lane: Lane, hit: Hit): number {
  if (lane === "velocity") return hitVelocity(hit);
  if (lane === "pitch") return 0;
  return 1;
}

export function setHit(score: Score, pattern: string, sound: string, step: number, hit: Hit): Score {
  if (!HITS.has(hit)) throw new ModelError(`${hit} is not a step`);
  const row = rowOf(score, pattern, sound);
  const text = stepsText(row);
  const at = stepIndices(text)[step];
  if (at === undefined || step < 0) throw new ModelError(`step ${step} is outside the row`);
  const next = text.slice(0, at) + hit + text.slice(at + 1);
  const out = copy(score);
  const rows = (out.patterns[pattern] as PatternJson).rows;
  if (typeof row === "string") {
    rows[sound] = next;
  } else {
    const obj = rows[sound] as DrumJson;
    obj.steps = next;
    // with a velocity lane the hit's own level would be lost; the new hit takes its default
    if (Array.isArray(obj.velocity) && step < obj.velocity.length) obj.velocity[step] = hitVelocity(hit);
  }
  return out;
}

const CYCLE: Record<Hit, Hit> = { ".": "x", x: "X", X: "g", g: ".", "2": ".", "3": ".", "4": ".", "6": "." };

export function cycleStep(score: Score, pattern: string, sound: string, step: number): Score {
  return setHit(score, pattern, sound, step, CYCLE[hitAt(score, pattern, sound, step)]);
}

export function setRoll(score: Score, pattern: string, sound: string, step: number, n: number): Score {
  if (!(ROLLS as readonly number[]).includes(n)) throw new ModelError(`a roll is 2, 3, 4 or 6 hits, not ${n}`);
  return setHit(score, pattern, sound, step, String(n) as Hit);
}

// the roll key: a plain step starts a roll of 2, a roll takes the next count, 6 goes back to a hit
export function nextRoll(hit: Hit): Hit {
  const order: Record<Hit, Hit> = { ".": "2", x: "2", X: "2", g: "2", "2": "3", "3": "4", "4": "6", "6": "x" };
  return order[hit];
}

export function rollCount(hit: Hit): number {
  return hit === "2" || hit === "3" || hit === "4" || hit === "6" ? Number(hit) : 0;
}

// ---------- lanes ----------

function round(value: number, step: number): number {
  const r = Math.round(value / step) * step;
  return Number(r.toFixed(6));
}

export function clampLane(lane: Lane, value: number): number {
  const s = LANE_SPECS[lane];
  return round(Math.min(s.high, Math.max(s.low, value)), s.step);
}

export function laneValues(score: Score, pattern: string, sound: string, lane: Lane): { values: number[]; explicit: boolean } {
  const row = rowOf(score, pattern, sound);
  const steps = drumSteps(score, pattern, sound);
  const given = typeof row === "string" || Array.isArray(row) ? undefined : row[lane];
  if (Array.isArray(given)) return { values: steps.map((h, i) => given[i] ?? laneDefault(lane, h)), explicit: true };
  return { values: steps.map((h) => laneDefault(lane, h)), explicit: false };
}

export function setLaneValue(score: Score, pattern: string, sound: string, lane: Lane, step: number, value: number): Score {
  if (!(LANES as readonly string[]).includes(lane)) throw new ModelError(`${String(lane)} is not a lane`);
  if (!Number.isFinite(value)) throw new ModelError("a lane value is a number");
  const steps = drumSteps(score, pattern, sound);
  if (step < 0 || step >= steps.length) throw new ModelError(`step ${step} is outside the row`);
  const { values } = laneValues(score, pattern, sound, lane);
  values[step] = clampLane(lane, value);
  const out = copy(score);
  const rows = (out.patterns[pattern] as PatternJson).rows;
  const row = rows[sound] as string | DrumJson;
  const obj: DrumJson = typeof row === "string" ? { steps: row } : row;
  // a rest does not play, so its value never keeps a lane alive
  const plain = values.every((v, i) => steps[i] === "." || v === laneDefault(lane, steps[i] as Hit));
  if (plain) delete obj[lane];
  else obj[lane] = values;
  const left = Object.keys(obj).filter((k) => k !== "steps");
  rows[sound] = left.length === 0 ? obj.steps : obj;
  return out;
}

// ---------- notes ----------

const PITCH: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];

export function noteNumber(name: string): number | null {
  const m = /^([A-G])([#b]?)(-?\d{1,2})$/.exec(name);
  if (!m) return null;
  const pc = (PITCH[m[1] as string] as number) + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0);
  const n = (Number(m[3]) + 1) * 12 + pc;
  return n >= 0 && n <= 127 ? n : null;
}

export function noteName(midi: number, flats: boolean): string {
  const names = flats ? FLATS : SHARPS;
  return `${names[midi % 12] as string}${Math.floor(midi / 12) - 1}`;
}

// a key written with a flat tonic spells its notes with flats
export function usesFlats(score: Score): boolean {
  return /^[A-G]b /.test(score.key) || /^F (major|lydian|mixolydian)/.test(score.key);
}

export interface NoteView {
  index: number;
  step: number;
  len: number;
  midi: number;
  vel: number;
  passing: boolean;
}

function noteRow(score: Score, pattern: string, sound: string): NoteJson[] {
  const row = rowOf(score, pattern, sound);
  if (!isNoteRow(row)) throw new ModelError(`${sound} in ${pattern} is not a note row`);
  return row;
}

export function notesOf(score: Score, pattern: string, sound: string): NoteView[] {
  return noteRow(score, pattern, sound).flatMap((n, index) => {
    const midi = typeof n.note === "string" ? noteNumber(n.note) : null;
    if (midi === null || typeof n.step !== "number" || typeof n.len !== "number") return [];
    return [{ index, step: n.step, len: n.len, midi, vel: typeof n.vel === "number" ? n.vel : 0.8, passing: n.passing === true }];
  });
}

const clampInt = (v: number, low: number, high: number): number => Math.min(high, Math.max(low, Math.round(v)));

export interface NewNote {
  step: number;
  len: number;
  midi: number;
  vel: number;
}

export function addNote(score: Score, pattern: string, sound: string, note: NewNote): { score: Score; index: number } {
  const end = patternSteps(score, pattern);
  const notes = noteRow(score, pattern, sound);
  const step = clampInt(note.step, 0, end - 1);
  const made: NoteJson = {
    step,
    len: clampInt(note.len, 1, end - step),
    note: noteName(clampInt(note.midi, 0, 127), usesFlats(score)),
    vel: Math.min(1, Math.max(0, note.vel)),
  };
  let index = notes.findIndex((n) => typeof n.step === "number" && n.step > step);
  if (index < 0) index = notes.length;
  const out = copy(score);
  (noteRow(out, pattern, sound)).splice(index, 0, made);
  return { score: out, index };
}

function editNote(score: Score, pattern: string, sound: string, index: number, f: (n: NoteJson, end: number) => void): Score {
  const end = patternSteps(score, pattern);
  if (!noteRow(score, pattern, sound)[index]) throw new ModelError(`no note ${index} in ${sound} of ${pattern}`);
  const out = copy(score);
  f(noteRow(out, pattern, sound)[index] as NoteJson, end);
  return out;
}

export function moveNote(score: Score, pattern: string, sound: string, index: number, to: { step?: number; midi?: number }): Score {
  return editNote(score, pattern, sound, index, (n, end) => {
    if (to.step !== undefined) n.step = clampInt(to.step, 0, end - n.len);
    if (to.midi !== undefined) {
      const midi = clampInt(to.midi, 0, 127);
      if (noteNumber(n.note) !== midi) n.note = noteName(midi, usesFlats(score));
    }
  });
}

export function resizeNote(score: Score, pattern: string, sound: string, index: number, len: number): Score {
  return editNote(score, pattern, sound, index, (n, end) => {
    n.len = clampInt(len, 1, end - n.step);
  });
}

export function setNoteVelocity(score: Score, pattern: string, sound: string, index: number, vel: number): Score {
  return editNote(score, pattern, sound, index, (n) => {
    n.vel = round(Math.min(1, Math.max(0, vel)), 0.01);
  });
}

export function deleteNote(score: Score, pattern: string, sound: string, index: number): Score {
  if (!noteRow(score, pattern, sound)[index]) throw new ModelError(`no note ${index} in ${sound} of ${pattern}`);
  const out = copy(score);
  noteRow(out, pattern, sound).splice(index, 1);
  return out;
}

// ---------- sounds and mix ----------

export function setSoundParam(score: Score, sound: string, param: string, value: ParamValue): Score {
  if (!score.sounds[sound]) throw new ModelError(`no sound ${sound}`);
  const out = copy(score);
  const s = out.sounds[sound] as SoundJson;
  s.params = { ...(s.params ?? {}), [param]: value };
  return out;
}

function mixOf(out: Score, sound: string): SoundMixJson {
  if (!out.sounds[sound]) throw new ModelError(`no sound ${sound}`);
  out.mix ??= {};
  out.mix.sounds ??= {};
  out.mix.sounds[sound] ??= {};
  return out.mix.sounds[sound];
}

export function soundMix(score: Score, sound: string): { gain_db: number; pan: number; rack: RackSlotJson[]; sidechain: string | null } {
  const m = score.mix?.sounds?.[sound] ?? {};
  return { gain_db: m.gain_db ?? 0, pan: m.pan ?? 0, rack: m.rack ?? [], sidechain: m.sidechain ?? null };
}

export function setGain(score: Score, sound: string, db: number): Score {
  const out = copy(score);
  mixOf(out, sound).gain_db = Math.min(GAIN_DB[1], Math.max(GAIN_DB[0], db));
  return out;
}

export function setPan(score: Score, sound: string, pan: number): Score {
  const out = copy(score);
  mixOf(out, sound).pan = Math.min(PAN[1], Math.max(PAN[0], pan));
  return out;
}

export function setRack(score: Score, sound: string, rack: RackSlotJson[]): Score {
  if (rack.length > MAX_RACK) throw new ModelError(`a rack takes at most ${MAX_RACK} effects`);
  const out = copy(score);
  mixOf(out, sound).rack = structuredClone(rack);
  return out;
}

// ---------- song ----------

export function songBars(score: Score): number {
  return score.sections.reduce((sum, s) => sum + (typeof s.bars === "number" ? s.bars : 0), 0);
}

export function sectionStarts(score: Score): number[] {
  let at = 0;
  return score.sections.map((s) => {
    const start = at;
    at += typeof s.bars === "number" ? s.bars : 0;
    return start;
  });
}

export interface Located {
  section: number;
  start: number;
  barInSection: number;
  last: boolean;
}

export function locate(score: Score, bar: number): Located | null {
  if (bar < 0) return null;
  const starts = sectionStarts(score);
  for (const [i, s] of score.sections.entries()) {
    const start = starts[i] as number;
    if (bar < start + s.bars) return { section: i, start, barInSection: bar - start, last: bar === start + s.bars - 1 };
  }
  return null;
}

export interface PlayingRow {
  pattern: string;
  sound: string;
  // the pattern's bar heard at this song bar
  patternBar: number;
  fill: boolean;
  // a fill on this bar has a row for the sound, so this row is silent here
  replaced: boolean;
  muted: boolean;
}

const sortedRows = (p: PatternJson): string[] => Object.keys(p.rows ?? {}).sort();

export function playingAt(score: Score, bar: number): PlayingRow[] {
  const at = locate(score, bar);
  if (!at) return [];
  const section = score.sections[at.section] as SectionJson;
  const mute = new Set(section.mute ?? []);
  const fill = at.last && section.fill ? score.patterns[section.fill] : undefined;
  const filled = new Set(fill ? Object.keys(fill.rows ?? {}) : []);
  const out: PlayingRow[] = [];
  for (const name of section.play ?? []) {
    const p = score.patterns[name];
    if (!p) continue;
    for (const sound of sortedRows(p)) {
      out.push({ pattern: name, sound, patternBar: at.barInSection % p.bars, fill: false, replaced: filled.has(sound), muted: mute.has(sound) });
    }
  }
  if (fill && section.fill) {
    for (const sound of sortedRows(fill)) out.push({ pattern: section.fill, sound, patternBar: 0, fill: true, replaced: false, muted: mute.has(sound) });
  }
  return out;
}

// the sections that play a pattern or use it as their fill
export function usage(score: Score, pattern: string): number[] {
  return score.sections.flatMap((s, i) => ((s.play ?? []).includes(pattern) || s.fill === pattern ? [i] : []));
}

export function freshName(taken: Iterable<string>, base: string): string {
  const names = new Set(taken);
  const stem = base.replace(/_\d+$/, "") || "p";
  for (let n = 2; ; n++) {
    const suffix = `_${n}`;
    const name = stem.slice(0, 24 - suffix.length) + suffix;
    if (!names.has(name) && NAME.test(name)) return name;
  }
}

export function makeUnique(score: Score, section: number, pattern: string): { score: Score; name: string } {
  const s = score.sections[section];
  const p = score.patterns[pattern];
  if (!s || !p) throw new ModelError(`no section ${section} or pattern ${pattern}`);
  const inPlay = (s.play ?? []).includes(pattern);
  if (!inPlay && s.fill !== pattern) throw new ModelError(`section ${section} does not play ${pattern}`);
  const name = freshName(Object.keys(score.patterns), pattern);
  const out = copy(score);
  out.patterns[name] = structuredClone(p);
  const target = out.sections[section] as SectionJson;
  if (inPlay) target.play = target.play.map((x) => (x === pattern ? name : x));
  else target.fill = name;
  return { score: out, name };
}

// the score the engine hears: solo and mute as section mutes on a copy
export function liveScore(score: Score, muted: ReadonlySet<string>, solo: ReadonlySet<string>): Score {
  if (muted.size === 0 && solo.size === 0) return score;
  const names = Object.keys(score.sounds).sort();
  const silent = names.filter((n) => muted.has(n) || (solo.size > 0 && !solo.has(n)));
  if (!silent.length) return score;
  const out = copy(score);
  for (const s of out.sections) {
    const mute = [...(s.mute ?? [])];
    for (const n of silent) if (!mute.includes(n)) mute.push(n);
    s.mute = mute;
  }
  return out;
}

// ---------- time ----------

export function stepFrames(bpm: number, rate: number): number {
  return (rate * 60) / bpm / 4;
}

export function barFrame(bar: number, bpm: number, rate: number): number {
  return Math.round(bar * STEPS_PER_BAR * stepFrames(bpm, rate));
}

export function barAtFrame(frame: number, bpm: number, rate: number): number {
  return Math.floor(frame / (STEPS_PER_BAR * stepFrames(bpm, rate)));
}

// 1-based bar, beat and 16th within the beat
export function position(frame: number, bpm: number, rate: number): { bar: number; beat: number; step: number } {
  const steps = Math.floor(frame / stepFrames(bpm, rate) + 1e-9);
  return { bar: Math.floor(steps / 16) + 1, beat: Math.floor((steps % 16) / 4) + 1, step: (steps % 4) + 1 };
}

// ---------- event table ----------

export interface TableEvent {
  id: number;
  sound: number;
  start: number;
  off: number | null;
  end: number | null;
  note: number;
  vel: number;
  decay: number;
  roll: number;
  legato_from: number | null;
  section: number;
  bar: number;
  step: number;
}

export interface EventTable {
  length: number;
  rate: number;
  key: { tonic: number; mode: string };
  sounds: { name: string; instrument: string }[];
  events: TableEvent[];
}

export interface Glide {
  sound: string;
  from: number;
  to: number;
  start: number;
  bar: number;
  step: number;
  ms: number;
}

export function glideCurves(table: EventTable, score: Score, instruments: readonly InstrumentSpec[]): Glide[] {
  const out: Glide[] = [];
  for (const e of table.events) {
    if (e.legato_from === null || e.legato_from === undefined) continue;
    const sound = table.sounds[e.sound];
    if (!sound) continue;
    const given = score.sounds[sound.name]?.params?.["glide_ms"];
    const spec = instruments.find((i) => i.instrument === sound.instrument)?.params.find((p) => p.name === "glide_ms");
    const ms = typeof given === "number" ? given : (spec?.default ?? 0);
    out.push({ sound: sound.name, from: e.legato_from, to: e.note, start: e.start, bar: e.bar, step: e.step, ms });
  }
  return out;
}

// ---------- checks and files ----------

export type Target =
  | { kind: "row"; pattern: string; sound: string; step: number | null; note: number | null; bar: number }
  | { kind: "bar"; bar: number }
  | { kind: "sound"; sound: string };

// the first song bar a pattern's bar is heard at
function barOfPattern(score: Score, pattern: string, patternBar: number): number | null {
  const starts = sectionStarts(score);
  let first: number | null = null;
  for (const i of usage(score, pattern)) {
    const s = score.sections[i] as SectionJson;
    const start = starts[i] as number;
    if (s.fill === pattern && !(s.play ?? []).includes(pattern)) {
      first ??= start + s.bars - 1;
      continue;
    }
    const bars = score.patterns[pattern]?.bars ?? 1;
    for (let b = 0; b < s.bars; b++) if (b % bars === patternBar) return start + b;
  }
  return first;
}

export function targetOf(path: string, score: Score): Target | null {
  let m = /^patterns\.([a-z0-9_]+)\.rows\.([a-z0-9_]+)(.*)$/.exec(path);
  if (m) {
    const [, pattern, sound, rest] = m as unknown as [string, string, string, string];
    const p = score.patterns[pattern];
    if (!p || p.rows?.[sound] === undefined) return null;
    const lane = /^\.(velocity|pitch|decay|chance)\[(\d+)\]/.exec(rest);
    const noteAt = /^\[(\d+)\]/.exec(rest);
    let step: number | null = lane ? Number(lane[2]) : null;
    let at = step ?? 0;
    let note: number | null = null;
    const row = p.rows[sound];
    if (noteAt && Array.isArray(row)) {
      const n = row[Number(noteAt[1])];
      if (n) note = Number(noteAt[1]);
      if (n && typeof n.step === "number") at = n.step;
    }
    const bar = barOfPattern(score, pattern, Math.floor(at / STEPS_PER_BAR)) ?? 0;
    if (step !== null && step >= p.bars * STEPS_PER_BAR) step = null;
    return { kind: "row", pattern, sound, step, note, bar };
  }
  m = /^sections\[(\d+)\]/.exec(path);
  if (m) {
    const i = Number(m[1]);
    const start = sectionStarts(score)[i];
    return start === undefined ? null : { kind: "bar", bar: start };
  }
  m = /^bars? (\d+)/.exec(path);
  if (m) {
    const bar = Number(m[1]) - 1;
    return bar >= 0 && bar < songBars(score) ? { kind: "bar", bar } : null;
  }
  m = /^(?:mix\.)?sounds\.([a-z0-9_]+)/.exec(path);
  if (m && score.sounds[m[1] as string]) return { kind: "sound", sound: m[1] as string };
  return null;
}

export function nextScoreName(base: string): string | null {
  const m = /^score-(\d+)\.json$/.exec(base);
  return m ? `score-${Number(m[1]) + 1}.json` : null;
}

// the path of a "path: message" line, for errors that come as text
export function splitIssue(line: string): { path: string; message: string } {
  const at = line.indexOf(": ");
  return at < 0 ? { path: "", message: line } : { path: line.slice(0, at), message: line.slice(at + 2) };
}

export interface Faults {
  // "pattern/sound"
  rows: Set<string>;
  notes: Map<string, Set<number>>;
  steps: Map<string, Set<number>>;
}

// what a list of "path: message" errors points at inside the patterns
export function faultsOf(errors: readonly string[], score: Score): Faults {
  const out: Faults = { rows: new Set(), notes: new Map(), steps: new Map() };
  const add = (map: Map<string, Set<number>>, key: string, n: number) => {
    let set = map.get(key);
    if (!set) map.set(key, (set = new Set()));
    set.add(n);
  };
  for (const line of errors) {
    const t = targetOf(splitIssue(line).path, score);
    if (!t || t.kind !== "row") continue;
    const key = `${t.pattern}/${t.sound}`;
    out.rows.add(key);
    if (t.note !== null) add(out.notes, key, t.note);
    if (t.step !== null) add(out.steps, key, t.step);
  }
  return out;
}

export interface BarNote {
  index: number;
  // steps from the start of the bar shown
  start: number;
  len: number;
  midi: number;
  // the note began in an earlier bar, or goes on into the next
  cutStart: boolean;
  cutEnd: boolean;
}

export function notesInBar(notes: readonly NoteView[], patternBar: number): BarNote[] {
  const from = patternBar * STEPS_PER_BAR;
  const to = from + STEPS_PER_BAR;
  return notes.flatMap((n) => {
    const a = Math.max(n.step, from);
    const b = Math.min(n.step + n.len, to);
    if (b <= a) return [];
    return [{ index: n.index, start: a - from, len: b - a, midi: n.midi, cutStart: n.step < from, cutEnd: n.step + n.len > to }];
  });
}

// a glide of the sound placed in steps from the start of the song bar shown
export function glidesInBar(glides: readonly Glide[], sound: string, bar: number, bpm: number, rate: number): { from: number; to: number; x: number; w: number }[] {
  const step = stepFrames(bpm, rate);
  const origin = barFrame(bar, bpm, rate);
  return glides.flatMap((g) => {
    if (g.sound !== sound) return [];
    const x = (g.start - origin) / step;
    if (x < 0 || x >= STEPS_PER_BAR) return [];
    return [{ from: g.from, to: g.to, x: Number(x.toFixed(6)), w: Number((((g.ms / 1000) * rate) / step).toFixed(6)) }];
  });
}
