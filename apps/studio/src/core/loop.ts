// The loop region on the waveform, in seconds.

export interface Loop {
  start: number;
  end: number;
}

export type LoopGrip = "start" | "end" | "move";

export const MIN_LOOP = 0.5;

export function dragLoop(loop: Loop, grip: LoopGrip, delta: number, duration: number, min = MIN_LOOP): Loop {
  if (grip === "start") {
    return { start: Math.min(loop.end - min, Math.max(0, loop.start + delta)), end: loop.end };
  }
  if (grip === "end") {
    return { start: loop.start, end: Math.max(loop.start + min, Math.min(duration, loop.end + delta)) };
  }
  const length = loop.end - loop.start;
  const start = Math.min(duration - length, Math.max(0, loop.start + delta));
  return { start, end: start + length };
}

// where a jump to `at` lands: a loop that is on takes one from outside it to its start
export function intoLoop(at: number, on: boolean, loop: Loop): number {
  return on && (at < loop.start || at >= loop.end) ? loop.start : at;
}

export function hitLoop(loop: Loop, t: number, tolerance: number): LoopGrip | null {
  if (Math.abs(t - loop.start) <= tolerance) return "start";
  if (Math.abs(t - loop.end) <= tolerance) return "end";
  if (t > loop.start && t < loop.end) return "move";
  return null;
}
