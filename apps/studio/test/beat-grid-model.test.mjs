// The grid's decisions, taken out of the DOM: which note focus picks, where a new note goes,
// what a click on a step does and when a drag gives way. Values follow the score format.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as model from "../src/beat/model.ts";

const note = (index, step, len, midi = 36) => ({ index, step, len, midi, vel: 0.8, passing: false });

test("focus entering a roll picks the first note of the bar shown, not of the pattern", () => {
  const notes = [note(0, 2, 4), note(1, 24, 2), note(2, 18, 2)];
  assert.equal(model.noteToFocus(notes, 1), 2);
  assert.equal(model.noteToFocus(notes, 0), 0);
});

test("a note held over from the bar before counts as in the bar shown", () => {
  assert.equal(model.noteToFocus([note(0, 14, 4), note(1, 20, 2)], 1), 0);
});

test("focus on a roll whose bar is empty picks no note", () => {
  assert.equal(model.noteToFocus([note(0, 2, 4)], 1), null);
  assert.equal(model.noteToFocus([], 0), null);
});

test("a note added after one ending past the bar stays in the bar shown", () => {
  assert.equal(model.addedNoteStep(note(0, 14, 4), 0, 2), 15);
});

test("a note added after one inside the bar follows it", () => {
  assert.equal(model.addedNoteStep(note(0, 4, 2), 0, 2), 6);
  assert.equal(model.addedNoteStep(note(0, 28, 4), 1, 2), 31);
});

test("a note added while the chosen one is in another bar starts the bar shown", () => {
  assert.equal(model.addedNoteStep(note(0, 2, 2), 1, 2), 16);
  assert.equal(model.addedNoteStep(null, 1, 2), 16);
});

const short = () =>
  model.parseScore(
    JSON.stringify({
      version: 1,
      bpm: 140,
      sounds: { kick: { instrument: "kick" } },
      patterns: { p: { bars: 1, rows: { kick: "x...x..." } } },
      sections: [{ name: "a", bars: 1, play: ["p"] }],
    }),
  );

test("a click on a step a short row lacks changes nothing and throws nothing", () => {
  const score = short();
  assert.equal(model.clickStep(score, "p", "kick", 8, false), null);
  assert.equal(model.clickStep(score, "p", "kick", 15, true), null);
});

test("a click on a step the row has cycles it, an alt-click rolls it", () => {
  const score = short();
  assert.deepEqual(model.clickStep(score, "p", "kick", 7, false), model.cycleStep(score, "p", "kick", 7));
  assert.deepEqual(model.clickStep(score, "p", "kick", 0, true), model.setHit(score, "p", "kick", 0, "2"));
});

test("a drag gives way once the score shown is not the one it made", () => {
  const made = short();
  assert.equal(model.dragOutlived(made, made), false);
  assert.equal(model.dragOutlived(made, short()), true);
});
