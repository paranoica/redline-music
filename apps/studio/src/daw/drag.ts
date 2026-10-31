// Dragging a block along the bar ruler: where it would drop, and what the drop would do.

import { MAX_BARS } from "../flip/model.ts";

// a pointer that moved less than this since it went down is a click
export const DRAG_THRESHOLD_PX = 4;

export function isDrag(downX: number, x: number, downY = 0, y = 0, threshold = DRAG_THRESHOLD_PX): boolean {
  return Math.hypot(x - downX, y - downY) >= threshold;
}

export interface DragPlan {
  // the gap between blocks the pointer is nearest to, 0..n, in the list as it is
  gap: number;
  // the bar the ghost is drawn at: the start of that gap
  ghostBar: number;
  // the index to hand moveBlockTo (the list without the block) or copyBlockTo (the gap)
  to: number;
  // a move that leaves the block where it is
  noop: boolean;
  copy: boolean;
  // set when the drop is refused
  reason: string | null;
}

// the gap nearest to a bar; the earlier one on a tie
export function nearestGap(lengths: readonly number[], pointerBar: number): number {
  let at = 0;
  let best = 0;
  let bestDist = Math.abs(pointerBar);
  for (let k = 0; k < lengths.length; k += 1) {
    at += lengths[k] as number;
    const dist = Math.abs(pointerBar - at);
    if (dist < bestDist) {
      best = k + 1;
      bestDist = dist;
    }
  }
  return best;
}

export function planDrag(lengths: readonly number[], i: number, pointerBar: number, copy: boolean): DragPlan | null {
  const len = lengths[i];
  if (len === undefined || !Number.isFinite(pointerBar)) return null;
  const gap = nearestGap(lengths, pointerBar);
  const ghostBar = lengths.slice(0, gap).reduce((a, b) => a + b, 0);
  const total = lengths.reduce((a, b) => a + b, 0);
  const reason = copy && total + len > MAX_BARS ? `Копия не влезет: не больше ${MAX_BARS} тактов.` : null;
  const to = copy ? gap : gap > i ? gap - 1 : gap;
  return { gap, ghostBar, to, noop: !copy && to === i, copy, reason };
}
