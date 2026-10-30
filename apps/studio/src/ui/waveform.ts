// The track's waveform: the playhead, the loop to drag, and the redline — where the master
// took gain off, painted in as the track plays.

import { clock } from "../core/format.ts";
import { dragLoop, hitLoop, type Loop, type LoopGrip } from "../core/loop.ts";
import { peaks } from "../core/peaks.ts";

const GRIP_PX = 8;
const BINS = 2048;
// the gain reduction that fills the redline lane to the top
const GR_FULL_DB = 12;

export interface WaveformEvents {
  seek: (seconds: number) => void;
  loop: (loop: Loop, done: boolean) => void;
}

function cssVar(el: Element, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

export class Waveform {
  readonly #canvas: HTMLCanvasElement;
  readonly #root: HTMLElement;
  readonly #on: WaveformEvents;
  #peaks: { min: Float32Array; max: Float32Array } | null = null;
  readonly #redline = new Float32Array(BINS);
  #duration = 1;
  #position = 0;
  #loop: Loop = { start: 0, end: 0 };
  #loopOn = true;
  #dirty = true;
  #frame = 0;

  constructor(root: HTMLElement, canvas: HTMLCanvasElement, on: WaveformEvents) {
    this.#root = root;
    this.#canvas = canvas;
    this.#on = on;
    new ResizeObserver(() => this.#invalidate()).observe(canvas);
    this.#wire();
  }

  setAudio(x: Float32Array, rate: number): void {
    this.#peaks = peaks(x, BINS);
    this.#duration = Math.max(1e-3, x.length / 2 / rate);
    this.#redline.fill(0);
    this.#invalidate();
  }

  setPosition(seconds: number): void {
    if (Math.abs(seconds - this.#position) < 1e-4) return;
    this.#position = seconds;
    this.#root.setAttribute("aria-valuenow", seconds.toFixed(1));
    this.#root.setAttribute("aria-valuetext", clock(seconds));
    this.#invalidate();
  }

  setLoop(loop: Loop, on: boolean): void {
    this.#loop = loop;
    this.#loopOn = on;
    this.#root.dataset.loop = String(on);
    this.#invalidate();
  }

  // the master's gain reduction heard at this second
  markRedline(seconds: number, db: number): void {
    const bin = Math.min(BINS - 1, Math.max(0, Math.floor((seconds / this.#duration) * BINS)));
    // what is played again replaces what was painted, so a change of settings shows
    if (Math.abs((this.#redline[bin] as number) - db) > 0.05) {
      this.#redline[bin] = db;
      this.#invalidate();
    }
  }

  clearRedline(): void {
    this.#redline.fill(0);
    this.#invalidate();
  }

  #invalidate(): void {
    this.#dirty = true;
    if (!this.#frame) this.#frame = requestAnimationFrame(() => this.#draw());
  }

  #toSeconds(clientX: number): number {
    const rect = this.#canvas.getBoundingClientRect();
    return Math.min(this.#duration, Math.max(0, ((clientX - rect.left) / rect.width) * this.#duration));
  }

  #wire(): void {
    let drag: { grip: LoopGrip | "new"; from: number; base: Loop; moved: boolean; id: number } | null = null;
    const c = this.#canvas;
    c.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const rect = c.getBoundingClientRect();
      const t = this.#toSeconds(e.clientX);
      const tolerance = (GRIP_PX / rect.width) * this.#duration;
      const grip = this.#loopOn ? hitLoop(this.#loop, t, tolerance) : null;
      c.setPointerCapture(e.pointerId);
      drag = { grip: grip ?? (e.shiftKey ? "new" : "move"), from: t, base: this.#loop, moved: false, id: e.pointerId };
      if (!grip && !e.shiftKey) drag = null;
      if (!grip && !e.shiftKey) this.#on.seek(t);
      this.#root.dataset.dragging = String(drag !== null);
    });
    c.addEventListener("pointermove", (e) => {
      const t = this.#toSeconds(e.clientX);
      if (!drag) {
        const rect = c.getBoundingClientRect();
        const grip = this.#loopOn ? hitLoop(this.#loop, t, (GRIP_PX / rect.width) * this.#duration) : null;
        c.dataset.cursor = grip === "start" || grip === "end" ? "edge" : grip === "move" ? "move" : "seek";
        return;
      }
      if (e.pointerId !== drag.id) return;
      drag.moved = true;
      let loop: Loop;
      if (drag.grip === "new") {
        const a = Math.min(drag.from, t);
        const b = Math.max(drag.from, t);
        loop = b - a >= 0.5 ? { start: a, end: b } : drag.base;
      } else {
        loop = dragLoop(drag.base, drag.grip, t - drag.from, this.#duration);
      }
      this.setLoop(loop, this.#loopOn || drag.grip === "new");
      this.#on.loop(loop, false);
    });
    const end = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const d = drag;
      drag = null;
      this.#root.dataset.dragging = "false";
      if (d.moved) this.#on.loop(this.#loop, true);
      else if (d.grip === "move") this.#on.seek(d.from);
    };
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    this.#root.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 5 : 1;
      let handled = true;
      if (e.key === "ArrowRight") this.#on.seek(Math.min(this.#duration, this.#position + step));
      else if (e.key === "ArrowLeft") this.#on.seek(Math.max(0, this.#position - step));
      else if (e.key === "Home") this.#on.seek(this.#loopOn ? this.#loop.start : 0);
      else if (e.key === "End") this.#on.seek(Math.max(0, this.#duration - 1));
      else if (e.key === "[" && this.#position < this.#loop.end - 0.5) this.#keyLoop({ start: this.#position, end: this.#loop.end });
      else if (e.key === "]" && this.#position > this.#loop.start + 0.5) this.#keyLoop({ start: this.#loop.start, end: this.#position });
      else handled = false;
      if (handled) e.preventDefault();
    });
  }

  #keyLoop(loop: Loop): void {
    this.setLoop(loop, true);
    this.#on.loop(loop, true);
  }

  #draw(): void {
    this.#frame = 0;
    if (!this.#dirty) return;
    this.#dirty = false;
    const c = this.#canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(c.clientWidth * dpr));
    const hgt = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== hgt) {
      c.width = w;
      c.height = hgt;
    }
    const g = c.getContext("2d");
    if (!g) return;
    const colors = {
      wave: cssVar(this.#root, "--wave"),
      played: cssVar(this.#root, "--wave-played"),
      loop: cssVar(this.#root, "--loop-fill"),
      edge: cssVar(this.#root, "--loop-edge"),
      head: cssVar(this.#root, "--playhead"),
      red: cssVar(this.#root, "--signal"),
      rule: cssVar(this.#root, "--line"),
    };
    g.clearRect(0, 0, w, hgt);
    const lane = Math.round(hgt * 0.16);
    const mid = lane + (hgt - lane) / 2;
    const amp = (hgt - lane) / 2 - 2 * dpr;
    const x = (s: number) => (s / this.#duration) * w;
    if (this.#loopOn && this.#loop.end > this.#loop.start) {
      g.fillStyle = colors.loop;
      g.fillRect(x(this.#loop.start), 0, x(this.#loop.end) - x(this.#loop.start), hgt);
    }
    g.fillStyle = colors.rule;
    g.fillRect(0, lane, w, dpr);
    const p = this.#peaks;
    if (p) {
      const head = x(this.#position);
      for (let px = 0; px < w; px++) {
        const a = Math.floor((px / w) * BINS);
        const b = Math.max(a + 1, Math.floor(((px + 1) / w) * BINS));
        let lo = 0;
        let hi = 0;
        let red = 0;
        for (let i = a; i < b && i < BINS; i++) {
          lo = Math.min(lo, p.min[i] as number);
          hi = Math.max(hi, p.max[i] as number);
          red = Math.max(red, this.#redline[i] as number);
        }
        g.fillStyle = px < head ? colors.played : colors.wave;
        const top = mid - hi * amp;
        g.fillRect(px, top, 1, Math.max(dpr, (hi - lo) * amp));
        if (red > 0.05) {
          g.fillStyle = colors.red;
          g.fillRect(px, 0, 1, Math.min(1, red / GR_FULL_DB) * (lane - dpr));
        }
      }
    }
    if (this.#loopOn && this.#loop.end > this.#loop.start) {
      g.fillStyle = colors.edge;
      for (const s of [this.#loop.start, this.#loop.end]) {
        g.fillRect(Math.round(x(s)) - dpr, lane, 2 * dpr, hgt - lane);
        g.fillRect(Math.round(x(s)) - 3 * dpr, lane, 6 * dpr, 6 * dpr);
      }
    }
    g.fillStyle = colors.head;
    g.fillRect(Math.round(x(this.#position)) - dpr / 2, 0, Math.max(1, dpr), hgt);
  }
}
