// The flip's pure parts: arrangement edits, effect painting, bar math, the grid, the confirm text.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  MAX_BARS,
  barCount,
  beatSurface,
  deleteBlock,
  draftProblem,
  finishedSince,
  renderOutcome,
  duplicateBlock,
  moveBlock,
  nudgeDownbeat,
  paintEffect,
  playToSource,
  scaleBpm,
  setRange,
  splitBlock,
  tapeCap,
  totalBars,
  useConfirm,
} from "../src/flip/model.ts";

const block = (label, from, to) => ({ label, from_bar: from, to_bar: to });
const layout = (...blocks) => ({ blocks, patterns: blocks.map(() => "8ths") });
const valid = (l, sourceBars) => {
  assert.equal(l.blocks.length, l.patterns.length);
  assert.ok(l.blocks.length >= 1);
  assert.ok(totalBars(l.blocks) <= MAX_BARS);
  for (const b of l.blocks) {
    assert.ok(Number.isInteger(b.from_bar) && Number.isInteger(b.to_bar));
    assert.ok(b.from_bar >= 0 && b.to_bar > b.from_bar && b.to_bar <= sourceBars);
  }
};

test("move swaps neighbours and refuses at the ends", () => {
  const l = layout(block("a", 0, 4), block("b", 4, 8), block("c", 8, 12));
  l.patterns = ["8ths", "16ths", "roll32"];
  const right = moveBlock(l, 0, 1);
  assert.deepEqual(right.blocks.map((b) => b.label), ["b", "a", "c"]);
  assert.deepEqual(right.patterns, ["16ths", "8ths", "roll32"]);
  assert.equal(moveBlock(l, 0, -1), null);
  assert.equal(moveBlock(l, 2, 1), null);
  assert.deepEqual(moveBlock(l, 2, -1).blocks.map((b) => b.label), ["a", "c", "b"]);
  valid(right, 12);
});

test("duplicate puts a copy after and stops at the bar limit", () => {
  const l = layout(block("a", 0, 4), block("b", 4, 8));
  const d = duplicateBlock(l, 1);
  assert.deepEqual(d.blocks.map((b) => [b.from_bar, b.to_bar]), [[0, 4], [4, 8], [4, 8]]);
  valid(d, 8);
  assert.equal(l.blocks.length, 2);
  const full = layout(block("a", 0, 300));
  assert.equal(duplicateBlock(full, 0), null);
  const edge = layout(block("a", 0, 256));
  assert.equal(totalBars(duplicateBlock(edge, 0).blocks), MAX_BARS);
});

test("delete keeps at least one block", () => {
  const l = layout(block("a", 0, 4), block("b", 4, 8));
  assert.deepEqual(deleteBlock(l, 0).blocks.map((b) => b.label), ["b"]);
  assert.deepEqual(deleteBlock(l, 1).blocks.map((b) => b.label), ["a"]);
  assert.equal(deleteBlock(layout(block("a", 0, 4)), 0), null);
  assert.equal(deleteBlock(l, 5), null);
});

test("split cuts strictly inside the block", () => {
  const l = layout(block("a", 2, 10));
  const s = splitBlock(l, 0, 6);
  assert.deepEqual(s.blocks.map((b) => [b.from_bar, b.to_bar]), [[2, 6], [6, 10]]);
  assert.equal(totalBars(s.blocks), 8);
  valid(s, 10);
  assert.equal(splitBlock(l, 0, 2), null);
  assert.equal(splitBlock(l, 0, 10), null);
  assert.equal(splitBlock(l, 0, 3.5), null);
  assert.deepEqual(splitBlock(l, 0, 3).blocks.map((b) => b.to_bar - b.from_bar), [1, 7]);
  assert.deepEqual(splitBlock(l, 0, 9).blocks.map((b) => b.to_bar - b.from_bar), [7, 1]);
});

test("a range change stays inside the source and the limit", () => {
  const l = layout(block("a", 0, 4), block("b", 4, 8));
  assert.deepEqual(setRange(l, 1, 6, 8, 8).blocks[1], block("b", 6, 8));
  assert.equal(setRange(l, 1, 6, 9, 8), null);
  assert.equal(setRange(l, 1, -1, 4, 8), null);
  assert.equal(setRange(l, 1, 5, 5, 8), null);
  assert.equal(setRange(l, 1, 2.5, 6, 8), null);
  const wide = layout(block("a", 0, 500), block("b", 0, 12));
  assert.equal(setRange(wide, 1, 0, 13, 600), null);
  assert.equal(totalBars(setRange(wide, 1, 0, 12, 600).blocks), 512);
});

test("effect painting clamps to the play order length", () => {
  const out = paintEffect([], { effect: "reverse", from_bar: 6, to_bar: 20, params: {} }, 8);
  assert.deepEqual(out, [{ effect: "reverse", from_bar: 6, to_bar: 8, params: {} }]);
  assert.deepEqual(paintEffect([], { effect: "reverse", from_bar: -3, to_bar: 2, params: {} }, 8)[0].from_bar, 0);
  assert.equal(paintEffect([], { effect: "reverse", from_bar: 8, to_bar: 12, params: {} }, 8), null);
  assert.equal(paintEffect([], { effect: "reverse", from_bar: 3, to_bar: 3, params: {} }, 8), null);
  const have = [{ effect: "stutter", from_bar: 0, to_bar: 2, params: { repeats: 4 } }];
  const two = paintEffect(have, { effect: "granular", from_bar: 1, to_bar: 3, params: {} }, 8);
  assert.equal(two.length, 2);
  assert.equal(have.length, 1);
});

