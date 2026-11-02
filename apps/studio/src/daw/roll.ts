// The piano roll of the bottom editor. An own beat's roll shows the note row picked in the
// channel rack, a whole pattern wherever it plays; with none picked, the 808 of the selected
// block, which plays its pattern round and round, so every pass is drawn and each is the same
// notes. A flip block's notes are its own, bar after bar. Click empty = add, drag = move, drag the
// right edge = resize, right click or Delete = remove. A note that starts while the one before
// still sounds glides from it.

import { instrumentLabel } from "../beat/labels.ts";
import { addNote, deleteNote, isNoteRow, moveNote, noteName, notesOf, patternLabel, resizeNote, STEPS_PER_BAR, usesFlats, type NoteView, type Score } from "../beat/model.ts";
import type { FlipApi } from "../flip/api.ts";
import { draftProblem, STEPS_PER_BAR as FLIP_STEPS, type Block as FlipBlock, type FlipDoc, type FlipNote, type Refusal } from "../flip/model.ts";
import { h } from "../ui/dom.ts";
import type { BeatEditing, Block } from "./drums.ts";
import type { FlipOp } from "./flipdraft.ts";

export interface RollLayout {
  stepPx: number;
  rowPx: number;
  // the top and bottom rows' MIDI notes
  hi: number;
  lo: number;
  patternSteps: number;
  viewSteps: number;
}

// the last pixels of a note that resize it rather than move it
export const EDGE_PX = 4;

export function stepAt(l: RollLayout, x: number): number {
  return Math.min(l.viewSteps - 1, Math.max(0, Math.floor(x / l.stepPx)));
}

export function pitchAt(l: RollLayout, y: number): number {
  return Math.min(l.hi, Math.max(l.lo, l.hi - Math.floor(y / l.rowPx)));
}

export type RollHit = { kind: "empty"; step: number; midi: number } | { kind: "note"; index: number; mode: "move" | "resize" };

type NoteLike = Pick<NoteView, "index" | "step" | "len" | "midi">;

export function rollHit(l: RollLayout, notes: readonly NoteLike[], x: number, y: number): RollHit {
  const view = stepAt(l, x);
  const pass = Math.floor(view / l.patternSteps);
  const step = view - pass * l.patternSteps;
  const midi = pitchAt(l, y);
  let hit: NoteLike | null = null;
  for (const n of notes) if (n.midi === midi && n.step <= step && step < n.step + n.len) hit = n;
  if (!hit) return { kind: "empty", step, midi };
  const end = (pass * l.patternSteps + hit.step + hit.len) * l.stepPx;
  const wide = hit.len * l.stepPx > 2 * EDGE_PX;
  return { kind: "note", index: hit.index, mode: wide && x >= end - EDGE_PX ? "resize" : "move" };
}

// where a drag of (dx, dy) pixels takes a note; the model clamps it into the pattern
export function dragTarget(note: Pick<NoteView, "step" | "len" | "midi">, mode: "move" | "resize", dx: number, dy: number, l: RollLayout): { step: number; midi: number } | { len: number } {
  const steps = Math.round(dx / l.stepPx) || 0;
  if (mode === "resize") return { len: Math.max(1, note.len + steps) };
  const semis = Math.round(dy / l.rowPx) || 0;
  return { step: Math.max(0, note.step + steps), midi: Math.min(127, Math.max(0, note.midi - semis)) };
}

export function glidePairs(notes: readonly NoteLike[]): { from: number; to: number }[] {
  const order = [...notes].sort((a, b) => a.step - b.step);
  const out: { from: number; to: number }[] = [];
  for (const b of order) {
    let from: NoteLike | null = null;
    for (const a of order) if (a.step < b.step && b.step < a.step + a.len && (!from || a.step >= from.step)) from = a;
    if (from) out.push({ from: from.index, to: b.index });
  }
  return out;
}

