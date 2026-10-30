// The arithmetic behind faders: normalised positions, drags, steps and snapping.

export interface Range {
  low: number;
  high: number;
  step?: number | undefined;
  log?: boolean | undefined;
}

export const FINE = 0.1;

// a frequency over more than a decade or so reads better on a log scale
export function isLog(low: number, high: number, unit: string): boolean {
  return (unit === "Hz" || unit === "Гц") && low > 0 && high / low >= 10;
}

export function clamp(v: number, r: Range): number {
  return Math.min(r.high, Math.max(r.low, v));
}

export function toNorm(v: number, r: Range): number {
  const c = clamp(v, r);
  if (r.log) return Math.log(c / r.low) / Math.log(r.high / r.low);
  return r.high === r.low ? 0 : (c - r.low) / (r.high - r.low);
}

export function fromNorm(n: number, r: Range): number {
  const t = Math.min(1, Math.max(0, n));
  if (r.log) return r.low * (r.high / r.low) ** t;
  return r.low + t * (r.high - r.low);
}

export function snap(v: number, step: number | undefined, low: number): number {
  if (step === undefined || step <= 0) return v;
  const n = Math.round((v - low) / step);
  // rounding away float noise such as 0.30000000000000004
  return Number((low + n * step).toPrecision(12));
}

// the value after dragging `dx` pixels from where the drag began
export function dragTo(start: number, dx: number, fullPixels: number, r: Range, fine: boolean): number {
  const n = toNorm(start, r) + (dx / fullPixels) * (fine ? FINE : 1);
  const step = r.step === undefined ? undefined : fine ? r.step * FINE : r.step;
  return clamp(snap(fromNorm(n, r), step, r.low), r);
}

export function stepBy(v: number, r: Range, direction: 1 | -1, big: boolean): number {
  const unit = r.step ?? (r.high - r.low) / 100;
  const moved = v + direction * unit * (big ? 10 : 1);
  return clamp(r.step === undefined ? Number(moved.toPrecision(12)) : snap(moved, r.step, r.low), r);
}
