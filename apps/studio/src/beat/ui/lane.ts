// The step lane under the grid: velocity, pitch, decay or chance of the selected drum row's
// steps in the selected bar, one bar a step, set by dragging across them.

import { formatNumber } from "../../core/format.ts";
import { LANE_NAMES } from "../labels.ts";
import { LANES, LANE_SPECS, STEPS_PER_BAR, clampLane, drumSteps, laneDefault, laneValues, setLaneValue, type Hit, type Lane, type Score } from "../model.ts";
import { Segmented } from "../../ui/controls.ts";
import { h } from "../../ui/dom.ts";

export interface LaneTarget {
  score: Score;
  pattern: string;
  sound: string;
  patternBar: number;
  column: number;
}

export interface LaneHandlers {
  edit: (next: Score, commit: boolean) => void;
  column: (column: number) => void;
}

function shown(lane: Lane, v: number): string {
  if (lane === "pitch") return `${formatNumber(v, 0)} пт`;
  if (lane === "decay") return `×${formatNumber(v, 2)}`;
  return `${Math.round(v * 100)}%`;
}

export class StepLane {
  readonly root: HTMLElement;
  readonly #tabs: Segmented<Lane>;
  readonly #bars: HTMLElement;
  readonly #cells: HTMLElement[] = [];
  readonly #readout: HTMLElement;
  readonly #note: HTMLElement;
  readonly #on: LaneHandlers;
  #lane: Lane = "velocity";
  #target: LaneTarget | null = null;
  #drag: { id: number; start: Score; next: Score } | null = null;

