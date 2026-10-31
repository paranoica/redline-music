// The canvases inside the lanes' blocks: a waveform slice of a file's peaks, the 808's notes and
// the drum hits of an own beat.

import { STEPS_PER_BAR } from "../beat/model.ts";
import type { BassMark, HitMark } from "./view.ts";

export interface PeakData {
  duration: number;
  peaks: [number, number][];
}

// a canvas of `width` x `height` css pixels at the screen's density
export function sizedCanvas(width: number, height: number): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null } {
  const canvas = document.createElement("canvas");
  const dpr = globalThis.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(width));
  const hgt = Math.max(1, Math.round(height));
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(hgt * dpr);
  const ctx = canvas.getContext("2d");
  ctx?.scale(dpr, dpr);
  return { canvas, ctx };
}

// the peaks of seconds [from, to) of the file, drawn over the canvas width
export function drawWave(ctx: CanvasRenderingContext2D, width: number, height: number, data: PeakData, from: number, to: number, color: string): void {
  const n = data.peaks.length;
  if (!n || !(data.duration > 0) || !(to > from)) return;
  ctx.fillStyle = color;
  ctx.globalAlpha = 0.75;
  const mid = height / 2;
  const span = to - from;
  for (let x = 0; x < width; x += 2) {
    const t0 = from + (x / width) * span;
    const t1 = from + ((x + 2) / width) * span;
    if (t1 <= 0 || t0 >= data.duration) continue;
    const i0 = Math.max(0, Math.floor((t0 / data.duration) * n));
    const i1 = Math.min(n, Math.max(i0 + 1, Math.ceil((t1 / data.duration) * n)));
    let lo = 0;
    let hi = 0;
    for (let i = i0; i < i1; i++) {
      const p = data.peaks[i];
      if (!p) continue;
      lo = Math.min(lo, p[0]);
      hi = Math.max(hi, p[1]);
    }
    const top = mid - Math.min(1, hi) * mid * 0.92;
    const bottom = mid - Math.max(-1, lo) * mid * 0.92;
    ctx.fillRect(x, top, 1.4, Math.max(1, bottom - top));
  }
  ctx.globalAlpha = 1;
}

// the notes of bars [fromBar, fromBar + bars), the pitch range spread over the height
export function drawNotes(ctx: CanvasRenderingContext2D, marks: readonly BassMark[], fromBar: number, bars: number, barPx: number, height: number, color: string): void {
  const inside = marks.filter((m) => m.bar >= fromBar && m.bar < fromBar + bars);
  if (!inside.length) return;
  const low = Math.min(...inside.map((m) => m.midi));
  const high = Math.max(...inside.map((m) => m.midi));
  const room = height - 16;
  const stepPx = barPx / STEPS_PER_BAR;
  ctx.fillStyle = color;
  for (const m of inside) {
    const y = high === low ? room / 2 + 6 : 6 + ((high - m.midi) / (high - low)) * room;
    ctx.fillRect((m.bar - fromBar) * barPx + m.step * stepPx, y, Math.max(2, m.len * stepPx - 1), 4);
  }
}

// hits on three rows: kick, snare, hats
export function drawHits(ctx: CanvasRenderingContext2D, marks: readonly HitMark[], fromBar: number, bars: number, barPx: number, height: number, color: string): void {
  const stepPx = barPx / STEPS_PER_BAR;
  const row = (height - 8) / 3;
  ctx.fillStyle = color;
  for (const m of marks) {
    if (m.bar < fromBar || m.bar >= fromBar + bars) continue;
    ctx.globalAlpha = m.row === 2 ? 0.6 : 0.95;
    const tall = m.row === 2 ? row * 0.45 : row * 0.75;
    ctx.fillRect((m.bar - fromBar) * barPx + m.step * stepPx, 4 + m.row * row + (row - tall) / 2, 2, tall);
  }
  ctx.globalAlpha = 1;
}
