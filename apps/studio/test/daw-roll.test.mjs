// The bottom editor's piano roll: where a press lands, what a drag makes, and which notes glide.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dragTarget, glidePairs, pitchAt, rollHit, rollScrollTop, stepAt } from "../src/daw/roll.ts";

// a 2-bar pattern shown over a 4-bar block: 32 pattern steps, 64 view steps, 10 px a step,
// 8 px a semitone, MIDI 40 on the top row down to 28
const L = { stepPx: 10, rowPx: 8, hi: 40, lo: 28, patternSteps: 32, viewSteps: 64 };

const note = (index, step, len, midi) => ({ index, step, len, midi, vel: 0.8, passing: false });

test("x gives the view step under the pointer, clamped to the steps shown", () => {
  assert.equal(stepAt(L, 0), 0);
  assert.equal(stepAt(L, 9.9), 0);
  assert.equal(stepAt(L, 10), 1);
  assert.equal(stepAt(L, 639), 63);
  assert.equal(stepAt(L, 640), 63);
  assert.equal(stepAt(L, -5), 0);
});

test("y gives the pitch of the row under the pointer, top row highest", () => {
  assert.equal(pitchAt(L, 0), 40);
  assert.equal(pitchAt(L, 7.9), 40);
  assert.equal(pitchAt(L, 8), 39);
  assert.equal(pitchAt(L, 12 * 8 + 1), 28);
  assert.equal(pitchAt(L, 500), 28);
  assert.equal(pitchAt(L, -3), 40);
});

test("a press on empty space names the pattern step and pitch for a new note", () => {
  const hit = rollHit(L, [note(0, 0, 4, 36)], 55, 8 * 4 + 2);
  assert.deepEqual(hit, { kind: "empty", step: 5, midi: 36 });
});

test("an empty press in the block's second pass of the pattern maps back onto the pattern", () => {
  // view step 37 is pattern step 5 of the second pass
  assert.deepEqual(rollHit(L, [], 375, 0), { kind: "empty", step: 5, midi: 40 });
});

test("a press on a note's body moves it, on its last few pixels resizes it", () => {
  const notes = [note(3, 4, 4, 36)];
  const y = 8 * 4 + 1;
  assert.deepEqual(rollHit(L, notes, 41, y), { kind: "note", index: 3, mode: "move" });
  assert.deepEqual(rollHit(L, notes, 75, y), { kind: "note", index: 3, mode: "move" });
  assert.deepEqual(rollHit(L, notes, 76, y), { kind: "note", index: 3, mode: "resize" });
  assert.deepEqual(rollHit(L, notes, 79, y), { kind: "note", index: 3, mode: "resize" });
  // one step past its end is empty
  assert.deepEqual(rollHit(L, notes, 80, y), { kind: "empty", step: 8, midi: 36 });
  // the same steps a semitone up are empty
  assert.equal(rollHit(L, notes, 50, y - 8).kind, "empty");
});

test("the note's copy in the second pass is the same note", () => {
  const notes = [note(1, 4, 4, 36)];
  const y = 8 * 4 + 1;
  assert.deepEqual(rollHit(L, notes, (32 + 5) * 10, y), { kind: "note", index: 1, mode: "move" });
  assert.deepEqual(rollHit(L, notes, (32 + 8) * 10 - 2, y), { kind: "note", index: 1, mode: "resize" });
});

test("of two notes over the same step and pitch the later one is hit", () => {
  const notes = [note(0, 0, 8, 36), note(1, 4, 2, 36)];
  assert.deepEqual(rollHit(L, notes, 45, 8 * 4 + 1), { kind: "note", index: 1, mode: "move" });
});

test("a short note is all body: its resize edge would eat it", () => {
  const thin = { ...L, stepPx: 4 };
  const notes = [note(0, 2, 1, 36)];
  assert.deepEqual(rollHit(thin, notes, 11, 8 * 4 + 1), { kind: "note", index: 0, mode: "move" });
});

test("a move drag rounds to whole steps and semitones; down is lower", () => {
  const n = note(0, 4, 4, 36);
  assert.deepEqual(dragTarget(n, "move", 0, 0, L), { step: 4, midi: 36 });
  assert.deepEqual(dragTarget(n, "move", 14, 0, L), { step: 5, midi: 36 });
  assert.deepEqual(dragTarget(n, "move", 16, 0, L), { step: 6, midi: 36 });
  assert.deepEqual(dragTarget(n, "move", -41, 0, L), { step: 0, midi: 36 });
  assert.deepEqual(dragTarget(n, "move", 0, 17, L), { step: 4, midi: 34 });
  assert.deepEqual(dragTarget(n, "move", 0, -8, L), { step: 4, midi: 37 });
});

test("a resize drag changes the length only, never below one step", () => {
  const n = note(0, 4, 4, 36);
  assert.deepEqual(dragTarget(n, "resize", 20, 30, L), { len: 6 });
  assert.deepEqual(dragTarget(n, "resize", -26, 0, L), { len: 1 });
  assert.deepEqual(dragTarget(n, "resize", -500, 0, L), { len: 1 });
});

test("a note that starts while the one before still sounds is a glide from it", () => {
  const notes = [note(0, 0, 6, 36), note(1, 4, 4, 41), note(2, 8, 2, 36), note(3, 12, 4, 31)];
  // 1 starts at 4 inside 0 (0..6); 2 starts at 8 = the end of 1 (4..8): touching is no glide
  assert.deepEqual(glidePairs(notes), [{ from: 0, to: 1 }]);
});

test("glides are found whatever order the notes come in, and none in an empty row", () => {
  const notes = [note(5, 10, 2, 36), note(2, 0, 12, 33)];
  assert.deepEqual(glidePairs(notes), [{ from: 2, to: 5 }]);
  assert.deepEqual(glidePairs([]), []);
  // two notes starting together are a chord, not a glide
  assert.deepEqual(glidePairs([note(0, 0, 4, 36), note(1, 0, 4, 40)]), []);
});

test("the roll opens scrolled so the block's notes are in view, the lowest included", () => {
  // 13 rows of 8 px: 104 px of roll in a 40 px view, scrolled 0..64
  const view = 40;
  // MIDI 30..32 is rows 8..10, 64..88 px: centred at 76
  assert.equal(rollScrollTop(L, [30, 32, 31], view), 56);
  // the bottom row alone: as far down as the roll goes, not left under the edge
  assert.equal(rollScrollTop(L, [28], view), 64);
  const top = rollScrollTop(L, [29], view);
  assert.ok((40 - 29) * 8 >= top && (40 - 29 + 1) * 8 <= top + view, "MIDI 29 is in view");
  // the top row alone: the roll's start
  assert.equal(rollScrollTop(L, [40], view), 0);
  // notes wider than the view: the lowest stay in view at its bottom
  assert.equal(rollScrollTop(L, [30, 40], 40), 48);
  // a view taller than the roll does not scroll
  assert.equal(rollScrollTop(L, [28, 40], 200), 0);
  // no notes: the middle of the roll
  assert.equal(rollScrollTop(L, [], view), 32);
});
