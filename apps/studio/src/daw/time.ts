// Bars, beats and the clock of the timeline. Bars are 0-based floats from the first bar line;
// the page shows them 1-based.

export const BEATS_PER_BAR = 4;

// bar math lands a hair off a bar line; this much of a beat still counts as on it
const ON_LINE = 1e-6;

export interface Grid {
  bpm: number;
  // where the first bar line sits in the file: 0 for an own beat, a flip's downbeat_s
  downbeat: number;
}

export function grid(bpm: number, downbeat = 0): Grid {
  if (!Number.isFinite(bpm) || bpm <= 0) throw new RangeError(`a tempo of ${bpm} BPM`);
  if (!Number.isFinite(downbeat)) throw new RangeError(`a first bar line at ${downbeat} s`);
  return { bpm, downbeat };
}

export const barSeconds = (g: Grid): number => (60 / g.bpm) * BEATS_PER_BAR;

export const toBars = (g: Grid, seconds: number): number => (seconds - g.downbeat) / barSeconds(g);

export const toSeconds = (g: Grid, bars: number): number => g.downbeat + bars * barSeconds(g);

// "bar.beat"; a time before the first bar line counts down as bar 0, -1...
export function barBeat(bar: number): string {
  const beats = Math.floor(bar * BEATS_PER_BAR + ON_LINE);
  const whole = Math.floor(beats / BEATS_PER_BAR);
  return `${whole + 1}.${beats - whole * BEATS_PER_BAR + 1}`;
}

// "m:ss.s", cut rather than rounded so the clock never shows a tenth that has not come
export function clockText(seconds: number): string {
  const tenths = Math.floor(Math.max(0, seconds) * 10 + ON_LINE);
  const m = Math.floor(tenths / 600);
  const rest = tenths - m * 600;
  return `${m}:${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}`;
}

export type Snap = "beat" | "bar";

// the nearest beat or bar line; one before the file snaps to its start
export function snapSeconds(g: Grid, seconds: number, to: Snap): number {
  const per = to === "bar" ? 1 : BEATS_PER_BAR;
  const bars = Math.round(toBars(g, seconds) * per) / per;
  return Math.max(0, toSeconds(g, bars));
}

// whole bars between the first bar line and the end of the file
export function wholeBars(g: Grid, duration: number): number {
  return Math.max(0, Math.floor(toBars(g, duration) + ON_LINE / BEATS_PER_BAR));
}