// the pattern of the section that has the 808 note row, and the row's sound
export function bassRowOf(score: Score, section: number): { pattern: string; sound: string } | null {
  const s = score.sections[section];
  for (const pattern of s?.play ?? []) {
    const p = score.patterns[pattern];
    if (!p) continue;
    for (const sound of Object.keys(p.rows).sort()) {
      const row = p.rows[sound];
      if (row !== undefined && isNoteRow(row) && score.sounds[sound]?.instrument === "bass808") return { pattern, sound };
    }
  }
  return null;
}

export interface NoteRowRef {
  pattern: string;
  sound: string;
}

// the row the roll edits: the one picked in the rack while it is a note row, else the block's 808
export function rollRowOf(score: Score, section: number | null, chosen: NoteRowRef | null): (NoteRowRef & { chosen: boolean }) | null {
  if (chosen && Object.hasOwn(score.patterns, chosen.pattern) && Object.hasOwn(score.sounds, chosen.sound)) {
    const rows = score.patterns[chosen.pattern]?.rows ?? {};
    const row = Object.hasOwn(rows, chosen.sound) ? rows[chosen.sound] : undefined;
    if (row !== undefined && isNoteRow(row)) return { pattern: chosen.pattern, sound: chosen.sound, chosen: true };
  }
  if (section === null) return null;
  const old = bassRowOf(score, section);
  return old ? { ...old, chosen: false } : null;
}

export const BASS_INSTRUMENTS: readonly string[] = ["bass808", "reese", "sample808"];
const MELODY_LO = 48;
const MELODY_HI = 84;

// a bass keeps the 808's range; anything else that plays notes gets C3..C6, more if its notes are wider
export function rollRangeFor(instrument: string | undefined, midis: readonly number[]): { lo: number; hi: number } {
  if (instrument === undefined || BASS_INSTRUMENTS.includes(instrument)) return rollRange(midis);
  const lo = midis.length ? Math.min(MELODY_LO, Math.min(...midis) - 4) : MELODY_LO;
  const hi = midis.length ? Math.max(MELODY_HI, Math.max(...midis) + 4) : MELODY_HI;
  return { lo: Math.max(0, lo), hi: Math.min(127, hi) };
}

// pitches with room around the notes, at least two octaves
export function rollRange(midis: readonly number[]): { lo: number; hi: number } {
  let lo = midis.length ? Math.min(...midis) - 4 : 26;
  let hi = midis.length ? Math.max(...midis) + 4 : 50;
  if (hi - lo < 24) {
    const grow = 24 - (hi - lo);
    lo -= Math.floor(grow / 2);
    hi += Math.ceil(grow / 2);
  }
  return { lo: Math.max(0, lo), hi: Math.min(127, hi) };
}

// The scroll that brings the notes into a view `viewPx` tall: their span centred, or, when it is
// taller than the view, its lowest notes at the view's bottom.
export function rollScrollTop(l: Pick<RollLayout, "rowPx" | "hi" | "lo">, midis: readonly number[], viewPx: number): number {
  const height = (l.hi - l.lo + 1) * l.rowPx;
  const most = Math.max(0, height - viewPx);
  let top: number;
  if (!midis.length) top = height / 2 - viewPx / 2;
  else {
    const spanTop = (l.hi - Math.max(...midis)) * l.rowPx;
    const spanBottom = (l.hi - Math.min(...midis) + 1) * l.rowPx;
    top = spanBottom - spanTop <= viewPx ? (spanTop + spanBottom) / 2 - viewPx / 2 : spanBottom - viewPx;
  }
  return Math.round(Math.min(most, Math.max(0, top)));
}

// ---------- what the roll shows and edits ----------

export interface RollData {
  // the block shown; another key starts at the notes and drops the pick
  key: string;
  caption: string;
  hint?: string;
  label: string;
  notes: NoteView[];
  patternSteps: number;
  viewBars: number;
  flats: boolean;
  // the pitches shown; without it, two octaves around the notes
  range?: { lo: number; hi: number };
  chip?: { text: string; action: string; run: () => void };
}

