import assert from "node:assert/strict";
import { test } from "node:test";

import { valueAt } from "../src/core/automation.ts";

const points = [
  [1, -3],
  [3, 1],
  [5, 1],
];

test("before the first point the first value holds, after the last the last", () => {
  assert.equal(valueAt(points, 0), -3);
  assert.equal(valueAt(points, 1), -3);
  assert.equal(valueAt(points, 9), 1);
});

test("between two points the value is linear", () => {
  assert.equal(valueAt(points, 2), -1);
  assert.equal(valueAt(points, 2.5), 0);
  assert.equal(valueAt(points, 4), 1);
});

test("two points at the same time step to the later value", () => {
  const step = [
    [0, 0],
    [2, 0],
    [2, 10],
    [4, 10],
  ];
  assert.equal(valueAt(step, 1.999), 0);
  assert.equal(valueAt(step, 2), 10);
  assert.equal(valueAt(step, 3), 10);
});

test("one point is a constant; no points is refused", () => {
  assert.equal(valueAt([[2, 7]], 0), 7);
  assert.equal(valueAt([[2, 7]], 5), 7);
  assert.throws(() => valueAt([], 1), /point/);
});
