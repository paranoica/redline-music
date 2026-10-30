import assert from "node:assert/strict";
import { test } from "node:test";

import { History } from "../src/core/history.ts";

test("undo walks back, redo walks forward, both stop at the ends", () => {
  const h = new History({ a: 1 });
  assert.equal(h.canUndo, false);
  assert.equal(h.undo(), null);
  assert.equal(h.push({ a: 2 }), true);
  assert.equal(h.push({ a: 3 }), true);
  assert.deepEqual(h.undo(), { a: 2 });
  assert.deepEqual(h.undo(), { a: 1 });
  assert.equal(h.undo(), null);
  assert.deepEqual(h.current, { a: 1 });
  assert.deepEqual(h.redo(), { a: 2 });
  assert.deepEqual(h.redo(), { a: 3 });
  assert.equal(h.redo(), null);
  assert.equal(h.canRedo, false);
});

test("a push equal to the present is not a step", () => {
  const h = new History({ a: 1, rack: [] });
  assert.equal(h.push({ a: 1, rack: [] }), false);
  assert.equal(h.canUndo, false);
});

test("a new push after an undo drops the redo branch", () => {
  const h = new History(0);
  h.push(1);
  h.push(2);
  h.undo();
  h.push(5);
  assert.equal(h.canRedo, false);
  assert.equal(h.redo(), null);
  assert.equal(h.undo(), 1);
});

test("the past keeps at most the limit; the oldest goes first", () => {
  const h = new History(0, 3);
  for (let i = 1; i <= 5; i++) h.push(i);
  assert.equal(h.undo(), 4);
  assert.equal(h.undo(), 3);
  assert.equal(h.undo(), 2);
  assert.equal(h.undo(), null);
  assert.equal(h.current, 2);
});

test("snapshots are copies: changing a pushed object does not change the history", () => {
  const first = { a: 1 };
  const h = new History(first);
  const next = { a: 2 };
  h.push(next);
  next.a = 99;
  first.a = 77;
  assert.deepEqual(h.undo(), { a: 1 });
  assert.deepEqual(h.redo(), { a: 2 });
});
