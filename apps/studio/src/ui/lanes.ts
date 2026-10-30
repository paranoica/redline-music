// The rack's automation under the waveform: a thin lane per automated param on the same time
// scale as the waveform. A press adds a point, a drag moves it, a double click removes it.

import { isLog, stepBy, type Range } from "../core/control-math.ts";
import { valueAt } from "../core/automation.ts";
import { effectInfo, paramLabel } from "../core/families.ts";
import { clock, formatValue } from "../core/format.ts";
import { insertPoint, movePoint, nearestPoint, removePoint, secondsToX, valueToY, withLane, xToSeconds, yToValue, type Point } from "../core/lanes.ts";
import type { Loop } from "../core/loop.ts";
import type { EffectSpec, ParamSpec, UserEffect } from "../types.ts";
import type { Handlers } from "./controls.ts";
import { h } from "./dom.ts";
import { isInteger } from "./rack.ts";

const PAD = 6;
const HIT_PX = 8;
const DOT = 6;
const NUDGE_S = 0.1;

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

interface Lane {
  effect: number;
  param: ParamSpec;
  range: Range;
  points: Point[];
  on: boolean;
  root: HTMLElement;
  canvas: HTMLCanvasElement;
  readout: HTMLElement;
  selected: number;
  dragging: boolean;
}

export class LanesView {
  readonly #root: HTMLElement;
  readonly #specs: Map<string, EffectSpec>;
  readonly #duration: number;
  readonly #on: Handlers<UserEffect[]>;
  #rack: UserEffect[] = [];
  #lanes: Lane[] = [];
  #shape = "";
  #position = 0;
  #loop: Loop = { start: 0, end: 0 };
  #loopOn = true;
  #frame = 0;

