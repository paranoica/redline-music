// The drum grid of the bottom editor: 16 steps of every drum row the selected block plays on one
// of its bars. An own beat's block plays its pattern's bar round and round, so a click lands on
// that pattern bar and is heard wherever the pattern plays it. A flip block has six rows of its
// own, empty until a step is set.

import { instrumentLabel } from "../beat/labels.ts";
import { drumSteps, isNoteRow, playingAt, ROLLS, setHit, STEPS_PER_BAR, type Hit, type Score } from "../beat/model.ts";
import { DRUM_ROWS, type FlipDoc, type Refusal } from "../flip/model.ts";
import { h } from "../ui/dom.ts";
import type { FlipOp } from "./flipdraft.ts";
import { layerProblem } from "./roll.ts";

export function patternStep(blockBar: number, patternBars: number, col: number): number {
  return (blockBar % Math.max(1, patternBars)) * STEPS_PER_BAR + col;
}

export interface DrumRow {
  pattern: string;
  sound: string;
  instrument: string;
  // the 16 steps of the bar
  hits: Hit[];
  // the pattern step of the bar's first column
  base: number;
  fill: boolean;
  muted: boolean;
}

// the drum rows heard on a song bar; a row a fill replaces there gives way to the fill's
export function drumRows(score: Score, songBar: number): DrumRow[] {
  const out: DrumRow[] = [];
  for (const r of playingAt(score, songBar)) {
    if (r.replaced) continue;
    const row = score.patterns[r.pattern]?.rows[r.sound];
    if (row === undefined || isNoteRow(row)) continue;
    const base = r.patternBar * STEPS_PER_BAR;
    let hits: Hit[];
    try {
      hits = drumSteps(score, r.pattern, r.sound).slice(base, base + STEPS_PER_BAR);
    } catch {
      continue;
    }
    while (hits.length < STEPS_PER_BAR) hits.push(".");
    out.push({ pattern: r.pattern, sound: r.sound, instrument: score.sounds[r.sound]?.instrument ?? "", hits, base, fill: r.fill, muted: r.muted });
  }
  return out;
}

// a click: a rest gets a hit (or the roll picked), any hit goes
export function toggleHit(hit: Hit, roll: number): Hit {
  if (hit !== ".") return ".";
  return (ROLLS as readonly number[]).includes(roll) ? (String(roll) as Hit) : "x";
}

// ---------- the blocks the editor picks ----------

export interface Block {
  section: number;
  fromBar: number;
  bars: number;
  label: string;
}

export interface BeatEditing {
  score(): Score | null;
  block(): Block | null;
  // an edit of `pattern` for the block's section; the editor may ask about a shared pattern
  // first, so the change gets the pattern name it should write to
  edit(section: number, pattern: string, change: (score: Score, pattern: string) => Score): void;
}

export const ROLL_NAMES: readonly [number, string][] = [
  [0, "удар"],
  [2, "дробь 2"],
  [3, "дробь 3"],
  [4, "дробь 4"],
  [6, "дробь 6"],
];

// ---------- what the grid shows and edits ----------

export interface DrumRowView {
  label: string;
  title: string;
  muted: boolean;
  // the 16 steps of the bar
  hits: Hit[];
  // a click on column `col`; `roll` is what a click on a rest puts
  press(col: number, roll: number): void;
}

export interface DrumsData {
  // the block shown; another key goes back to its first bar
  key: string;
  bars: number;
  barTitle(bar: number): string;
  // whether the roll picker (what a click puts) is offered
  rolls: boolean;
  hint: string;
}

// `data` answers the words to show instead of a grid when there is none
export interface DrumsSource {
  data(): DrumsData | string;
  rows(bar: number): DrumRowView[];
  emptyBar: string;
}

