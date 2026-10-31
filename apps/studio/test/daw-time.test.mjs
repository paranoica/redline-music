// Bar, beat and clock math of the timeline.
import assert from "node:assert/strict";
import { test } from "node:test";

import { barBeat, barSeconds, clockText, grid, snapSeconds, toBars, toSeconds, wholeBars } from "../src/daw/time.ts";

const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg ?? ""} ${a} != ${b}`);

test("seconds and bars convert both ways at 120 BPM from zero", () => {
  const g = grid(120);
  assert.equal(barSeconds(g), 2);
  assert.equal(toBars(g, 0), 0);
  assert.equal(toBars(g, 2), 1);
  assert.equal(toBars(g, 3), 1.5);
  assert.equal(toSeconds(g, 0), 0);
  assert.equal(toSeconds(g, 1.5), 3);
  near(toSeconds(g, toBars(g, 7.3)), 7.3);
});

test("a first bar line just before the file shifts every bar", () => {
  const g = grid(120, -0.02);
  near(toBars(g, 0), 0.01);
  near(toSeconds(g, 0), -0.02);
  near(toBars(g, 1.98), 1);
  assert.equal(barBeat(toBars(g, 0)), "1.1");
  assert.equal(barBeat(toBars(g, 1.98)), "2.1");
  assert.equal(barBeat(toBars(g, 1.97)), "1.4");
});

test("bar.beat labels are 1-based and land on the bar line exactly", () => {
  assert.equal(barBeat(0), "1.1");
  assert.equal(barBeat(0.25), "1.2");
  assert.equal(barBeat(0.75), "1.4");
  assert.equal(barBeat(0.99), "1.4");
  assert.equal(barBeat(1), "2.1");
  assert.equal(barBeat(0.9999999999), "2.1");
  assert.equal(barBeat(29.75), "30.4");
  assert.equal(barBeat(toBars(grid(140), (60 / 140) * 4 * 104)), "105.1");
});

test("a time before the first bar line counts down to bar 1", () => {
  const g = grid(120, 0.5);
  assert.equal(toBars(g, 0), -0.25);
  assert.equal(barBeat(-0.25), "0.4");
  assert.equal(barBeat(toBars(g, 0.5)), "1.1");
});

test("the clock is m:ss.s, cut to the tenth, never negative", () => {
  assert.equal(clockText(0), "0:00.0");
  assert.equal(clockText(0.3), "0:00.3");
  assert.equal(clockText(5.06), "0:05.0");
  assert.equal(clockText(59.96), "0:59.9");
  assert.equal(clockText(60), "1:00.0");
  assert.equal(clockText(65.25), "1:05.2");
  assert.equal(clockText(600), "10:00.0");
  assert.equal(clockText(-1), "0:00.0");
});

test("snapping goes to the nearest beat or bar line and not before the file", () => {
  const g = grid(120);
  assert.equal(snapSeconds(g, 0.9, "bar"), 0);
  assert.equal(snapSeconds(g, 1.1, "bar"), 2);
  assert.equal(snapSeconds(g, 0.74, "beat"), 0.5);
  assert.equal(snapSeconds(g, 0.76, "beat"), 1);
  assert.equal(snapSeconds(g, 4, "bar"), 4);
  const early = grid(120, -0.02);
  assert.equal(snapSeconds(early, 0.3, "bar"), 0);
  near(snapSeconds(early, 1, "beat"), 0.98);
  near(snapSeconds(early, 2.5, "bar"), 1.98);
});

test("whole bars in a file stop at the last full bar", () => {
  assert.equal(wholeBars(grid(120), 60), 30);
  assert.equal(wholeBars(grid(120), 60.5), 30);
  assert.equal(wholeBars(grid(120), 59.99), 29);
  assert.equal(wholeBars(grid(120), 1.5), 0);
  assert.equal(wholeBars(grid(120), 0), 0);
  assert.equal(wholeBars(grid(140), (60 / 140) * 4 * 105), 105);
  assert.equal(wholeBars(grid(120, -0.02), 59.98), 30);
});

test("a grid needs a positive finite tempo and a finite offset", () => {
  assert.throws(() => grid(0), RangeError);
  assert.throws(() => grid(-120), RangeError);
  assert.throws(() => grid(Number.NaN), RangeError);
  assert.throws(() => grid(Number.POSITIVE_INFINITY), RangeError);
  assert.throws(() => grid(120, Number.NaN), RangeError);
});