test("effects past a shortened arrangement are cut or dropped", async () => {
  const { fitEffects } = await import("../src/flip/model.ts");
  const list = [
    { effect: "reverse", from_bar: 0, to_bar: 4, params: {} },
    { effect: "reverse", from_bar: 6, to_bar: 10, params: {} },
    { effect: "reverse", from_bar: 8, to_bar: 10, params: {} },
  ];
  assert.deepEqual(fitEffects(list, 8).map((e) => [e.from_bar, e.to_bar]), [[0, 4], [6, 8]]);
});

test("a play-order bar maps to its source bar", () => {
  const blocks = [block("a", 10, 14), block("b", 2, 4), block("c", 20, 21)];
  assert.deepEqual(playToSource(blocks, 0), { block: 0, bar: 10 });
  assert.deepEqual(playToSource(blocks, 3), { block: 0, bar: 13 });
  assert.deepEqual(playToSource(blocks, 4), { block: 1, bar: 2 });
  assert.deepEqual(playToSource(blocks, 5), { block: 1, bar: 3 });
  assert.deepEqual(playToSource(blocks, 6), { block: 2, bar: 20 });
  assert.equal(playToSource(blocks, 7), null);
  assert.equal(playToSource(blocks, -1), null);
});

test("the downbeat nudges by milliseconds and beats and stays at or above zero", () => {
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { ms: 10 }) - 1.01) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { ms: -10 }) - 0.99) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { beats: 1 }) - 1.5) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { beats: -1 }) - 0.5) < 1e-9);
  // before zero it moves a whole bar (2 s at 120 BPM) forward
  assert.ok(Math.abs(nudgeDownbeat(0.2, 120, { beats: -1 }) - 1.7) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(0.005, 120, { ms: -10 }) - 1.995) < 1e-9);
  assert.ok(nudgeDownbeat(0, 120, { ms: -10 }) >= 0);
});

test("half and double time stay inside 30..300", () => {
  assert.equal(scaleBpm(140, 0.5), 70);
  assert.equal(scaleBpm(140, 2), 280);
  assert.equal(scaleBpm(160, 2), null);
  assert.equal(scaleBpm(50, 0.5), null);
  assert.equal(scaleBpm(60, 0.5), 30);
});

test("whole bars fit between the downbeat and the end", () => {
  assert.equal(barCount(8, 0, 120), 4);
  assert.equal(barCount(8.1, 0.05, 120), 4);
  assert.equal(barCount(7.9, 0, 120), 3);
  assert.equal(barCount(1, 2, 120), 0);
  assert.equal(barCount(8, 0, 240), 8);
});

test("a tape effect is capped by the range and 4 s", () => {
  assert.ok(Math.abs(tapeCap(120, 1) - 2) < 1e-9);
  assert.equal(tapeCap(120, 4), 4);
  assert.equal(tapeCap(120, 8), 4);
});

test("the beat station picks the surface by what the beat is", () => {
  assert.equal(beatSurface({ kind: "own", seed: 3, bpm: 140, file: null }), "beat");
  assert.equal(beatSurface({ kind: "own", seed: 3, bpm: 140, file: "input/beats/x.mp3" }), "beat");
  assert.equal(beatSurface({ kind: "file", bpm: 140, file: "input/beats/x.mp3" }), "flip");
  assert.equal(beatSurface({ kind: "file", bpm: 140, file: "work/flips/1a2b3c4d/flip-2.wav" }), "flip");
  assert.equal(beatSurface({ kind: "file", bpm: null, file: null }), "flip");
});

test("the use confirm names the takes that change beat", () => {
  assert.ok(!useConfirm(2, 0).includes("дубл"));
  assert.ok(useConfirm(2, 0).includes("2"));
  assert.ok(useConfirm(2, 5).includes("5 дублей станут записанными под другой бит"));
  assert.ok(useConfirm(2, 1).includes("1 дубль станет записанным под другой бит"));
  assert.ok(useConfirm(2, 3).includes("3 дубля станут записанными под другой бит"));
  assert.ok(useConfirm(2, 11).includes("11 дублей станут"));
  assert.ok(useConfirm(2, 21).includes("21 дубль станет"));
});

test("no flip string says лучше", () => {
  const dir = new URL("../src/flip/", import.meta.url);
  const files = readdirSync(dir).filter((f) => f.endsWith(".ts"));
  assert.ok(files.length >= 3);
  for (const f of files) assert.ok(!/лучше/i.test(readFileSync(new URL(f, dir), "utf8")), f);
  assert.ok(!/лучше/i.test(readFileSync(new URL("../src/styles/flip.css", import.meta.url), "utf8")));
});