// `data` answers the words to show instead of a roll when there is none
export interface RollSource {
  data(): RollData | string;
  add(step: number, midi: number, chose: (index: number) => void): void;
  remove(index: number): void;
  move(index: number, to: { step: number; midi: number }): void;
  resize(index: number, len: number): void;
}

const STEP_PX = 18;
const ROW_PX = 14;
const MAX_VIEW_BARS = 16;
const NEW_LEN = 2;

const barWord = (n: number): string => (n === 1 ? "такт" : n < 5 ? "такта" : "тактов");

export interface OwnRollEditing extends BeatEditing {
  // the note row picked in the rack, null for the block's 808
  notes(): NoteRowRef | null;
  // an edit of the whole pattern, heard wherever it plays: nothing to ask
  editPattern(change: (score: Score) => Score): void;
}

export function scoreRoll(host: OwnRollEditing): RollSource {
  let where: (NoteRowRef & { chosen: boolean; section: number | null }) | null = null;
  const edit = (change: (s: Score, pattern: string, sound: string) => Score): void => {
    const w = where;
    if (!w) return;
    if (w.chosen || w.section === null) host.editPattern((s) => change(s, w.pattern, w.sound));
    else host.edit(w.section, w.pattern, (s, p) => change(s, p, w.sound));
  };
  return {
    data() {
      const score = host.score();
      if (!score) return "Загружаю партитуру…";
      const block = host.block();
      const row = rollRowOf(score, block?.section ?? null, host.notes());
      where = null;
      if (!row) return block ? "В этом куске 808 не играет. Звук с нотами открывается в «Каналах»." : "Выбери кусок на дорожке бита.";
      where = { ...row, section: block?.section ?? null };
      const instrument = score.sounds[row.sound]?.instrument;
      const notes = notesOf(score, row.pattern, row.sound);
      const patternBars = score.patterns[row.pattern]?.bars ?? 1;
      const range = rollRangeFor(instrument, notes.map((n) => n.midi));
      if (row.chosen || !block) {
        const label = patternLabel(score, row.pattern, Object.keys(score.patterns).indexOf(row.pattern));
        return {
          key: `notes:${row.pattern}:${row.sound}`,
          caption: `${instrumentLabel(instrument ?? row.sound)} · ${row.sound} · ${label} · ${patternBars} ${barWord(patternBars)}, правка везде, где он играет`,
          label: "Ноты: клик — нота, тяни — сдвинуть, правый край — длина, правый клик или Delete — убрать",
          notes,
          patternSteps: patternBars * STEPS_PER_BAR,
          viewBars: patternBars,
          flats: usesFlats(score),
          range,
        };
      }
      const viewBars = Math.max(1, Math.min(MAX_VIEW_BARS, block.bars));
      const passes = Math.ceil(viewBars / patternBars);
      return {
        key: `${block.section}:${row.pattern}`,
        caption: `паттерн ${row.pattern} · ${patternBars} ${barWord(patternBars)}${passes > 1 ? `, в куске играет ${passes} раза` : ""}`,
        label: "Ноты 808: клик — нота, тяни — сдвинуть, правый край — длина, правый клик или Delete — убрать",
        notes,
        patternSteps: patternBars * STEPS_PER_BAR,
        viewBars,
        flats: usesFlats(score),
        range,
      };
    },
    add: (step, midi, chose) =>
      edit((s, p, sound) => {
        const made = addNote(s, p, sound, { step, len: NEW_LEN, midi, vel: 0.8 });
        chose(made.index);
        return made.score;
      }),
    remove: (index) => edit((s, p, sound) => deleteNote(s, p, sound, index)),
    move: (index, to) => edit((s, p, sound) => moveNote(s, p, sound, index, to)),
    resize: (index, len) => edit((s, p, sound) => resizeNote(s, p, sound, index, len)),
  };
}

