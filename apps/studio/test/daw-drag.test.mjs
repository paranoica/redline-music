// Dragging a block on the bar ruler: the gap it would drop in, the no-op, the copy, the click.
import assert from "node:assert/strict";
import { test } from "node:test";

import { DRAG_THRESHOLD_PX, isDrag, nearestGap, planDrag } from "../src/daw/drag.ts";
import { moveBlockTo, copyBlockTo } from "../src/flip/model.ts";

// blocks of 4, 2 and 8 bars: edges at 0, 4, 6, 14
const L = [4, 2, 8];

test("the gap is the nearest block edge; the first and last edges are reachable", () => {
  assert.equal(nearestGap(L, -3), 0);
  assert.equal(nearestGap(L, 0), 0);
  assert.equal(nearestGap(L, 1.9), 0);
  assert.equal(nearestGap(L, 2.1), 1);
  assert.equal(nearestGap(L, 4.9), 1);
  assert.equal(nearestGap(L, 5.1), 2);
  assert.equal(nearestGap(L, 9.9), 2);
  assert.equal(nearestGap(L, 10.1), 3);
  assert.equal(nearestGap(L, 99), 3);
  assert.equal(nearestGap(L, 2), 0);
  assert.equal(nearestGap([], 5), 0);
});

test("a move to the gap before or after the block itself is a no-op", () => {
  for (const [bar, noop] of [[0, true], [4, true], [6, false], [14, false]]) {
    assert.equal(planDrag(L, 0, bar, false).noop, noop, `block 0 at ${bar}`);
  }
  assert.equal(planDrag(L, 1, 3.9, false).noop, true);
  assert.equal(planDrag(L, 1, 6.1, false).noop, true);
  assert.equal(planDrag(L, 1, 0, false).noop, false);
  assert.equal(planDrag(L, 2, 6, false).noop, true);
  assert.equal(planDrag(L, 2, 14, false).noop, true);
  assert.equal(planDrag(L, 2, 0, false).noop, false);
});

test("the plan hands the model the index it expects, and the ghost sits at the gap", () => {
  const blocks = L.map((n, k) => ({ label: String(k), from_bar: 0, to_bar: n }));
  const l = { blocks, patterns: ["a", "b", "c"] };
  for (let i = 0; i < 3; i += 1) {
    for (const bar of [0, 4, 6, 14]) {
      const p = planDrag(L, i, bar, false);
      const out = moveBlockTo(l, i, p.to);
      assert.equal(out.blocks.findIndex((b) => b.label === String(i)), out.at);
      const asked = L.slice(0, p.gap).reduce((a, b) => a + b, 0);
      assert.equal(p.ghostBar, asked);
      if (p.noop) assert.deepEqual(out.blocks.map((b) => b.label), ["0", "1", "2"]);
      else assert.notDeepEqual(out.blocks.map((b) => b.label), ["0", "1", "2"]);
    }
  }
  const last = planDrag(L, 0, 14, false);
  assert.equal(last.to, 2);
  assert.equal(moveBlockTo(l, 0, last.to).blocks[2].label, "0");
  const copy = planDrag(L, 0, 14, true);
  assert.equal(copy.to, 3);
  assert.equal(copyBlockTo(l, 0, copy.to).blocks[3].label, "0");
});

test("a copy is never a no-op and is refused at 512 bars", () => {
  const p = planDrag(L, 1, 4, true);
  assert.equal(p.noop, false);
  assert.equal(p.copy, true);
  assert.equal(p.reason, null);
  const full = planDrag([256, 255, 1], 2, 0, true);
  assert.match(full.reason, /512/);
  assert.equal(planDrag([256, 255, 1], 2, 0, false).reason, null);
  assert.equal(planDrag([256, 254, 1], 2, 0, true).reason, null);
});

test("bad input gives no plan", () => {
  assert.equal(planDrag(L, 3, 1, false), null);
  assert.equal(planDrag(L, -1, 1, false), null);
  assert.equal(planDrag(L, 0, Number.NaN, false), null);
});

test("a pointer that barely moved is a click, not a drag", () => {
  assert.equal(isDrag(100, 100), false);
  assert.equal(isDrag(100, 100 + DRAG_THRESHOLD_PX - 1), false);
  assert.equal(isDrag(100, 100 + DRAG_THRESHOLD_PX), true);
  assert.equal(isDrag(100, 100 - DRAG_THRESHOLD_PX), true);
  assert.equal(isDrag(0, 3, 0, 3), true);
  assert.equal(isDrag(0, 2, 0, 2), false);
});
