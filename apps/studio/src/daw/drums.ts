// The drum grid of the bottom editor: 16 steps of every drum row the selected block plays on one
// of its bars. A block's bar plays its pattern's bar round and round, so a click lands on that
// pattern bar and is heard wherever the pattern plays it.

import { instrumentLabel } from "../beat/labels.ts";
import { drumSteps, isNoteRow, playingAt, ROLLS, setHit, STEPS_PER_BAR, type Hit, type Score } from "../beat/model.ts";
import { h } from "../ui/dom.ts";

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

// ---------- the view ----------

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

const ROLL_NAMES: [number, string][] = [
  [0, "удар"],
  [2, "дробь 2"],
  [3, "дробь 3"],
  [4, "дробь 4"],
  [6, "дробь 6"],
];

export class DrumsView {
  readonly root: HTMLElement;
  readonly #tools: HTMLElement;
  readonly #rows: HTMLElement;
  readonly #host: BeatEditing;
  #bar = 0;
  #roll = 0;
  #blockKey = "";

  constructor(host: BeatEditing) {
    this.#host = host;
    this.#tools = h("div", { class: "dtools" });
    this.#rows = h("div", { class: "drums" });
    this.root = h("div", { class: "dpane" }, this.#tools, this.#rows);
  }

  show(): void {
    const score = this.#host.score();
    const block = this.#host.block();
    if (!score || !block) {
      this.#tools.replaceChildren();
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, score ? "Выбери кусок на дорожке бита." : "Загружаю партитуру…"));
      return;
    }
    const key = `${block.section}:${block.fromBar}:${block.bars}`;
    if (key !== this.#blockKey) {
      this.#blockKey = key;
      this.#bar = 0;
    }
    this.#bar = Math.min(this.#bar, Math.max(0, block.bars - 1));
    this.#paintTools(block);
    const rows = drumRows(score, block.fromBar + this.#bar);
    if (!rows.length) {
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, "В этом такте барабаны не играют."));
      return;
    }
    const els: HTMLElement[] = [];
    for (const row of rows) {
      const name = h("div", { class: "dname", title: `${row.sound} · паттерн ${row.pattern}${row.fill ? " (сбивка)" : ""}` }, h("i", {}), `${instrumentLabel(row.instrument)}${row.fill ? " · сбивка" : ""}`);
      if (row.muted) name.classList.add("dname--muted");
      const steps = h("div", { class: "steps" });
      row.hits.forEach((hit, col) => {
        const rolls = hit === "2" || hit === "3" || hit === "4" || hit === "6";
        const cell = h("button", {
          class: `step${hit !== "." ? " on" : ""}${hit === "g" ? " soft" : ""}${rolls ? " roll" : ""}`,
          type: "button",
          "aria-pressed": String(hit !== "."),
          "aria-label": `${instrumentLabel(row.instrument)}, шаг ${col + 1}`,
          "data-roll": rolls ? hit : undefined,
        });
        cell.addEventListener("click", () => {
          this.#host.edit(block.section, row.pattern, (s, p) => setHit(s, p, row.sound, row.base + col, toggleHit(hit, this.#roll)));
        });
        steps.append(cell);
      });
      els.push(name, steps);
    }
    this.#rows.replaceChildren(...els);
  }

  #paintTools(block: Block): void {
    const bars = h("div", { class: "barpick", role: "radiogroup", "aria-label": "Такт куска" });
    for (let b = 0; b < block.bars; b++) {
      const btn = h("button", { class: "chip", type: "button", role: "radio", "aria-checked": String(b === this.#bar), title: `Такт песни ${block.fromBar + b + 1}` }, String(b + 1));
      btn.addEventListener("click", () => {
        this.#bar = b;
        this.show();
      });
      bars.append(btn);
    }
    const roll = h("select", { class: "rollpick", "aria-label": "Что ставит клик", title: "Что ставит клик по пустому шагу" });
    for (const [n, label] of ROLL_NAMES) roll.append(h("option", { value: String(n), selected: n === this.#roll }, label));
    roll.addEventListener("change", () => {
      this.#roll = Number(roll.value);
    });
    this.#tools.replaceChildren(h("span", { class: "dtools__lbl" }, "такт"), bars, roll, h("span", { class: "dtools__hint" }, "клик — поставить, ещё клик — убрать"));
  }
}
