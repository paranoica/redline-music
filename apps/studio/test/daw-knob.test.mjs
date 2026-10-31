// The knob control's math: a drag up turns it up, 200 px is the whole range, Shift is four times
// finer, the wheel moves it a twentieth, always inside 0..1.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dragKnob, KNOB_DRAG_PX, wheelKnob } from "../src/daw/knob.ts";

test("the whole range is 200 px of drag", () => {
  assert.equal(KNOB_DRAG_PX, 200);
});

test("dragging up turns the knob up, down turns it down", () => {
  assert.equal(dragKnob(0.5, -100, false), 1);
  assert.equal(dragKnob(0.5, 100, false), 0);
  assert.equal(dragKnob(0.2, -20, false), 0.3);
  assert.equal(dragKnob(0.7, 40, false), 0.5);
  assert.equal(dragKnob(0.4, 0, false), 0.4);
});

test("a drag past either end stops there", () => {
  assert.equal(dragKnob(0.9, -500, false), 1);
  assert.equal(dragKnob(0.1, 500, false), 0);
});

test("with Shift the drag is four times finer", () => {
  assert.equal(dragKnob(0.5, -80, true), 0.6);
  assert.equal(dragKnob(0.5, 400, true), 0);
});

test("the wheel moves the knob a twentieth a notch, a fine notch a hundredth", () => {
  assert.equal(wheelKnob(0.5, 100, false), 0.45);
  assert.equal(wheelKnob(0.5, -3, false), 0.55);
  assert.equal(wheelKnob(0.5, -3, true), 0.51);
  assert.equal(wheelKnob(0.5, 0, false), 0.5);
  assert.equal(wheelKnob(0.98, -1, false), 1);
  assert.equal(wheelKnob(0.02, 1, false), 0);
});