export function scoreDrums(host: BeatEditing): DrumsSource {
  let block: Block | null = null;
  return {
    emptyBar: "В этом такте барабаны не играют.",
    data() {
      const score = host.score();
      block = host.block();
      if (!score || !block) return score ? "Выбери кусок на дорожке бита." : "Загружаю партитуру…";
      const from = block.fromBar;
      return {
        key: `${block.section}:${block.fromBar}:${block.bars}`,
        bars: block.bars,
        barTitle: (bar) => `Такт песни ${from + bar + 1}`,
        rolls: true,
        hint: "клик — поставить, ещё клик — убрать",
      };
    },
    rows(bar) {
      const score = host.score();
      const at = block;
      if (!score || !at) return [];
      return drumRows(score, at.fromBar + bar).map((row) => ({
        label: `${instrumentLabel(row.instrument)}${row.fill ? " · сбивка" : ""}`,
        title: `${row.sound} · паттерн ${row.pattern}${row.fill ? " (сбивка)" : ""}`,
        muted: row.muted,
        hits: row.hits,
        press: (col, roll) => host.edit(at.section, row.pattern, (s, p) => setHit(s, p, row.sound, row.base + col, toggleHit(row.hits[col] ?? ".", roll))),
      }));
    },
  };
}

// ---------- a flip block's rows ----------

// the instrument each flip row sounds as, for its name
const ROW_SOUND: Record<(typeof DRUM_ROWS)[number], string> = { kick: "kick", snare: "snare", clap: "clap", hat: "hat_closed", hat_open: "hat_open", perc: "rim" };

export function flipRowLabel(row: string): string {
  return instrumentLabel(ROW_SOUND[row as keyof typeof ROW_SOUND] ?? row);
}

// the 16 steps of one bar of a block's row; a row the block lacks is all rests
export function flipDrumHits(drums: Record<string, string> | undefined, row: string, bar: number): Hit[] {
  const text = drums?.[row] ?? "";
  return Array.from({ length: STEPS_PER_BAR }, (_, col) => (text[bar * STEPS_PER_BAR + col] === "x" ? "x" : "."));
}

// Sets or clears one step of a block's row. A row left with no hit goes, and so does the block's
// drums when no row is left, so a block that was never given drums stays as it was.
export function setFlipDrum(doc: FlipDoc, i: number, row: string, bar: number, col: number, on: boolean): FlipDoc | Refusal {
  const block = doc.arrangement[i];
  if (!block) return { ok: false, reason: "Такого блока нет." };
  if (!(DRUM_ROWS as readonly string[]).includes(row)) return { ok: false, reason: `Строки ${row} нет.` };
  const steps = (block.to_bar - block.from_bar) * STEPS_PER_BAR;
  const at = bar * STEPS_PER_BAR + col;
  if (!Number.isInteger(bar) || !Number.isInteger(col) || bar < 0 || col < 0 || col >= STEPS_PER_BAR || at >= steps) return { ok: false, reason: "Шаг вне блока." };
  const drums = { ...(block.drums ?? {}) };
  const text = (drums[row] ?? ".".repeat(steps)).padEnd(steps, ".").slice(0, steps);
  drums[row] = `${text.slice(0, at)}${on ? "x" : "."}${text.slice(at + 1)}`;
  if (!drums[row].includes("x")) delete drums[row];
  if (Object.keys(drums).length) block.drums = drums;
  else delete block.drums;
  return doc;
}

export interface FlipDrumEditing {
  doc(): FlipDoc | null;
  apply(op: FlipOp): boolean;
  block(): Block | null;
}