// ---------- a flip block's notes ----------

export const MAX_NOTES_PER_BAR = 64;
const FLIP_VEL = 102;

// the source's 808 notes, one list per source bar with steps inside the bar
export type SourceBars = readonly (readonly FlipNote[])[];
export type SourceState = SourceBars | "loading" | { error: string };

const roomFor = (steps: number, step: number): number => steps + FLIP_STEPS - step;

// the notes a block with none of its own plays: its source bars' notes, steps from the block start
export function flipRollNotes(block: Pick<FlipBlock, "from_bar" | "to_bar">, bars: SourceBars): FlipNote[] {
  const steps = (block.to_bar - block.from_bar) * FLIP_STEPS;
  const out: FlipNote[] = [];
  for (let bar = block.from_bar; bar < block.to_bar; bar += 1) {
    for (const n of bars[bar] ?? []) {
      if (n.step < 0 || n.step >= FLIP_STEPS) continue;
      const step = (bar - block.from_bar) * FLIP_STEPS + n.step;
      out.push({ ...n, step, len: Math.max(1, Math.min(n.len, roomFor(steps, step))) });
    }
  }
  return out;
}

// why the synth refuses our notes and drums at this grid, null when it does not
export function layerProblem(doc: FlipDoc): string | null {
  const probe: FlipDoc = { ...doc, arrangement: doc.arrangement.map((b) => ({ ...b, drums: { kick: "", ...b.drums } })) };
  const problem = draftProblem(probe);
  return problem !== null && problem.includes("BPM, а в сетке") ? problem : null;
}

function crowded(notes: readonly FlipNote[], step: number, skip = -1): boolean {
  const bar = Math.floor(step / FLIP_STEPS);
  return notes.filter((n, k) => k !== skip && Math.floor(n.step / FLIP_STEPS) === bar).length >= MAX_NOTES_PER_BAR;
}

const CROWDED = `В такте не больше ${MAX_NOTES_PER_BAR} нот.`;

// the notes with a new one; the index it got, or the words it was refused with
export function addFlipNote(notes: readonly FlipNote[], steps: number, at: { step: number; midi: number }): { notes: FlipNote[]; index: number } | string {
  if (!Number.isInteger(at.step) || at.step < 0 || at.step >= steps) return "Нота вне блока.";
  if (crowded(notes, at.step)) return CROWDED;
  const note: FlipNote = { step: at.step, len: Math.min(NEW_LEN, roomFor(steps, at.step)), note: Math.min(127, Math.max(0, at.midi)), vel: FLIP_VEL };
  return { notes: [...notes, note], index: notes.length };
}

export function moveFlipNote(notes: readonly FlipNote[], steps: number, index: number, to: { step: number; midi: number }): FlipNote[] | string {
  const old = notes[index];
  if (!old) return "Такой ноты нет.";
  const step = Math.min(steps - 1, Math.max(0, Math.round(to.step)));
  if (Math.floor(step / FLIP_STEPS) !== Math.floor(old.step / FLIP_STEPS) && crowded(notes, step, index)) return CROWDED;
  const next = notes.map((n) => ({ ...n }));
  next[index] = { ...old, step, note: Math.min(127, Math.max(0, Math.round(to.midi))), len: Math.min(old.len, roomFor(steps, step)) };
  return next;
}

export function resizeFlipNote(notes: readonly FlipNote[], steps: number, index: number, len: number): FlipNote[] | string {
  const old = notes[index];
  if (!old) return "Такой ноты нет.";
  const next = notes.map((n) => ({ ...n }));
  next[index] = { ...old, len: Math.min(roomFor(steps, old.step), Math.max(1, Math.round(len))) };
  return next;
}

