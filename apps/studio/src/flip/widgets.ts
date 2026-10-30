// Small controls and the source waveform of the flip page.

import { h } from "../ui/dom.ts";
import type { Peaks } from "./api.ts";

export function seg<T extends string>(label: string, options: readonly [T, string][], current: T, pick: (value: T) => void): HTMLElement {
  const row = h("div", { class: "flip-seg", role: "group", "aria-label": label });
  for (const [value, text] of options) {
    const b = h("button", { class: "btn btn--small", type: "button", "aria-pressed": String(value === current) }, text);
    b.addEventListener("click", () => pick(value));
    row.append(b);
  }
  return row;
}

export interface SliderOptions {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  digits?: number;
  title?: string;
  input: (value: number) => void;
}

export function slider(o: SliderOptions): HTMLElement {
  const digits = o.digits ?? (o.step >= 1 ? 0 : o.step >= 0.1 ? 1 : 2);
  const show = (v: number): string => `${v.toFixed(digits)}${o.unit ? ` ${o.unit}` : ""}`;
  const out = h("output", { class: "flip-slider__out num" }, show(o.value));
  const range = h("input", { class: "flip-slider__range", type: "range", min: o.min, max: o.max, step: o.step, value: o.value, "aria-label": o.label });
  range.addEventListener("input", () => {
    const v = Number(range.value);
    out.textContent = show(v);
    o.input(v);
  });
  return h("label", { class: "flip-slider", title: o.title }, h("span", { class: "flip-slider__name" }, o.label), range, out);
}

export function numberField(label: string, value: number, min: number, max: number, step: number, change: (value: number) => void): HTMLElement {
  const input = h("input", { class: "field flip-num", type: "number", min, max, step, value, "aria-label": label });
  input.addEventListener("change", () => {
    const v = Number(input.value);
    if (input.value !== "" && Number.isFinite(v)) change(v);
  });
  return h("label", { class: "flip-field" }, h("span", {}, label), input);
}

export interface WaveModel {
  peaks: Peaks | null;
  duration: number;
  // seconds of each bar line
  bars: readonly number[];
  downbeat: number;
  // a time range to tint (the selected block)
  tint: [number, number] | null;
}

function color(el: Element, name: string, fallback: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}

export function drawWave(canvas: HTMLCanvasElement, m: WaveModel): void {
  const ratio = globalThis.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * ratio));
  const hgt = Math.max(1, Math.round(canvas.clientHeight * ratio));
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== hgt) canvas.height = hgt;
  const g = canvas.getContext("2d");
  if (!g || m.duration <= 0) return;
  g.clearRect(0, 0, w, hgt);
  const x = (t: number): number => (t / m.duration) * w;
  if (m.tint) {
    g.fillStyle = color(canvas, "--loop-fill", "rgba(233,235,239,0.07)");
    g.fillRect(x(m.tint[0]), 0, Math.max(1, x(m.tint[1]) - x(m.tint[0])), hgt);
  }
  const mid = hgt / 2;
  const list = m.peaks?.peaks ?? [];
  g.fillStyle = color(canvas, "--wave", "#5f6674");
  list.forEach((p, i) => {
    const x0 = Math.floor((i / list.length) * w);
    const x1 = Math.max(x0 + 1, Math.floor(((i + 1) / list.length) * w));
    const top = mid - Math.min(1, Math.max(-1, p[1])) * mid;
    const bottom = mid - Math.min(1, Math.max(-1, p[0])) * mid;
    g.fillRect(x0, top, x1 - x0, Math.max(1, bottom - top));
  });
  // a bar line every bar when they are far enough apart, else every fourth or eighth
  const gap = m.bars.length > 1 ? x((m.bars[1] as number) - (m.bars[0] as number)) : w;
  const every = gap >= 6 * ratio ? 1 : gap >= 3 * ratio ? 4 : 8;
  g.fillStyle = color(canvas, "--line-strong", "#3b414c");
  m.bars.forEach((t, i) => {
    if (i % every === 0) g.fillRect(Math.round(x(t)), 0, 1, hgt);
  });
  g.fillStyle = color(canvas, "--signal", "#ff4a3d");
  g.fillRect(Math.round(x(m.downbeat)), 0, Math.max(1, Math.round(ratio)), hgt);
}