// the six rows of the selected flip block, a bar at a time
export function flipDrums(host: FlipDrumEditing): DrumsSource {
  let at: Block | null = null;
  return {
    emptyBar: "",
    data() {
      const doc = host.doc();
      at = host.block();
      if (!doc) return "Загружаю флип…";
      const block = at ? doc.arrangement[at.section] : undefined;
      if (!at || !block) return "Выбери блок на дорожке бита.";
      const problem = layerProblem(doc);
      if (problem) return problem;
      const picked = at.section;
      return {
        key: `flip:${picked}:${block.from_bar}:${block.to_bar}`,
        bars: block.to_bar - block.from_bar,
        barTitle: (bar) => `Такт блока ${bar + 1}`,
        rolls: false,
        hint: "▶ — только наш слой, без исходника",
      };
    },
    rows(bar) {
      const doc = host.doc();
      const picked = at;
      const block = doc && picked ? doc.arrangement[picked.section] : undefined;
      if (!picked || !block) return [];
      return DRUM_ROWS.map((row) => {
        const hits = flipDrumHits(block.drums, row, bar);
        return {
          label: flipRowLabel(row),
          title: row === "perc" ? "перкуссия (римшот)" : row,
          muted: false,
          hits,
          press: (col) => {
            host.apply((d) => setFlipDrum(d, picked.section, row, bar, col, hits[col] === "."));
          },
        };
      });
    },
  };
}

// ---------- the view ----------

export class DrumsView {
  readonly root: HTMLElement;
  readonly #tools: HTMLElement;
  readonly #rows: HTMLElement;
  readonly #source: DrumsSource;
  #bar = 0;
  #roll = 0;
  #blockKey = "";

  constructor(source: DrumsSource) {
    this.#source = source;
    this.#tools = h("div", { class: "dtools" });
    this.#rows = h("div", { class: "drums" });
    this.root = h("div", { class: "dpane" }, this.#tools, this.#rows);
  }

  show(): void {
    const data = this.#source.data();
    if (typeof data === "string") {
      this.#tools.replaceChildren();
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, data));
      return;
    }
    if (data.key !== this.#blockKey) {
      this.#blockKey = data.key;
      this.#bar = 0;
    }
    this.#bar = Math.min(this.#bar, Math.max(0, data.bars - 1));
    this.#paintTools(data);
    const rows = this.#source.rows(this.#bar);
    if (!rows.length) {
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, this.#source.emptyBar));
      return;
    }
    const els: HTMLElement[] = [];
    for (const row of rows) {
      const name = h("div", { class: "dname", title: row.title }, h("i", {}), row.label);
      if (row.muted) name.classList.add("dname--muted");
      const steps = h("div", { class: "steps" });
      row.hits.forEach((hit, col) => {
        const rolls = hit === "2" || hit === "3" || hit === "4" || hit === "6";
        const cell = h("button", {
          class: `step${hit !== "." ? " on" : ""}${hit === "g" ? " soft" : ""}${rolls ? " roll" : ""}`,
          type: "button",
          "aria-pressed": String(hit !== "."),
          "aria-label": `${row.label}, шаг ${col + 1}`,
          "data-roll": rolls ? hit : undefined,
        });
        cell.addEventListener("click", () => row.press(col, this.#roll));
        steps.append(cell);
      });
      els.push(name, steps);
    }
    this.#rows.replaceChildren(...els);
  }

  #paintTools(data: DrumsData): void {
    const bars = h("div", { class: "barpick", role: "radiogroup", "aria-label": "Такт куска" });
    for (let b = 0; b < data.bars; b++) {
      const btn = h("button", { class: "chip", type: "button", role: "radio", "aria-checked": String(b === this.#bar), title: data.barTitle(b) }, String(b + 1));
      btn.addEventListener("click", () => {
        this.#bar = b;
        this.show();
      });
      bars.append(btn);
    }
    const tools: (Node | string)[] = [h("span", { class: "dtools__lbl" }, "такт"), bars];
    if (data.rolls) {
      const roll = h("select", { class: "rollpick", "aria-label": "Что ставит клик", title: "Что ставит клик по пустому шагу" });
      for (const [n, label] of ROLL_NAMES) roll.append(h("option", { value: String(n), selected: n === this.#roll }, label));
      roll.addEventListener("change", () => {
        this.#roll = Number(roll.value);
      });
      tools.push(roll);
    }
    tools.push(h("span", { class: "dtools__hint" }, data.hint));
    this.#tools.replaceChildren(...tools);
  }
}