export function removeFlipNote(notes: readonly FlipNote[], index: number): FlipNote[] | string {
  if (!notes[index]) return "Такой ноты нет.";
  return notes.filter((_, k) => k !== index).map((n) => ({ ...n }));
}

// A roll edit of block i: the block's notes (its own, else a copy of the source's) go through
// `change`, and the 808 plays ours, which is the only mode that plays block notes.
export function editBlockNotes(doc: FlipDoc, i: number, source: SourceBars | null, change: (notes: FlipNote[]) => FlipNote[] | string): FlipDoc | Refusal {
  const block = doc.arrangement[i];
  if (!block) return { ok: false, reason: "Такого блока нет." };
  const current = block.notes ? block.notes.map((n) => ({ ...n })) : flipRollNotes(block, source ?? []);
  const next = change(current);
  if (typeof next === "string") return { ok: false, reason: next };
  block.notes = next;
  doc.bass808.mode = "ours";
  return doc;
}

export interface FlipEditing {
  doc(): FlipDoc | null;
  apply(op: FlipOp): boolean;
  block(): Block | null;
  source(): SourceState;
}

// the roll over the selected flip block: its own notes, or the source's until the first edit
export class FlipRoll implements RollSource {
  readonly #host: FlipEditing;
  // the 808 mode the first edit switched away from, for the chip that takes it back
  #switched: { was: FlipDoc["bass808"]["mode"] } | null = null;

  constructor(host: FlipEditing) {
    this.#host = host;
  }

  data(): RollData | string {
    const doc = this.#host.doc();
    const picked = this.#host.block();
    if (!doc) return "Загружаю флип…";
    const block = picked ? doc.arrangement[picked.section] : undefined;
    if (!picked || !block) return "Выбери блок на дорожке бита.";
    const problem = layerProblem(doc);
    if (problem) return problem;
    let notes: FlipNote[];
    if (block.notes) notes = block.notes;
    else {
      const source = this.#host.source();
      if (source === "loading") return "Загружаю ноты исходника…";
      if (!Array.isArray(source)) return `Ноты исходника не загрузились: ${(source as { error: string }).error}`;
      notes = flipRollNotes(block, source as SourceBars);
    }
    const bars = block.to_bar - block.from_bar;
    const data: RollData = {
      key: `flip:${picked.section}:${block.from_bar}:${block.to_bar}`,
      caption: `блок «${block.label}» · ${bars} ${barWord(bars)} · ${block.notes ? "свои ноты" : "ноты исходника"}`,
      hint: "▶ — только наш слой, без исходника",
      label: "Ноты 808 блока: клик — нота, тяни — сдвинуть, правый край — длина, правый клик или Delete — убрать",
      notes: notes.map((n, index) => ({ index, step: n.step, len: n.len, midi: n.note, vel: n.vel / 127, passing: false })),
      patternSteps: bars * FLIP_STEPS,
      viewBars: bars,
      flats: false,
    };
    const was = this.#switched?.was;
    if (was !== undefined && doc.bass808.mode === "ours") data.chip = { text: "808: наш", action: "вернуть", run: () => this.#restore(was) };
    return data;
  }

  add(step: number, midi: number, chose: (index: number) => void): void {
    this.#edit((notes, steps) => {
      const made = addFlipNote(notes, steps, { step, midi });
      if (typeof made === "string") return made;
      chose(made.index);
      return made.notes;
    });
  }

  remove(index: number): void {
    this.#edit((notes) => removeFlipNote(notes, index));
  }

  move(index: number, to: { step: number; midi: number }): void {
    this.#edit((notes, steps) => moveFlipNote(notes, steps, index, to));
  }

  resize(index: number, len: number): void {
    this.#edit((notes, steps) => resizeFlipNote(notes, steps, index, len));
  }

  #edit(change: (notes: FlipNote[], steps: number) => FlipNote[] | string): void {
    const picked = this.#host.block();
    if (!picked) return;
    const source = this.#host.source();
    let was: FlipDoc["bass808"]["mode"] | null = null;
    const done = this.#host.apply((doc) => {
      was = doc.bass808.mode;
      const block = doc.arrangement[picked.section];
      if (block && !block.notes && !Array.isArray(source)) return { ok: false, reason: "Ноты исходника ещё не загрузились." };
      const steps = block ? (block.to_bar - block.from_bar) * FLIP_STEPS : 0;
      return editBlockNotes(doc, picked.section, Array.isArray(source) ? (source as SourceBars) : null, (notes) => change(notes, steps));
    });
    if (done && was !== null && was !== "ours") this.#switched = { was };
  }

  #restore(was: FlipDoc["bass808"]["mode"]): void {
    this.#switched = null;
    this.#host.apply((doc) => {
      doc.bass808.mode = was;
      return doc;
    });
  }
}

