// The piano roll of the bottom editor: the 808 notes of the selected block. The block plays its
// pattern round and round, so every pass is drawn and each is the same notes; a press anywhere
// lands on the pattern. Click empty = add, drag = move, drag the right edge = resize, right click
// or Delete = remove. A note that starts while the one before still sounds glides from it.

import { addNote, deleteNote, isNoteRow, moveNote, noteName, notesOf, resizeNote, STEPS_PER_BAR, usesFlats, type NoteView, type Score } from "../beat/model.ts";
import { h } from "../ui/dom.ts";
import type { BeatEditing, Block } from "./drums.ts";

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

// ---------- the view ----------

const STEP_PX = 18;
const ROW_PX = 14;
const MAX_VIEW_BARS = 16;
const NEW_LEN = 2;
const SVG = "http://www.w3.org/2000/svg";
// frames to wait for the pane to be laid out before the first scroll
const SCROLL_TRIES = 10;

export class RollView {
  readonly root: HTMLElement;
  readonly #bar: HTMLElement;
  readonly #scroller: HTMLElement;
  readonly #host: BeatEditing;
  #chosen: number | null = null;
  #where: { section: number; pattern: string; sound: string } | null = null;
  #blockKey = "";
  #dragging = false;
  #scrollSeq = 0;

  constructor(host: BeatEditing) {
    this.#host = host;
    this.#bar = h("div", { class: "rollbar" });
    this.#scroller = h("div", { class: "roll", tabindex: 0, role: "application", "aria-roledescription": "пианоролл", "aria-label": "Ноты 808: клик — нота, тяни — сдвинуть, правый край — длина, правый клик или Delete — убрать" });
    this.root = h("div", { class: "rpane" }, this.#bar, this.#scroller);
    this.#scroller.addEventListener("keydown", (e) => {
      if ((e.key === "Delete" || e.key === "Backspace") && this.#chosen !== null && this.#where) {
        e.preventDefault();
        e.stopPropagation();
        const index = this.#chosen;
        const { section, pattern, sound } = this.#where;
        this.#chosen = null;
        this.#host.edit(section, pattern, (s, p) => deleteNote(s, p, sound, index));
      }
    });
  }

  show(): void {
    if (this.#dragging) return;
    const score = this.#host.score();
    const block = this.#host.block();
    if (!score || !block) {
      this.#bar.replaceChildren();
      this.#scroller.replaceChildren(h("p", { class: "epane__empty" }, score ? "Выбери кусок на дорожке бита." : "Загружаю партитуру…"));
      return;
    }
    const row = bassRowOf(score, block.section);
    if (!row) {
      this.#bar.replaceChildren();
      this.#scroller.replaceChildren(h("p", { class: "epane__empty" }, "В этом куске 808 не играет."));
      return;
    }
    const key = `${block.section}:${row.pattern}`;
    const fresh = key !== this.#blockKey;
    if (fresh) this.#chosen = null;
    this.#blockKey = key;
    this.#where = { section: block.section, ...row };
    this.#paint(score, block, row, fresh);
  }

  #paint(score: Score, block: Block, row: { pattern: string; sound: string }, fresh: boolean): void {
    const patternBars = score.patterns[row.pattern]?.bars ?? 1;
    const viewBars = Math.max(1, Math.min(MAX_VIEW_BARS, block.bars));
    const notes = notesOf(score, row.pattern, row.sound);
    const { lo, hi } = rollRange(notes.map((n) => n.midi));
    const l: RollLayout = { stepPx: STEP_PX, rowPx: ROW_PX, hi, lo, patternSteps: patternBars * STEPS_PER_BAR, viewSteps: viewBars * STEPS_PER_BAR };
    const flats = usesFlats(score);
    const passes = Math.ceil(viewBars / patternBars);
    this.#bar.replaceChildren(
      h("span", { class: "rollbar__what" }, `паттерн ${row.pattern} · ${patternBars} ${patternBars === 1 ? "такт" : patternBars < 5 ? "такта" : "тактов"}${passes > 1 ? `, в куске играет ${passes} раза` : ""}`),
    );

    const keys = h("div", { class: "keys" });
    const rows = h("div", { class: "grid__rows" });
    for (let m = hi; m >= lo; m--) {
      const name = noteName(m, flats);
      const black = name.length > 1 && (name[1] === "#" || name[1] === "b");
      keys.append(h("div", { class: `key${black ? " black" : ""}${m % 12 === 0 ? " c" : ""}` }, name));
      rows.append(h("div", { class: `grid__row${black ? " black" : ""}` }));
    }
    const area = h("div", { class: "grid" }, rows, h("div", { class: "grid__beats" }));
    area.style.setProperty("width", `${l.viewSteps * STEP_PX}px`);
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
        if (start >= l.viewSteps) continue;
        const len = Math.min(n.len, l.viewSteps - start);
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
    svg.setAttribute("viewBox", `0 0 ${l.viewSteps} ${hi - lo + 1}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    for (const g of glides) {
      const a = byIndex.get(g.from);
      const b = byIndex.get(g.to);
      if (!a || !b) continue;
      for (let pass = 0; pass < passes; pass++) {
        const x = pass * l.patternSteps + b.step;
        if (x >= l.viewSteps) continue;
        const line = document.createElementNS(SVG, "line");
        line.setAttribute("x1", String(x));
        line.setAttribute("y1", String(hi - a.midi + 0.5));
        line.setAttribute("x2", String(Math.min(l.viewSteps, x + Math.min(2, b.len))));
        line.setAttribute("y2", String(hi - b.midi + 0.5));
        line.setAttribute("class", "roll__glide");
        svg.append(line);
      }
    }
    area.append(svg);

    area.addEventListener("contextmenu", (e) => e.preventDefault());
    area.addEventListener("pointerdown", (e) => this.#press(e, area, l, notes, row));
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

  #press(e: PointerEvent, area: HTMLElement, l: RollLayout, notes: NoteView[], row: { pattern: string; sound: string }): void {
    const where = this.#where;
    if (!where) return;
    const { section } = where;
    const sound = row.sound;
    const rect = area.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const hit = rollHit(l, notes, x, y);
    this.#scroller.focus({ preventScroll: true });
    if (e.button === 2) {
      if (hit.kind === "note") {
        this.#chosen = null;
        this.#host.edit(section, row.pattern, (s, p) => deleteNote(s, p, sound, hit.index));
      }
      return;
    }
    if (e.button !== 0) return;
    e.preventDefault();
    if (hit.kind === "empty") {
      this.#host.edit(section, row.pattern, (s, p) => {
        const made = addNote(s, p, sound, { step: hit.step, len: NEW_LEN, midi: hit.midi, vel: 0.8 });
        this.#chosen = made.index;
        return made.score;
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
      this.#host.edit(section, row.pattern, (s, p) => ("len" in t ? resizeNote(s, p, sound, note.index, t.len) : moveNote(s, p, sound, note.index, { step: t.step, midi: t.midi })));
    };
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerup", up);
    area.addEventListener("pointercancel", up);
  }
}
