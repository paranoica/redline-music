// Automation lanes on screen: time across the width as the waveform draws it, the param's
// range up the height, and edits of a lane's points that keep the order the core needs.

import { clamp, fromNorm, snap, toNorm, type Range } from "./control-math.ts";
import type { AutomationLane } from "../types.ts";

export type Point = [number, number];

// the core's limit (redline.engine.settings.MAX_LANE_POINTS)
export const MAX_LANE_POINTS = 4096;

export function secondsToX(seconds: number, duration: number, width: number): number {
  return (seconds / duration) * width;
}

export function xToSeconds(x: number, duration: number, width: number): number {
  return Math.min(duration, Math.max(0, (x / width) * duration));
}

export function valueToY(value: number, range: Range, height: number, pad = 0): number {
  return pad + (1 - toNorm(value, range)) * (height - 2 * pad);
}

function fit(value: number, range: Range): number {
  return clamp(snap(clamp(value, range), range.step, range.low), range);
}

export function yToValue(y: number, range: Range, height: number, pad = 0): number {
  const inner = height - 2 * pad;
  const n = inner > 0 ? 1 - (y - pad) / inner : 0;
  return fit(fromNorm(n, range), range);
}

// null when the lane is full
export function insertPoint(points: readonly (readonly [number, number])[], seconds: number, value: number, range: Range): { points: Point[]; index: number } | null {
  if (points.length >= MAX_LANE_POINTS) return null;
  const t = Math.max(0, seconds);
  let index = points.length;
  for (let i = 0; i < points.length; i++) {
    if ((points[i] as readonly [number, number])[0] > t) {
      index = i;
      break;
    }
  }
  const out = points.map((p) => [p[0], p[1]] as Point);
  out.splice(index, 0, [t, fit(value, range)]);
  return { points: out, index };
}

export function movePoint(points: readonly (readonly [number, number])[], index: number, seconds: number, value: number, range: Range): Point[] {
  const out = points.map((p) => [p[0], p[1]] as Point);
  if (index < 0 || index >= out.length) return out;
  const before = out[index - 1]?.[0] ?? 0;
  const after = out[index + 1]?.[0] ?? Infinity;
  out[index] = [Math.min(after, Math.max(before, seconds)), fit(value, range)];
  return out;
}

export function removePoint(points: readonly (readonly [number, number])[], index: number): Point[] {
  return points.filter((_, i) => i !== index).map((p) => [p[0], p[1]] as Point);
}

// a lane without points is no lane: the param goes back to its own value
export function withLane(automation: readonly AutomationLane[], param: string, points: readonly (readonly [number, number])[]): AutomationLane[] {
  const out: AutomationLane[] = [];
  for (const lane of automation) {
    if (lane.param !== param) out.push(structuredClone(lane));
    else if (points.length) out.push({ param, points: points.map((p) => [p[0], p[1]]) });
  }
  return out;
}

export function nearestPoint(pixels: readonly (readonly [number, number])[], x: number, y: number, radius: number): number {
  let best = -1;
  let bestD = radius * radius;
  pixels.forEach(([px, py], i) => {
    const d = (px - x) ** 2 + (py - y) ** 2;
    if (d <= bestD) {
      best = i;
      bestD = d;
    }
  });
  return best;
}
