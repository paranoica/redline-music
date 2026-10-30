import assert from "node:assert/strict";
import { test } from "node:test";

import { dragTo, fromNorm, isLog, snap, stepBy, toNorm } from "../src/core/control-math.ts";
import { keyAction } from "../src/core/keys.ts";
import { dragLoop, hitLoop } from "../src/core/loop.ts";

function close(got, want, tol = 1e-9) {
  assert.ok(Math.abs(got - want) <= tol, `${got} against ${want}`);
}

const DB = { low: -12, high: 12, step: 0.5 };
const HZ = { low: 20, high: 20000, log: true };

test("frequencies over a wide range are logarithmic, the rest linear", () => {
  assert.equal(isLog(20, 20000, "Hz"), true);
  assert.equal(isLog(1000, 12000, "Гц"), true);
  assert.equal(isLog(60, 250, "Гц"), false);
  assert.equal(isLog(-12, 12, "дБ"), false);
  close(toNorm(Math.sqrt(20 * 20000), HZ), 0.5);
  close(fromNorm(0.5, HZ), Math.sqrt(20 * 20000));
  close(fromNorm(0.5, DB), 0);
  close(toNorm(12, DB), 1);
});

test("a drag across the full width covers the range; shift moves it ten times finer", () => {
  close(dragTo(0, 100, 200, DB, false), 12);
  close(dragTo(0, 50, 200, DB, false), 6);
  close(dragTo(0, 5, 200, DB, false), 0.5);
  close(dragTo(0, 100, 200, DB, true), 1.2);
  close(dragTo(0, 1000, 200, DB, false), 12, 0);
  close(dragTo(0, -1000, 200, DB, false), -12, 0);
});

test("values snap to the step counted from the low end", () => {
  close(snap(0.26, 0.5, -12), 0.5);
  close(snap(3, 5, 1), 1);
  close(snap(3.3, 5, 1), 1);
  close(snap(3.6, 5, 1), 6);
  close(snap(7.77, undefined, 0), 7.77);
});

test("arrow keys move one step, page keys ten; a range without a step moves 1 %", () => {
  close(stepBy(0, DB, 1, false), 0.5);
  close(stepBy(0, DB, -1, true), -5);
  close(stepBy(11.8, DB, 1, true), 12);
  close(stepBy(0.5, { low: 0, high: 1 }, 1, false), 0.51);
});

test("the keyboard map: space, L, B, R, undo, redo; nothing while typing", () => {
  const k = (code, mods = {}) => ({ code, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
  assert.equal(keyAction(k("Space"), false), "play");
  assert.equal(keyAction(k("KeyL"), false), "loop");
  assert.equal(keyAction(k("KeyB"), false), "ab");
  assert.equal(keyAction(k("KeyR"), false), "reference");
  assert.equal(keyAction(k("KeyZ", { ctrlKey: true }), false), "undo");
  assert.equal(keyAction(k("KeyZ", { metaKey: true }), false), "undo");
  assert.equal(keyAction(k("KeyZ", { ctrlKey: true, shiftKey: true }), false), "redo");
  assert.equal(keyAction(k("KeyY", { ctrlKey: true }), false), "redo");
  assert.equal(keyAction(k("KeyR", { ctrlKey: true }), false), null, "Ctrl+R stays the browser's reload");
  assert.equal(keyAction(k("KeyZ"), false), null);
  assert.equal(keyAction(k("Space"), true), null);
  assert.equal(keyAction(k("KeyZ", { ctrlKey: true }), true), null);
});

test("a loop edge drag stops short of the other edge; a move keeps the length inside the track", () => {
  const loop = { start: 10, end: 20 };
  assert.deepEqual(dragLoop(loop, "start", 3, 60), { start: 13, end: 20 });
  assert.deepEqual(dragLoop(loop, "start", 30, 60), { start: 19.5, end: 20 });
  assert.deepEqual(dragLoop(loop, "start", -30, 60), { start: 0, end: 20 });
  assert.deepEqual(dragLoop(loop, "end", 100, 60), { start: 10, end: 60 });
  assert.deepEqual(dragLoop(loop, "end", -30, 60), { start: 10, end: 10.5 });
  assert.deepEqual(dragLoop(loop, "move", 45, 60), { start: 50, end: 60 });
  assert.deepEqual(dragLoop(loop, "move", -15, 60), { start: 0, end: 10 });
});

test("a press near an edge grabs the edge, inside the loop moves it, outside seeks", () => {
  const loop = { start: 10, end: 20 };
  assert.equal(hitLoop(loop, 10.2, 0.5), "start");
  assert.equal(hitLoop(loop, 19.6, 0.5), "end");
  assert.equal(hitLoop(loop, 15, 0.5), "move");
  assert.equal(hitLoop(loop, 25, 0.5), null);
  assert.equal(hitLoop(loop, 9.4, 0.5), null);
});