  constructor(root: HTMLElement, effects: readonly EffectSpec[], duration: number, on: Handlers<UserEffect[]>) {
    this.#root = root;
    this.#specs = new Map(effects.map((e) => [e.effect, e]));
    this.#duration = Math.max(1e-3, duration);
    this.#on = on;
    new ResizeObserver(() => this.#invalidate()).observe(root);
  }

  show(rack: readonly UserEffect[]): void {
    this.#rack = structuredClone([...rack]);
    const found: { effect: number; param: ParamSpec; points: Point[]; on: boolean }[] = [];
    this.#rack.forEach((e, i) => {
      const spec = this.#specs.get(e.effect);
      for (const lane of e.automation ?? []) {
        const param = spec?.params.find((p) => p.name === lane.param);
        if (param) found.push({ effect: i, param, points: lane.points.map((p) => [p[0], p[1]]), on: e.on });
      }
    });
    const shape = JSON.stringify(found.map((f) => [f.effect, this.#rack[f.effect]?.effect, f.param.name]));
    if (shape !== this.#shape) {
      this.#shape = shape;
      this.#lanes = found.map((f) => this.#build(f.effect, f.param));
      this.#root.replaceChildren(...this.#lanes.map((l) => l.root));
      this.#root.hidden = this.#lanes.length === 0;
    }
    found.forEach((f, i) => {
      const lane = this.#lanes[i];
      if (!lane) return;
      lane.points = f.points;
      lane.on = f.on;
      lane.root.dataset.on = String(f.on);
      lane.selected = Math.min(lane.selected, lane.points.length - 1);
      this.#readout(lane);
    });
    this.#invalidate();
  }

  setPosition(seconds: number): void {
    if (Math.abs(seconds - this.#position) < 1e-4) return;
    this.#position = seconds;
    if (this.#lanes.length) this.#invalidate();
  }

  setLoop(loop: Loop, on: boolean): void {
    this.#loop = loop;
    this.#loopOn = on;
    if (this.#lanes.length) this.#invalidate();
  }

  #build(effect: number, param: ParamSpec): Lane {
    const name = `${String(effect + 1).padStart(2, "0")} ${effectInfo(this.#rack[effect]?.effect ?? "").label} · ${paramLabel(param.name)}`;
    const range: Range = { low: param.low, high: param.high, log: isLog(param.low, param.high, param.unit), step: isInteger(param) ? 1 : undefined };
    const canvas = h("canvas", {
      class: "lane__canvas",
      tabindex: 0,
      "data-cursor": "add",
      "aria-label": `Автоматизация: ${name}. Стрелки влево и вправо выбирают точку, вверх и вниз меняют значение, Alt со стрелками двигает по времени, Enter ставит точку на позиции, Delete убирает`,
    });
    const readout = h("span", { class: "lane__value num", "aria-live": "polite" });
    const root = h("div", { class: "lane" }, canvas, h("span", { class: "lane__label" }, h("span", { class: "lane__name" }, name), readout));
    const lane: Lane = { effect, param, range, points: [], on: true, root, canvas, readout, selected: -1, dragging: false };
    this.#wire(lane);
    return lane;
  }

  #emit(lane: Lane, points: Point[], commit: boolean): void {
    const rack = structuredClone(this.#rack);
    const e = rack[lane.effect];
    if (!e) return;
    e.automation = withLane(e.automation ?? [], lane.param.name, points);
    this.#rack = rack;
    lane.points = points;
    this.#readout(lane);
    this.#invalidate();
    if (commit) this.#on.commit(structuredClone(rack));
    else this.#on.input(structuredClone(rack));
  }

  #pixels(lane: Lane): [number, number][] {
    const w = lane.canvas.clientWidth;
    const hgt = lane.canvas.clientHeight;
    return lane.points.map(([t, v]) => [secondsToX(t, this.#duration, w), valueToY(v, lane.range, hgt, PAD)]);
  }

  #at(lane: Lane, e: MouseEvent): { x: number; y: number; t: number; v: number } {
    const rect = lane.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    return { x, y, t: xToSeconds(x, this.#duration, rect.width), v: yToValue(y, lane.range, rect.height, PAD) };
  }

  #wire(lane: Lane): void {
    const c = lane.canvas;
    let drag: { index: number; id: number; moved: boolean; added: boolean } | null = null;
    // a double click on empty ground first adds a point; if snapping moved it out of reach,
    // the double click still removes that one
    let lastAdded = -1;
    c.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      c.focus();
      lastAdded = -1;
      const p = this.#at(lane, e);
      let index = nearestPoint(this.#pixels(lane), p.x, p.y, HIT_PX);
      let added = false;
      if (index < 0) {
        const next = insertPoint(lane.points, p.t, p.v, lane.range);
        if (!next) return;
        index = next.index;
        added = true;
        this.#emit(lane, next.points, false);
      }
      if (added) lastAdded = index;
      lane.selected = index;
      lane.dragging = true;
      c.setPointerCapture(e.pointerId);
      drag = { index, id: e.pointerId, moved: false, added };
      this.#readout(lane);
      this.#invalidate();
    });
    c.addEventListener("pointermove", (e) => {
      if (!drag) {
        const p = this.#at(lane, e);
        c.dataset.cursor = nearestPoint(this.#pixels(lane), p.x, p.y, HIT_PX) >= 0 ? "grab" : "add";
        return;
      }
      if (e.pointerId !== drag.id) return;
      const p = this.#at(lane, e);
      drag.moved = true;
      this.#emit(lane, movePoint(lane.points, drag.index, p.t, p.v, lane.range), false);
    });
    const end = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const d = drag;
      drag = null;
      lane.dragging = false;
      if (d.moved || d.added) this.#emit(lane, lane.points, true);
      else this.#invalidate();
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("dblclick", (e) => {
      const p = this.#at(lane, e);
      const hit = nearestPoint(this.#pixels(lane), p.x, p.y, HIT_PX);
      const index = hit >= 0 ? hit : lastAdded;
      lastAdded = -1;
      if (index < 0 || index >= lane.points.length) return;
      lane.selected = Math.min(index, lane.points.length - 2);
      this.#emit(lane, removePoint(lane.points, index), true);
    });
    c.addEventListener("keydown", (e) => this.#key(lane, e));
  }

  #key(lane: Lane, e: KeyboardEvent): void {
    const at = lane.selected;
    const point = lane.points[at];
    let handled = true;
    if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && e.altKey) {
      if (point) {
        const dt = (e.key === "ArrowRight" ? 1 : -1) * NUDGE_S * (e.shiftKey ? 10 : 1);
        this.#emit(lane, movePoint(lane.points, at, point[0] + dt, point[1], lane.range), true);
      }
    } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const step = e.key === "ArrowRight" ? 1 : -1;
      lane.selected = at < 0 ? (step > 0 ? 0 : lane.points.length - 1) : Math.min(lane.points.length - 1, Math.max(0, at + step));
      this.#readout(lane);
      this.#invalidate();
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      if (point) {
        const v = stepBy(point[1], lane.range, e.key === "ArrowUp" ? 1 : -1, e.shiftKey);
        this.#emit(lane, movePoint(lane.points, at, point[0], v, lane.range), true);
      }
    } else if (e.key === "Enter" || e.key === "Insert") {
      const v = lane.points.length ? valueAt(lane.points, this.#position) : lane.param.default;
      const next = insertPoint(lane.points, this.#position, v, lane.range);
      if (next) {
        lane.selected = next.index;
        this.#emit(lane, next.points, true);
      }
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if (point) {
        lane.selected = Math.min(at, lane.points.length - 2);
        this.#emit(lane, removePoint(lane.points, at), true);
      }
    } else {
      handled = false;
    }
    if (handled) e.preventDefault();
  }

  #readout(lane: Lane): void {
    const p = lane.points[lane.selected];
    lane.readout.textContent = p ? `${clock(p[0])} · ${formatValue(p[1], lane.param.unit, lane.range.step)}` : "";
  }

  #invalidate(): void {
    if (!this.#frame) this.#frame = requestAnimationFrame(() => this.#draw());
  }

  #draw(): void {
    this.#frame = 0;
    const dpr = window.devicePixelRatio || 1;
    const colors = {
      line: cssVar(this.#root, "--level"),
      dot: cssVar(this.#root, "--text"),
      hand: cssVar(this.#root, "--signal"),
      loop: cssVar(this.#root, "--loop-fill"),
      head: cssVar(this.#root, "--playhead"),
      well: cssVar(this.#root, "--well"),
    };
    for (const lane of this.#lanes) {
      const c = lane.canvas;
      const cw = c.clientWidth;
      const ch = c.clientHeight;
      const w = Math.max(1, Math.round(cw * dpr));
      const hgt = Math.max(1, Math.round(ch * dpr));
      if (c.width !== w || c.height !== hgt) {
        c.width = w;
        c.height = hgt;
      }
      const g = c.getContext("2d");
      if (!g) continue;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, cw, ch);
      if (this.#loopOn && this.#loop.end > this.#loop.start) {
        const a = secondsToX(this.#loop.start, this.#duration, cw);
        g.fillStyle = colors.loop;
        g.fillRect(a, 0, secondsToX(this.#loop.end, this.#duration, cw) - a, ch);
      }
      const px = this.#pixels(lane);
      const first = px[0];
      const last = px[px.length - 1];
      if (first && last) {
        g.strokeStyle = colors.line;
        g.lineWidth = 1.5;
        g.beginPath();
        g.moveTo(0, first[1]);
        for (const [x, y] of px) g.lineTo(x, y);
        g.lineTo(cw, last[1]);
        g.stroke();
      }
      px.forEach(([x, y], i) => {
        const chosen = i === lane.selected;
        g.fillStyle = chosen && lane.dragging ? colors.hand : colors.dot;
        const size = chosen ? DOT + 2 : DOT;
        g.fillRect(x - size / 2, y - size / 2, size, size);
        if (chosen && !lane.dragging) {
          g.fillStyle = colors.well;
          g.fillRect(x - 1.5, y - 1.5, 3, 3);
        }
      });
      g.fillStyle = colors.head;
      g.fillRect(Math.round(secondsToX(this.#position, this.#duration, cw)), 0, 1, ch);
    }
  }
}