// The source's 808 notes per bar, read once for a grid and kept; the draft goes to the server
// first because the notes are snapped to the grid the server has.
export class SourceNotes {
  readonly #api: Pick<FlipApi, "sourceNotes">;
  readonly #sync: () => Promise<unknown>;
  readonly #changed: () => void;
  #key = "";
  #state: SourceState = "loading";
  #asking = false;

  constructor(api: Pick<FlipApi, "sourceNotes">, sync: () => Promise<unknown>, changed: () => void) {
    this.#api = api;
    this.#sync = sync;
    this.#changed = changed;
  }

  // what is known for a grid; the first ask for it starts the read
  get(doc: FlipDoc | null): SourceState {
    if (!doc) return "loading";
    const key = JSON.stringify(doc.grid);
    if (key !== this.#key) {
      this.#key = key;
      this.#state = "loading";
      this.#asking = false;
    }
    if (this.#state === "loading" && !this.#asking) {
      this.#asking = true;
      void this.#read(key);
    }
    return this.#state;
  }

  async #read(key: string): Promise<void> {
    let state: SourceState;
    try {
      await this.#sync();
      state = await this.#api.sourceNotes();
    } catch (error) {
      state = { error: error instanceof Error ? error.message : String(error) };
    }
    if (key !== this.#key) return;
    this.#state = state;
    this.#changed();
  }

  // a failed read is tried again on the next ask
  retry(): void {
    if (this.#state !== "loading" && !Array.isArray(this.#state)) {
      this.#state = "loading";
      this.#asking = false;
    }
  }
}

// ---------- the view ----------

const SVG = "http://www.w3.org/2000/svg";
// frames to wait for the pane to be laid out before the first scroll
const SCROLL_TRIES = 10;

export class RollView {
  readonly root: HTMLElement;
  readonly #bar: HTMLElement;
  readonly #scroller: HTMLElement;
  readonly #source: RollSource;
  #chosen: number | null = null;
  #blockKey = "";
  #dragging = false;
  #scrollSeq = 0;

