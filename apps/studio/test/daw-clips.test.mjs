// The beat lane's blocks: own beat sections, a flip's arrangement, a plain file.
import assert from "node:assert/strict";
import { test } from "node:test";

import { fileClips, flipClips, ownClips, shareCount, sharedPatterns } from "../src/daw/clips.ts";
import { grid } from "../src/daw/time.ts";

const score = (sections) => ({ version: 1, bpm: 140, seed: 1, key: "C", sounds: {}, patterns: {}, sections });

test("own sections become blocks one after another, with their patterns", () => {
  const s = score([
    { name: "intro", bars: 4, play: ["a"] },
    { name: "verse", bars: 16, play: ["a", "b"], fill: "f" },
    { name: "hook", bars: 8, play: ["c", "b"] },
  ]);
  assert.deepEqual(ownClips(s), [
    { index: 0, label: "intro", fromBar: 0, bars: 4, patterns: ["a"], fill: null, source: null },
    { index: 1, label: "verse", fromBar: 4, bars: 16, patterns: ["a", "b"], fill: "f", source: null },
    { index: 2, label: "hook", fromBar: 20, bars: 8, patterns: ["c", "b"], fill: null, source: null },
  ]);
});

test("own blocks do not share arrays with the score", () => {
  const s = score([{ name: "verse", bars: 8, play: ["a"] }]);
  ownClips(s)[0].patterns.push("x");
  assert.deepEqual(s.sections[0].play, ["a"]);
});

test("a score with no sections has no blocks", () => {
  assert.deepEqual(ownClips(score([])), []);
});

test("shared patterns are the ones more than one section plays or fills with", () => {
  const s = score([
    { name: "intro", bars: 4, play: ["a"], fill: "g" },
    { name: "verse", bars: 8, play: ["a", "b"], fill: "f" },
    { name: "hook", bars: 8, play: ["c", "b"], fill: "g" },
    { name: "outro", bars: 4, play: ["c"], fill: "c" },
  ]);
  assert.deepEqual(sharedPatterns(s), [
    { pattern: "a", sections: [0, 1] },
    { pattern: "b", sections: [1, 2] },
    { pattern: "c", sections: [2, 3] },
    { pattern: "g", sections: [0, 2] },
  ]);
  assert.equal(shareCount(s, "a"), 2);
  assert.equal(shareCount(s, "c"), 2);
  assert.equal(shareCount(s, "f"), 1);
  assert.equal(shareCount(s, "missing"), 0);
});

test("a pattern played by one section only is not shared", () => {
  const s = score([
    { name: "verse", bars: 8, play: ["a"] },
    { name: "hook", bars: 8, play: ["b"] },
  ]);
  assert.deepEqual(sharedPatterns(s), []);
});

test("a flip's blocks follow the play order and keep their source bars", () => {
  const doc = {
    arrangement: [
      { label: "A", from_bar: 8, to_bar: 16 },
      { label: "B", from_bar: 0, to_bar: 4 },
      { label: "A", from_bar: 8, to_bar: 16 },
    ],
  };
  assert.deepEqual(flipClips(doc), [
    { index: 0, label: "A", fromBar: 0, bars: 8, patterns: [], fill: null, source: { fromBar: 8, toBar: 16 } },
    { index: 1, label: "B", fromBar: 8, bars: 4, patterns: [], fill: null, source: { fromBar: 0, toBar: 4 } },
    { index: 2, label: "A", fromBar: 12, bars: 8, patterns: [], fill: null, source: { fromBar: 8, toBar: 16 } },
  ]);
  assert.deepEqual(flipClips({ arrangement: [] }), []);
});

test("a plain file is one block over the whole file", () => {
  assert.deepEqual(fileClips(grid(120), 60, "бит"), [
    { index: 0, label: "бит", fromBar: 0, bars: 30, patterns: [], fill: null, source: null },
  ]);
  const [tail] = fileClips(grid(120), 61, "бит");
  assert.equal(tail.bars, 30.5);
  const [early] = fileClips(grid(120, -0.02), 60, "бит");
  assert.ok(Math.abs(early.fromBar - 0.01) < 1e-9);
  assert.equal(early.bars, 30);
  assert.deepEqual(fileClips(grid(120), 0, "бит"), []);
});
