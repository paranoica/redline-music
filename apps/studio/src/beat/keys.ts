// The beat page's keyboard. Letters mean different things where the focus is: G marks a bar
// good in the song strip and sets a ghost note in the step grid.

import type { KeyLike } from "../core/keys.ts";

export type Zone = "strip" | "grid" | "roll" | "other";

export type BeatKey =
  | { type: "play" }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "bar"; by: -1 | 1 }
  | { type: "move"; dx: number; dy: number; wide: boolean }
  | { type: "hit"; hit: "x" | "X" | "g" | "." }
  | { type: "roll"; n: 2 | 3 | 4 | 6 }
  | { type: "cycle" }
  | { type: "mark"; verdict: "good" | "bad" }
  | { type: "playFrom" }
  | { type: "add" }
  | { type: "remove" };

const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-1, 0],
  ArrowRight: [1, 0],
  ArrowUp: [0, -1],
  ArrowDown: [0, 1],
};

export function beatKey(e: KeyLike & { key: string }, zone: Zone, typing: boolean): BeatKey | null {
  if (typing || e.altKey) return null;
  if (e.ctrlKey || e.metaKey) {
    if (e.code === "KeyZ") return { type: e.shiftKey ? "redo" : "undo" };
    if (e.code === "KeyY") return { type: "redo" };
    return null;
  }
  if (e.code === "Space") return { type: "play" };
  if (e.code === "BracketLeft") return { type: "bar", by: -1 };
  if (e.code === "BracketRight") return { type: "bar", by: 1 };
  const arrow = ARROWS[e.key];
  if (zone === "strip") {
    if (arrow && arrow[0]) return { type: "move", dx: arrow[0], dy: 0, wide: e.shiftKey };
    if (e.code === "KeyG") return { type: "mark", verdict: "good" };
    if (e.code === "KeyB") return { type: "mark", verdict: "bad" };
    if (e.key === "Enter") return { type: "playFrom" };
    return null;
  }
  if (zone === "grid") {
    if (arrow) return { type: "move", dx: arrow[0], dy: arrow[1], wide: e.shiftKey };
    if (e.code === "KeyX") return { type: "hit", hit: e.shiftKey ? "X" : "x" };
    if (e.code === "KeyA") return { type: "hit", hit: "X" };
    if (e.code === "KeyG") return { type: "hit", hit: "g" };
    if (e.key === "Delete" || e.key === "Backspace") return { type: "hit", hit: "." };
    if (e.key === "Enter") return { type: "cycle" };
    for (const n of [2, 3, 4, 6] as const) if (e.code === `Digit${n}` || e.code === `Numpad${n}`) return { type: "roll", n };
    return null;
  }
  if (zone === "roll") {
    if (arrow) return { type: "move", dx: arrow[0], dy: arrow[1], wide: e.shiftKey };
    if (e.key === "Delete" || e.key === "Backspace") return { type: "remove" };
    if (e.key === "Enter" || e.key === "Insert") return { type: "add" };
    return null;
  }
  return null;
}
