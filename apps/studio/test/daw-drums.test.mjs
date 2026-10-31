// The bottom editor's drum grid: a block's bar shows the bar of each pattern it plays, and a click
// toggles the step of that pattern bar.
import assert from "node:assert/strict";
import { test } from "node:test";

import { drumSteps, setHit } from "../src/beat/model.ts";
import { drumRows, patternStep, toggleHit } from "../src/daw/drums.ts";

const bar = (s) => s.replaceAll(" ", "");
const flat = (s) => s.replace(/[ |]/g, "");
const score = () => ({
  version: 1,
  bpm: 140,
  seed: 1,
  key: "C minor",
  sounds: {
    kick: { instrument: "kick" },
    hat: { instrument: "hat_closed" },
    bass: { instrument: "bass808" },
    snare: { instrument: "snare" },
  },
  patterns: {
    // two bars of kick and hats, and an 808 note row
    groove: {
      bars: 2,
      rows: {
        kick: bar("x... .... x... .... | .... x... .... ...."),
        hat: { steps: bar("x.x. x.x. x.x. x.x. | xxxx xxxx xxxx xxxx") },
        bass: [{ step: 0, len: 4, note: "C1", vel: 0.8 }],
      },
    },
    fill: { bars: 1, rows: { snare: bar(".... .... x.x. xxxx"), kick: bar("x... .... .... ....") } },
  },
  sections: [
    { name: "intro", bars: 2, play: ["groove"] },
    { name: "verse", bars: 4, play: ["groove"], fill: "fill" },
  ],
});

test("a block's bar maps onto its pattern's bar, round and round", () => {
  assert.equal(patternStep(0, 2, 0), 0);
  assert.equal(patternStep(1, 2, 3), 19);
  assert.equal(patternStep(2, 2, 3), 3);
  assert.equal(patternStep(3, 2, 15), 31);
  assert.equal(patternStep(5, 4, 0), 16);
  assert.equal(patternStep(0, 1, 7), 7);
});

test("the rows of a bar are the drum rows its patterns play there, without note rows", () => {
  // song bar 3 = the verse's 2nd bar = groove's 2nd bar
  const rows = drumRows(score(), 3);
  assert.deepEqual(rows.map((r) => [r.pattern, r.sound, r.base]), [
    ["groove", "hat", 16],
    ["groove", "kick", 16],
  ]);
  assert.equal(rows[0].hits.join(""), "xxxxxxxxxxxxxxxx");
  assert.equal(rows[1].hits.join(""), "....x...........");
});

test("on the last bar of a section with a fill, the fill's rows replace the ones it has", () => {
  // song bar 5 = the verse's last bar
  const rows = drumRows(score(), 5);
  const kick = rows.filter((r) => r.sound === "kick");
  assert.deepEqual(kick.map((r) => r.pattern), ["fill"]);
  assert.ok(rows.some((r) => r.pattern === "groove" && r.sound === "hat"));
  assert.ok(rows.some((r) => r.pattern === "fill" && r.sound === "snare" && r.fill));
  assert.equal(rows.find((r) => r.sound === "snare").base, 0);
});

test("a bar past the song has no rows", () => {
  assert.deepEqual(drumRows(score(), 6), []);
  assert.deepEqual(drumRows(score(), -1), []);
});

test("a click puts a hit on a rest and takes any hit away", () => {
  assert.equal(toggleHit(".", 0), "x");
  assert.equal(toggleHit("x", 0), ".");
  assert.equal(toggleHit("X", 0), ".");
  assert.equal(toggleHit("g", 0), ".");
  assert.equal(toggleHit("3", 0), ".");
});

test("with a roll picked a click on a rest puts that roll", () => {
  assert.equal(toggleHit(".", 2), "2");
  assert.equal(toggleHit(".", 6), "6");
  assert.equal(toggleHit("4", 4), ".");
});

test("a toggle through a row's base lands on the pattern bar the block's bar plays", () => {
  const s = score();
  const [, kick] = drumRows(s, 3);
  const step = kick.base + 2;
  const next = toggleHit(kick.hits[2], 0);
  const out = setHit(s, kick.pattern, kick.sound, step, next);
  assert.equal(drumSteps(out, "groove", "kick").join(""), flat("x... .... x... .... | ..x. x... .... ...."));
  // the first bar of the pattern is untouched
  assert.equal(drumSteps(out, "groove", "kick").slice(0, 16).join(""), flat("x... .... x... ...."));
});