import { rangeOf } from "../src/flip/api.ts";
import { beatMeta, flipStale, flipTag, mayAdoptDraft, sameBpm } from "../src/flip/model.ts";

test("a bpm re-entered as shown is the same bpm", () => {
  assert.ok(sameBpm(131.4567, 131.46));
  assert.ok(sameBpm(132, 132));
  assert.ok(!sameBpm(131.4567, 131.47));
  assert.ok(!sameBpm(131.4567, 131.45));
});

test("the server draft is adopted only when nothing was edited since the read began", () => {
  const idle = { seqBefore: 3, seqNow: 3, edited: false, saving: false, failed: false };
  assert.ok(mayAdoptDraft(idle));
  assert.ok(!mayAdoptDraft({ ...idle, seqNow: 4 }));
  assert.ok(!mayAdoptDraft({ ...idle, edited: true }));
  assert.ok(!mayAdoptDraft({ ...idle, saving: true }));
  assert.ok(!mayAdoptDraft({ ...idle, failed: true }));
});

test("the flip surface is dropped when the song's source file changes, not when a render of it is picked", () => {
  assert.equal(flipTag(null, "input/beats/x.mp3"), "input/beats/x.mp3");
  assert.equal(flipTag("input/beats/x.mp3", "work/flips/1a2b3c4d/flip-2.wav"), "input/beats/x.mp3");
  assert.equal(flipTag(null, "work/flips/1a2b3c4d/flip-2.wav"), "work/flips/1a2b3c4d/flip-2.wav");
  assert.ok(!flipStale(null, "input/beats/y.mp3"));
  assert.ok(!flipStale("input/beats/x.mp3", "input/beats/x.mp3"));
  assert.ok(!flipStale("input/beats/x.mp3", "work/flips/1a2b3c4d/flip-2.wav"));
  assert.ok(flipStale("input/beats/x.mp3", "input/beats/y.mp3"));
  assert.ok(flipStale("work/flips/1a2b3c4d/flip-2.wav", "input/beats/y.mp3"));
});

test("the beat header names the file or the flip version", () => {
  assert.deepEqual(beatMeta("input/beats/x.mp3", 131.6), { text: "файл · x.mp3 · 132 BPM", title: "input/beats/x.mp3" });
  assert.deepEqual(beatMeta("input/beats/x.mp3", null), { text: "файл · x.mp3", title: "input/beats/x.mp3" });
  assert.deepEqual(beatMeta("work/flips/1a2b3c4d/flip-12.wav", 90), { text: "флип · версия 12 · 90 BPM", title: "work/flips/1a2b3c4d/flip-12.wav" });
  assert.deepEqual(beatMeta(null, null), { text: "файл", title: "" });
});

test("a param range keeps the unit the server gives", () => {
  assert.deepEqual(rangeOf({ low: 0.1, high: 2, default: 0.5, unit: "с" }), { low: 0.1, high: 2, def: 0.5, unit: "с" });
  assert.equal(rangeOf({ low: 0, high: 1 })?.unit, null);
});

const doc = (bpm, bassMode, hatsMode, bars) => ({
  grid: { bpm, downbeat_s: 0, swing: 0 },
  bass808: { mode: bassMode, params: {} },
  hats: { mode: hatsMode, remove: [], patterns: ["8ths"] },
  arrangement: [block("a", 0, bars)],
});

test("our parts refuse a tempo outside 40..220 and only then", () => {
  assert.ok(draftProblem(doc(230, "ours", "original", 8)));
  assert.ok(draftProblem(doc(39.9, "original", "ours", 8)));
  assert.equal(draftProblem(doc(230, "original", "original", 8)), null);
  assert.equal(draftProblem(doc(40, "ours", "ours", 8)), null);
  assert.equal(draftProblem(doc(220, "ours", "ours", 8)), null);
});

test("a play length over fifteen minutes is refused at the limit", () => {
  assert.equal(draftProblem(doc(60, "original", "original", 225)), null);
  assert.ok(draftProblem(doc(60, "original", "original", 226)));
  assert.equal(draftProblem(doc(130, "original", "original", 100)), null);
});

test("a finished render is told by a newer stamp", () => {
  const job = (state, finished) => ({ kind: "flip_render", state, reason: null, started: 1, finished });
  assert.equal(finishedSince(null, undefined), false);
  assert.equal(finishedSince(5, job("done", 5)), false);
  assert.equal(finishedSince(5, job("done", 6)), true);
  assert.equal(finishedSince(5, job("failed", 6)), true);
  assert.equal(finishedSince(null, job("done", 1)), true);
  assert.equal(finishedSince(5, job("running", null)), false);
  assert.equal(finishedSince(5, job("running", 9)), false);
});

test("a parked flip keeps a finished render ready without playing it", () => {
  assert.equal(renderOutcome({ failed: false, present: true, parked: false }), "play");
  assert.equal(renderOutcome({ failed: false, present: true, parked: true }), "ready");
  assert.equal(renderOutcome({ failed: true, present: false, parked: true }), "failed");
  assert.equal(renderOutcome({ failed: false, present: false, parked: false }), "missing");
});