  constructor(source: RollSource) {
    this.#source = source;
    this.#bar = h("div", { class: "rollbar" });
    this.#scroller = h("div", { class: "roll", tabindex: 0, role: "application", "aria-roledescription": "пианоролл" });
    this.root = h("div", { class: "rpane" }, this.#bar, this.#scroller);
    this.#scroller.addEventListener("keydown", (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && this.#chosen !== null) {
        e.preventDefault();
        e.stopPropagation();
        const index = this.#chosen;
        this.#chosen = null;
        this.#source.remove(index);
      }
    });
  }

  // whether a note is picked: a Delete then belongs to the roll, not to the timeline's block
  get hasPick(): boolean {
    return this.#chosen !== null;
  }

  show(): void {
    if (this.#dragging) return;
    const data = this.#source.data();
    if (typeof data === "string") {
      this.#bar.replaceChildren();
      this.#scroller.replaceChildren(h("p", { class: "epane__empty" }, data));
      return;
    }
    const fresh = data.key !== this.#blockKey;
    if (fresh) this.#chosen = null;
    this.#blockKey = data.key;
    this.#paint(data, fresh);
  }

  #paint(data: RollData, fresh: boolean): void {
    const { notes, flats } = data;
    const viewSteps = data.viewBars * STEPS_PER_BAR;
    const { lo, hi } = data.range ?? rollRange(notes.map((n) => n.midi));
    const l: RollLayout = { stepPx: STEP_PX, rowPx: ROW_PX, hi, lo, patternSteps: data.patternSteps, viewSteps };
    const passes = Math.ceil(viewSteps / l.patternSteps);
    this.#scroller.setAttribute("aria-label", data.label);
    const bar: (Node | string)[] = [h("span", { class: "rollbar__what" }, data.caption)];
    const chip = data.chip;
    if (chip) {
      const back = h("button", { class: "chip chip--small", type: "button", title: "Вернуть режим 808, что был до правки" }, chip.action);
      back.addEventListener("click", chip.run);
      bar.push(h("span", { class: "rollbar__chip" }, chip.text, back));
    }
    if (data.hint) bar.push(h("span", { class: "rollbar__hint" }, data.hint));
    this.#bar.replaceChildren(...bar);

    const keys = h("div", { class: "keys" });
    const rows = h("div", { class: "grid__rows" });
    for (let m = hi; m >= lo; m--) {
      const name = noteName(m, flats);
      const black = name.length > 1 && (name[1] === "#" || name[1] === "b");
      keys.append(h("div", { class: `key${black ? " black" : ""}${m % 12 === 0 ? " c" : ""}` }, name));
      rows.append(h("div", { class: `grid__row${black ? " black" : ""}` }));
    }
    const area = h("div", { class: "grid" }, rows, h("div", { class: "grid__beats" }));
    area.style.setProperty("width", `${viewSteps * STEP_PX}px`);
    area.style.setProperty("height", `${(hi - lo + 1) * ROW_PX}px`);
    area.style.setProperty("--step", `${STEP_PX}px`);
    area.style.setProperty("--row", `${ROW_PX}px`);
    area.style.setProperty("--pattern", `${l.patternSteps * STEP_PX}px`);

    const glides = glidePairs(notes);
    const into = new Set(glides.map((g) => g.to));
    const byIndex = new Map(notes.map((n) => [n.index, n]));
    for (let pass = 0; pass < passes; pass++) {
      for (const n of notes) {
        const start = pass * l.patternSteps + n.step;
        if (start >= viewSteps) continue;
        const len = Math.min(n.len, viewSteps - start);
        const el = h("div", { class: `note${into.has(n.index) ? " glide" : ""}${pass > 0 ? " copy" : ""}`, "data-index": n.index, "data-selected": String(n.index === this.#chosen) }, noteName(n.midi, flats));
        el.style.setProperty("left", `${start * STEP_PX + 1}px`);
        el.style.setProperty("width", `${Math.max(3, len * STEP_PX - 2)}px`);
        el.style.setProperty("top", `${(hi - n.midi) * ROW_PX + 1}px`);
        el.style.setProperty("height", `${ROW_PX - 2}px`);
        area.append(el);
      }
    }
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "roll__glides");
    svg.setAttribute("viewBox", `0 0 ${viewSteps} ${hi - lo + 1}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    for (const g of glides) {
      const a = byIndex.get(g.from);
      const b = byIndex.get(g.to);
      if (!a || !b) continue;
      for (let pass = 0; pass < passes; pass++) {
        const x = pass * l.patternSteps + b.step;
        if (x >= viewSteps) continue;
        const line = document.createElementNS(SVG, "line");
        line.setAttribute("x1", String(x));
        line.setAttribute("y1", String(hi - a.midi + 0.5));
        line.setAttribute("x2", String(Math.min(viewSteps, x + Math.min(2, b.len))));
        line.setAttribute("y2", String(hi - b.midi + 0.5));
        line.setAttribute("class", "roll__glide");
        svg.append(line);
      }
    }
    area.append(svg);

    area.addEventListener("contextmenu", (e) => e.preventDefault());
    area.addEventListener("pointerdown", (e) => this.#press(e, area, l, notes));
    const keep = fresh ? null : { left: this.#scroller.scrollLeft, top: this.#scroller.scrollTop };
    this.#scroller.replaceChildren(h("div", { class: "roll__in" }, keys, area));
    if (keep) {
      this.#scroller.scrollLeft = keep.left;
      this.#scroller.scrollTop = keep.top;
    } else {
      this.#scroller.scrollLeft = 0;
      this.#scrollTo(l, notes.map((n) => n.midi));
    }
  }

  // the pane is drawn before the editor puts it on the page, where it has no height to scroll yet
  #scrollTo(l: RollLayout, midis: number[], tries = SCROLL_TRIES): void {
    const seq = ++this.#scrollSeq;
    const run = (left: number): void => {
      if (seq !== this.#scrollSeq) return;
      const view = this.#scroller.clientHeight;
      if ((!this.#scroller.isConnected || view <= 0) && left > 0) {
        requestAnimationFrame(() => run(left - 1));
        return;
      }
      this.#scroller.scrollTop = rollScrollTop(l, midis, view);
    };
    run(tries);
  }

  #press(e: PointerEvent, area: HTMLElement, l: RollLayout, notes: NoteView[]): void {
    const rect = area.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = rollHit(l, notes, x, y);
    this.#scroller.focus({ preventScroll: true });
    if (e.button === 2) {
      if (hit.kind === "note") {
        this.#chosen = null;
        this.#source.remove(hit.index);
      }
      return;
    }
    if (e.button !== 0) return;
    e.preventDefault();
    if (hit.kind === "empty") {
      this.#source.add(hit.step, hit.midi, (index) => {
        this.#chosen = index;
      });
      return;
    }
    const note = notes.find((n) => n.index === hit.index);
    if (!note) return;
    this.#chosen = note.index;
    for (const el of area.querySelectorAll<HTMLElement>(".note")) el.dataset["selected"] = String(Number(el.dataset["index"]) === note.index);
    const els = [...area.querySelectorAll<HTMLElement>(`.note[data-index="${note.index}"]`)];
    const origin = els.map((el) => ({ el, left: parseFloat(el.style.left), top: parseFloat(el.style.top), width: parseFloat(el.style.width) }));
    area.setPointerCapture(e.pointerId);
    this.#dragging = true;
    let target: ReturnType<typeof dragTarget> = hit.mode === "resize" ? { len: note.len } : { step: note.step, midi: note.midi };
    const move = (ev: PointerEvent): void => {
      target = dragTarget(note, hit.mode, ev.clientX - e.clientX, ev.clientY - e.clientY, l);
      for (const o of origin) {
        if ("len" in target) o.el.style.setProperty("width", `${Math.max(3, target.len * STEP_PX - 2)}px`);
        else {
          o.el.style.setProperty("left", `${o.left + (target.step - note.step) * STEP_PX}px`);
          o.el.style.setProperty("top", `${o.top + (note.midi - target.midi) * ROW_PX}px`);
        }
      }
    };
    const up = (): void => {
      area.removeEventListener("pointermove", move);
      area.removeEventListener("pointerup", up);
      area.removeEventListener("pointercancel", up);
      this.#dragging = false;
      const t = target;
      const same = "len" in t ? t.len === note.len : t.step === note.step && t.midi === note.midi;
      if (same) {
        this.show();
        return;
      }
      if ("len" in t) this.#source.resize(note.index, t.len);
      else this.#source.move(note.index, { step: t.step, midi: t.midi });
      this.show();
    };
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerup", up);
    area.addEventListener("pointercancel", up);
  }
}
