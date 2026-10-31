// The shared-pattern question: an edit of a pattern more than one section plays asks once
// «изменить во всех N местах / только здесь»; "only here" gives the section its own copy.
import assert from "node:assert/strict";
import { test } from "node:test";

import { chooseShare, shareGate } from "../src/daw/share.ts";

const score = () => ({
  version: 1,
  bpm: 140,
  seed: 1,
  key: "C minor",
  sounds: { kick: { instrument: "kick" } },
  patterns: {
    a: { bars: 1, rows: { kick: "x..............." } },
    b: { bars: 1, rows: { kick: "....x..........." } },
    f: { bars: 1, rows: { kick: "x.x.x.x.x.x.x.x." } },
  },
  sections: [
    { name: "intro", bars: 4, play: ["b"] },
    { name: "verse", bars: 8, play: ["a"], fill: "f" },
    { name: "hook", bars: 8, play: ["a"] },
    { name: "verse", bars: 8, play: ["a"], fill: "f" },
  ],
});

test("a pattern one section plays is edited straight away", () => {
  assert.deepEqual(shareGate(score(), 0, "b", new Set()), { kind: "go" });
});

test("a pattern three sections play asks, with the count of places", () => {
  assert.deepEqual(shareGate(score(), 2, "a", new Set()), { kind: "ask", count: 3 });
});

test("a fill used by two sections counts as shared too", () => {
  assert.deepEqual(shareGate(score(), 1, "f", new Set()), { kind: "ask", count: 2 });
});

test("once answered for a pattern, it does not ask again", () => {
  assert.deepEqual(shareGate(score(), 2, "a", new Set(["a"])), { kind: "go" });
  // another shared pattern still asks
  assert.deepEqual(shareGate(score(), 1, "f", new Set(["a"])), { kind: "ask", count: 2 });
});

test("«во всех местах» keeps the score and the pattern", () => {
  const s = score();
  const out = chooseShare(s, 2, "a", "all");
  assert.equal(out.pattern, "a");
  assert.deepEqual(out.score, s);
});

test("«только здесь» gives that section its own copy and the others keep the old one", () => {
  const s = score();
  const out = chooseShare(s, 2, "a", "here");
  assert.notEqual(out.pattern, "a");
  assert.deepEqual(out.score.sections.map((x) => x.play), [["b"], ["a"], [out.pattern], ["a"]]);
  assert.deepEqual(out.score.patterns[out.pattern], s.patterns.a);
  // the score given is left alone
  assert.deepEqual(s.sections[2].play, ["a"]);
});

test("«только здесь» on a fill copies the fill for that section", () => {
  const out = chooseShare(score(), 3, "f", "here");
  assert.notEqual(out.pattern, "f");
  assert.equal(out.score.sections[3].fill, out.pattern);
  assert.equal(out.score.sections[1].fill, "f");
});