  constructor(on: LaneHandlers) {
    this.#on = on;
    this.#tabs = new Segmented<Lane>(
      "Что показывает дорожка шагов",
      LANES.map((l) => ({ value: l, label: LANE_NAMES[l] })),
      "velocity",
      (l) => {
        this.#lane = l;
        this.#tabs.set(l);
        this.#render();
      },
      "seg seg--small",
    );
    this.#readout = h("span", { class: "lane2__value num", "aria-live": "polite" });
    this.#note = h("span", { class: "lane2__note" });
    for (let c = 0; c < STEPS_PER_BAR; c++) {
      this.#cells.push(h("span", { class: "lane2__step", "data-col": c }, h("span", { class: "lane2__bar" })));
    }
    this.#bars = h(
      "div",
      { class: "lane2__bars", role: "slider", tabindex: 0, "aria-label": "Значение шага: стрелки влево и вправо — шаг, вверх и вниз — значение, Delete — по умолчанию" },
      ...this.#cells,
    );
    this.root = h(
      "section",
      { class: "lane2", "aria-label": "Дорожка шагов" },
      h("header", { class: "lane2__head" }, h("h2", { class: "lane2__title" }, "Шаги"), this.#tabs.root, this.#readout, this.#note),
      this.#bars,
    );
    this.#wire();
  }

  get dragging(): boolean {
    return this.#drag !== null;
  }

  show(target: LaneTarget | null): void {
    this.#target = target;
    if (!this.#drag) this.#render();
  }

  #render(): void {
    const t = this.#target;
    this.root.dataset.empty = String(!t);
    if (!t) {
      this.#note.textContent = "выбери строку барабана";
      this.#readout.textContent = "";
      this.#bars.setAttribute("aria-disabled", "true");
      for (const cell of this.#cells) cell.dataset.rest = "true";
      return;
    }
    this.#bars.removeAttribute("aria-disabled");
    const lane = this.#lane;
    const spec = LANE_SPECS[lane];
    const { values, explicit } = laneValues(t.score, t.pattern, t.sound, lane);
    const hits = drumSteps(t.score, t.pattern, t.sound);
    const base = t.patternBar * STEPS_PER_BAR;
    // pitch grows both ways from 0; the rest from the bottom
    const zero = lane === "pitch" ? 0.5 : 0;
    this.#cells.forEach((cell, c) => {
      const v = values[base + c] ?? 0;
      const share = (v - spec.low) / (spec.high - spec.low);
      cell.dataset.rest = String((hits[base + c] ?? ".") === ".");
      cell.dataset.cursor = String(c === t.column);
      cell.style.setProperty("--from", String(Math.min(share, zero)));
      cell.style.setProperty("--to", String(Math.max(share, zero)));
      cell.title = `шаг ${c + 1}: ${shown(lane, v)}`;
    });
    const v = values[base + t.column] ?? 0;
    this.#readout.textContent = `шаг ${t.column + 1}: ${shown(lane, v)}`;
    this.#note.textContent = explicit ? `${t.sound} · своя дорожка` : `${t.sound} · по умолчанию`;
    this.#bars.setAttribute("aria-valuemin", String(spec.low));
    this.#bars.setAttribute("aria-valuemax", String(spec.high));
    this.#bars.setAttribute("aria-valuenow", String(v));
    this.#bars.setAttribute("aria-valuetext", `${LANE_NAMES[lane]}, шаг ${t.column + 1}: ${shown(lane, v)}`);
  }

  #valueAt(e: PointerEvent): { column: number; value: number } {
    const rect = this.#bars.getBoundingClientRect();
    const column = Math.min(STEPS_PER_BAR - 1, Math.max(0, Math.floor(((e.clientX - rect.left) / rect.width) * STEPS_PER_BAR)));
    const share = 1 - (e.clientY - rect.top) / rect.height;
    const spec = LANE_SPECS[this.#lane];
    return { column, value: clampLane(this.#lane, spec.low + share * (spec.high - spec.low)) };
  }

  #set(score: Score, column: number, value: number): Score {
    const t = this.#target as LaneTarget;
    return setLaneValue(score, t.pattern, t.sound, this.#lane, t.patternBar * STEPS_PER_BAR + column, value);
  }

  #wire(): void {
    this.#bars.addEventListener("pointerdown", (e) => {
      const t = this.#target;
      if (!t || e.button !== 0) return;
      e.preventDefault();
      this.#bars.focus();
      this.#bars.setPointerCapture(e.pointerId);
      const { column, value } = this.#valueAt(e);
      this.#drag = { id: e.pointerId, start: t.score, next: t.score };
      this.#apply(column, value);
    });
    this.#bars.addEventListener("pointermove", (e) => {
      if (!this.#drag || e.pointerId !== this.#drag.id) return;
      const { column, value } = this.#valueAt(e);
      this.#apply(column, value);
    });
    const end = (e: PointerEvent) => {
      const d = this.#drag;
      if (!d || e.pointerId !== d.id) return;
      this.#drag = null;
      if (d.next !== d.start) this.#on.edit(d.next, true);
      this.#render();
    };
    this.#bars.addEventListener("pointerup", end);
    this.#bars.addEventListener("pointercancel", end);
    this.#bars.addEventListener("keydown", (e) => {
      const t = this.#target;
      if (!t) return;
      const spec = LANE_SPECS[this.#lane];
      const step = spec.step * (e.shiftKey ? 10 : this.#lane === "pitch" ? 1 : 5);
      const { values } = laneValues(t.score, t.pattern, t.sound, this.#lane);
      const v = values[t.patternBar * STEPS_PER_BAR + t.column] ?? 0;
      const hit = (drumSteps(t.score, t.pattern, t.sound)[t.patternBar * STEPS_PER_BAR + t.column] ?? ".") as Hit;
      let next: number | null = null;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        e.stopPropagation();
        this.#on.column(Math.min(15, Math.max(0, t.column + (e.key === "ArrowLeft" ? -1 : 1))));
        return;
      }
      if (e.key === "ArrowUp") next = v + step;
      else if (e.key === "ArrowDown") next = v - step;
      else if (e.key === "Home") next = spec.low;
      else if (e.key === "End") next = spec.high;
      else if (e.key === "Delete" || e.key === "Backspace") next = laneDefault(this.#lane, hit);
      if (next === null) return;
      e.preventDefault();
      e.stopPropagation();
      this.#on.edit(this.#set(t.score, t.column, next), true);
    });
  }

  #apply(column: number, value: number): void {
    const d = this.#drag;
    const t = this.#target;
    if (!d || !t) return;
    const next = this.#set(d.next, column, value);
    d.next = next;
    this.#target = { ...t, score: next, column };
    this.#render();
    this.#on.edit(next, false);
  }
}
