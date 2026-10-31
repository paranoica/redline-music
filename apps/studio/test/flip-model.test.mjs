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

test("the downbeat nudges by milliseconds and beats and stays within the lead before zero", () => {
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { ms: 10 }) - 1.01) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { ms: -10 }) - 0.99) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { beats: 1 }) - 1.5) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(1, 120, { beats: -1 }) - 0.5) < 1e-9);
  // up to 20 ms before the file it stays (a bar line the file cut off)
  assert.ok(Math.abs(nudgeDownbeat(0.005, 120, { ms: -10 }) - -0.005) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(0, 120, { ms: -10 }) - -0.01) < 1e-9);
  // further back it moves a whole bar (2 s at 120 BPM) forward
  assert.ok(Math.abs(nudgeDownbeat(0.2, 120, { beats: -1 }) - 1.7) < 1e-9);
  assert.ok(Math.abs(nudgeDownbeat(-0.015, 120, { ms: -10 }) - 1.975) < 1e-9);
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
  assert.ok(files.length >= 2);
  for (const f of files) assert.ok(!/лучше/i.test(readFileSync(new URL(f, dir), "utf8")), f);
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

// ---- notes, drums, block ops that carry them, effects that follow blocks ----
import {
  copyBlockTo,
  copyRefusal,
  deleteRefusal,
  moveBlockTo,
  normalisePatterns,
  remapEffects,
} from "../src/flip/model.ts";

const note = (step, len = 2, n = 36, vel = 100) => ({ step, len, note: n, vel });
const rich = (label, from, to, extra = {}) => ({ ...block(label, from, to), ...extra });
const fx = (effect, from, to, params = {}) => ({ effect, from_bar: from, to_bar: to, params });
const lengths = (l) => l.blocks.map((b) => b.to_bar - b.from_bar);
const mkdoc = (mode, blocks, patterns, over = {}) => ({
  format: 1,
  source: "x.wav",
  grid: { bpm: 120, downbeat_s: 0, swing: 50 },
  bass808: { mode: "original", params: {} },
  hats: { mode, remove: [], patterns },
  gains: { drums: 0, other: 0, bass: 0 },
  arrangement: blocks,
  effects: [],
  ...over,
});

test("moveBlockTo: j is the index in the list without the block", () => {
  const l = layout(block("a", 0, 1), block("b", 1, 2), block("c", 2, 3), block("d", 3, 4));
  l.patterns = ["p0", "p1", "p2", "p3"];
  const labels = (r) => r.blocks.map((b) => b.label).join("");
  assert.equal(labels(moveBlockTo(l, 0, 3)), "bcda");
  assert.equal(labels(moveBlockTo(l, 3, 0)), "dabc");
  assert.equal(labels(moveBlockTo(l, 1, 2)), "acbd");
  assert.equal(labels(moveBlockTo(l, 2, 2)), "abcd");
  const r = moveBlockTo(l, 0, 3);
  assert.equal(r.at, 3);
  assert.deepEqual(r.patterns, ["p1", "p2", "p3", "p0"]);
  assert.deepEqual(r.map, [3, 0, 1, 2]);
  assert.equal(moveBlockTo(l, 0, 4).ok, false);
  assert.equal(moveBlockTo(l, 0, -1).ok, false);
  assert.equal(moveBlockTo(l, 4, 0).ok, false);
});

test("move and copy make deep copies; the input is untouched", () => {
  const l = layout(rich("a", 0, 2, { notes: [note(0)], drums: { kick: "x".repeat(32) } }), block("b", 2, 4));
  const moved = moveBlockTo(l, 0, 1);
  moved.blocks[1].notes[0].step = 9;
  moved.blocks[1].drums.kick = "";
  const copied = copyBlockTo(l, 0, 2);
  copied.blocks[2].notes[0].vel = 1;
  copied.blocks[2].drums.kick = "";
  assert.equal(l.blocks[0].notes[0].step, 0);
  assert.equal(l.blocks[0].notes[0].vel, 100);
  assert.equal(l.blocks[0].drums.kick.length, 32);
  assert.equal(copied.blocks[0].notes[0].vel, 100);
  const dup = duplicateBlock(l, 0);
  dup.blocks[1].notes[0].len = 7;
  assert.equal(dup.blocks[0].notes[0].len, 2);
  const del = deleteBlock(l, 1);
  del.blocks[0].notes.push(note(5));
  assert.equal(l.blocks[0].notes.length, 1);
  const spl = splitBlock(l, 0, 1);
  spl.blocks[0].drums.kick = "z";
  assert.equal(l.blocks[0].drums.kick.length, 32);
});

test("absent notes and drums stay absent; an empty notes list is kept", () => {
  const l = layout(block("a", 0, 4), rich("b", 4, 8, { notes: [] }));
  for (const r of [moveBlockTo(l, 0, 1), copyBlockTo(l, 0, 0), duplicateBlock(l, 0), splitBlock(l, 0, 2), setRange(l, 0, 0, 3, 8)]) {
    for (const b of r.blocks) {
      assert.ok(!("drums" in b));
      assert.equal("notes" in b, b.label === "b");
    }
  }
  const back = JSON.parse(JSON.stringify(moveBlockTo(l, 0, 1).blocks));
  assert.deepEqual(back[0], { label: "b", from_bar: 4, to_bar: 8, notes: [] });
  assert.deepEqual(back[1], block("a", 0, 4));
  const split = splitBlock(l, 1, 6);
  assert.deepEqual(split.blocks[1].notes, []);
  assert.deepEqual(split.blocks[2].notes, []);
});

test("copyBlockTo puts the copy in the gap, refuses at 512 bars with a reason", () => {
  const l = layout(block("a", 0, 1), block("b", 1, 2));
  l.patterns = ["x", "y"];
  const r = copyBlockTo(l, 1, 0);
  assert.deepEqual(r.blocks.map((b) => b.label), ["b", "a", "b"]);
  assert.deepEqual(r.patterns, ["y", "x", "y"]);
  assert.deepEqual(r.map, [1, 2]);
  assert.equal(r.at, 0);
  assert.deepEqual(copyBlockTo(l, 0, 2).blocks.map((b) => b.label), ["a", "b", "a"]);
  assert.equal(copyBlockTo(l, 0, 3).ok, false);
  const full = layout(block("a", 0, 256), block("b", 0, 256));
  const no = copyBlockTo(full, 0, 1);
  assert.equal(no.ok, false);
  assert.match(no.reason, /512/);
  assert.match(copyRefusal(full, 0), /512/);
  assert.equal(copyRefusal(l, 0), null);
  const edge = layout(block("a", 0, 256), block("b", 0, 255), block("c", 0, 1));
  assert.equal(copyBlockTo(edge, 2, 3).ok, false);
  const under = layout(block("a", 0, 256), block("b", 0, 254), block("c", 0, 1));
  assert.equal(totalBars(copyBlockTo(under, 2, 3).blocks), 512);
});

test("deleting the last block is refused with a reason", () => {
  assert.match(deleteRefusal(layout(block("a", 0, 1)), 0), /Последний/);
  assert.equal(deleteRefusal(layout(block("a", 0, 1), block("b", 0, 1)), 1), null);
  assert.ok(deleteRefusal(layout(block("a", 0, 1)), 3));
});

test("split slices notes by step and drum rows at the cut", () => {
  const drums = { kick: "x...".repeat(8), hat: "." .repeat(16) + "x".repeat(16) };
  const l = layout(rich("a", 2, 4, { notes: [note(0), note(14, 4), note(16), note(20, 6, 40, 90), note(31, 17)], drums }));
  const s = splitBlock(l, 0, 3);
  const [a, b] = s.blocks;
  assert.deepEqual(a.notes, [note(0), note(14, 2)]);
  assert.deepEqual(b.notes, [note(0), note(4, 6, 40, 90), note(15, 17)]);
  assert.equal(a.drums.kick, "x...".repeat(4));
  assert.equal(b.drums.kick, "x...".repeat(4));
  assert.equal(a.drums.hat, ".".repeat(16));
  assert.equal(b.drums.hat, "x".repeat(16));
  assert.equal(drums.kick.length, 32);
  assert.deepEqual(l.blocks[0].notes[1], note(14, 4));
});

test("setRange: a front trim shifts the notes and drops those that leave the block", () => {
  const l = layout(rich("a", 2, 6, { notes: [note(0), note(16), note(33), note(50, 20), note(63, 17)] }));
  const t = setRange(l, 0, 3, 6, 10);
  assert.deepEqual(t.blocks[0].notes, [note(0), note(17), note(34, 20), note(47, 17)]);
  const end = setRange(l, 0, 2, 4, 10);
  assert.deepEqual(end.blocks[0].notes, [note(0), note(16)]);
  const both = setRange(l, 0, 3, 5, 10);
  assert.deepEqual(both.blocks[0].notes, [note(0), note(17)]);
  const wholeCut = setRange(l, 0, 4, 5, 10);
  assert.deepEqual(wholeCut.blocks[0].notes, [note(1)]);
});

test("setRange: a note that rings past the new end is shortened to the allowed tail", () => {
  const l = layout(rich("a", 0, 4, { notes: [note(20, 40)] }));
  const t = setRange(l, 0, 0, 2, 10);
  assert.deepEqual(t.blocks[0].notes, [note(20, 28)]);
});

test("setRange: growth takes the new bars from sourceNotes, only for a block that has notes", () => {
  const asked = [];
  const src = (bar) => {
    asked.push(bar);
    return [note(0, 2, 30 + bar), note(8, 2, 30 + bar)];
  };
  const l = layout(rich("a", 2, 4, { notes: [note(0, 2, 99)] }), block("plain", 4, 6));
  const g = setRange(l, 0, 1, 6, 8, src);
  assert.deepEqual(asked, [1, 4, 5]);
  assert.deepEqual(g.blocks[0].notes, [
    note(16, 2, 99),
    note(0, 2, 31),
    note(8, 2, 31),
    note(48, 2, 34),
    note(56, 2, 34),
    note(64, 2, 35),
    note(72, 2, 35),
  ]);
  const plain = setRange(l, 1, 3, 6, 8, src);
  assert.ok(!("notes" in plain.blocks[1]));
  const none = setRange(l, 0, 2, 5, 8);
  assert.deepEqual(none.blocks[0].notes, [note(0, 2, 99)]);
});

test("setRange pads and cuts drum rows with rests", () => {
  const l = layout(rich("a", 0, 2, { drums: { kick: "x".repeat(32), snare: "x...".repeat(8) } }));
  const g = setRange(l, 0, 0, 3, 8);
  assert.equal(g.blocks[0].drums.kick, "x".repeat(32) + ".".repeat(16));
  assert.equal(g.blocks[0].drums.snare, "x...".repeat(8) + ".".repeat(16));
  const c = setRange(l, 0, 0, 1, 8);
  assert.equal(c.blocks[0].drums.kick, "x".repeat(16));
  assert.equal(l.blocks[0].drums.kick.length, 32);
});

test("hats.patterns stays as long as the blocks after every op, in every hats mode", () => {
  for (const mode of ["original", "removed", "ours"]) {
    const loaded = normalisePatterns(mkdoc(mode, [block("a", 0, 4), block("b", 4, 8), block("c", 8, 12)], mode === "ours" ? ["16ths"] : []));
    assert.equal(loaded.hats.patterns.length, 3);
    assert.deepEqual(loaded.hats.patterns[0], mode === "ours" ? "16ths" : "8ths");
    const l = { blocks: loaded.arrangement, patterns: loaded.hats.patterns };
    const ragged = { blocks: l.blocks, patterns: [] };
    for (const base of [l, ragged]) {
      const n = base.blocks.length;
      const runs = {
        move: moveBlockTo(base, 0, 2),
        moveStep: moveBlock(base, 1, -1),
        copy: copyBlockTo(base, 1, 0),
        dup: duplicateBlock(base, 2),
        del: deleteBlock(base, 1),
        split: splitBlock(base, 0, 2),
        range: setRange(base, 0, 0, 2, 12),
      };
      const want = { move: n, moveStep: n, copy: n + 1, dup: n + 1, del: n - 1, split: n + 1, range: n };
      for (const [k, r] of Object.entries(runs)) {
        assert.equal(r.blocks.length, want[k], k);
        assert.equal(r.patterns.length, want[k], `${mode} ${k}`);
      }
    }
  }
  const over = normalisePatterns(mkdoc("ours", [block("a", 0, 1)], ["8ths", "16ths", "roll32"]));
  assert.deepEqual(over.hats.patterns, ["8ths"]);
  assert.equal(normalisePatterns(mkdoc("ours", [block("a", 0, 1)], [])).hats.patterns.length, 1);
});

test("patterns follow their blocks through a move", () => {
  const l = { blocks: [block("a", 0, 1), block("b", 1, 2), block("c", 2, 3)], patterns: ["p", "q", "r"] };
  assert.deepEqual(moveBlockTo(l, 2, 0).patterns, ["r", "p", "q"]);
  assert.deepEqual(deleteBlock(l, 0).patterns, ["q", "r"]);
  assert.deepEqual(splitBlock(l, 0, 0.5), null);
});

test("effects inside a moved block travel with it, keeping their place in it", () => {
  const l = {
    blocks: [block("a", 0, 4), block("b", 4, 6), block("c", 6, 10)],
    patterns: ["8ths", "8ths", "8ths"],
    effects: [fx("reverse", 1, 3), fx("stutter", 4, 5, { repeats: 4 }), fx("granular", 7, 9)],
    bpm: 120,
  };
  const r = moveBlockTo(l, 0, 2);
  assert.deepEqual(r.blocks.map((b) => b.label), ["b", "c", "a"]);
  assert.deepEqual(r.effects.map((e) => [e.effect, e.from_bar, e.to_bar]), [
    ["reverse", 7, 9],
    ["stutter", 0, 1],
    ["granular", 3, 5],
  ]);
  assert.deepEqual(r.effects[1].params, { repeats: 4 });
  assert.equal(l.effects[0].from_bar, 1);
  r.effects[1].params.repeats = 9;
  assert.equal(l.effects[1].params.repeats, 4);
});

test("an effect straddling blocks is cut at its block's end", () => {
  const l = {
    blocks: [block("a", 0, 2), block("b", 2, 6)],
    patterns: ["8ths", "8ths"],
    effects: [fx("reverse", 1, 4), fx("granular", 3, 6)],
  };
  const r = moveBlockTo(l, 0, 1);
  assert.deepEqual(r.effects.map((e) => [e.from_bar, e.to_bar]), [[5, 6], [1, 4]]);
  const c = copyBlockTo(l, 1, 0);
  assert.deepEqual(c.effects.map((e) => [e.from_bar, e.to_bar]), [[5, 6], [7, 10]]);
});

test("effects of a deleted block go; the rest close up", () => {
  const l = {
    blocks: [block("a", 0, 2), block("b", 2, 4), block("c", 4, 8)],
    patterns: ["8ths", "8ths", "8ths"],
    effects: [fx("reverse", 0, 2), fx("granular", 2, 4), fx("stutter", 5, 7)],
  };
  const r = deleteBlock(l, 1);
  assert.deepEqual(r.effects.map((e) => [e.effect, e.from_bar, e.to_bar]), [["reverse", 0, 2], ["stutter", 3, 5]]);
  const first = deleteBlock(l, 0);
  assert.deepEqual(first.effects.map((e) => [e.effect, e.from_bar]), [["granular", 0], ["stutter", 3]]);
  assert.equal(deleteBlock({ ...l, effects: [fx("reverse", 2, 3)] }, 1).effects.length, 0);
});

test("a copy leaves the effects on the original; split and a longer block keep them", () => {
  const l = { blocks: [block("a", 0, 4)], patterns: ["8ths"], effects: [fx("reverse", 1, 3)] };
  assert.deepEqual(copyBlockTo(l, 0, 1).effects.map((e) => [e.from_bar, e.to_bar]), [[1, 3]]);
  assert.deepEqual(duplicateBlock(l, 0).effects.length, 1);
  assert.deepEqual(splitBlock(l, 0, 2).effects.map((e) => [e.from_bar, e.to_bar]), [[1, 3]]);
  assert.deepEqual(setRange(l, 0, 0, 6, 8).effects.map((e) => [e.from_bar, e.to_bar]), [[1, 3]]);
  assert.deepEqual(setRange(l, 0, 0, 2, 8).effects.map((e) => [e.from_bar, e.to_bar]), [[1, 2]]);
  assert.equal(setRange(l, 0, 0, 1, 8).effects.length, 0);
  assert.equal(moveBlockTo(layout(block("a", 0, 1), block("b", 1, 2)), 0, 1).effects, undefined);
});

test("a tape effect is re-capped to its range after a trim", () => {
  const l = {
    blocks: [block("a", 0, 4)],
    patterns: ["8ths"],
    bpm: 120,
    effects: [
      fx("tape_stop", 0, 4, { duration: 4, time: 1 }),
      fx("tape_start", 0, 3, { duration: 3 }),
      fx("tape_stop", 0, 2, {}),
    ],
  };
  const r = setRange(l, 0, 0, 1, 8);
  assert.deepEqual(r.effects.map((e) => e.to_bar), [1, 1, 1]);
  assert.equal(r.effects[0].params.duration, 2);
  assert.equal(r.effects[0].params.time, 1);
  assert.equal(r.effects[1].params.duration, 2);
  assert.equal(r.effects[2].params.duration, undefined);
  const tight = setRange({ ...l, bpm: 240 }, 0, 0, 1, 8);
  assert.equal(tight.effects[0].params.duration, 1);
  assert.ok(!("time" in tight.effects[0].params));
  assert.equal(tight.effects[2].params.duration, undefined);
  const t = remapEffects(l.blocks, l.blocks, [fx("tape_stop", 0, 1, { duration: 4 })], [0], 240);
  assert.equal(t[0].params.duration, 1);
  const half = remapEffects(l.blocks, [block("a", 0, 1)], [fx("tape_stop", 0, 1, {})], [0], 480);
  assert.equal(half[0].params.duration, undefined);
  const narrow = remapEffects(l.blocks, [block("a", 0, 1)], [fx("tape_stop", 0, 1, {})], [0], 960);
  assert.equal(narrow[0].params.duration, 0.25);
  const reverse = remapEffects(l.blocks, [block("a", 0, 1)], [fx("reverse", 0, 4, { x: 1 })], [0], 120);
  assert.deepEqual(reverse[0], fx("reverse", 0, 1, { x: 1 }));
});

test("draftProblem also guards a block with drums or notes at a tempo the synth refuses", () => {
  const plain = mkdoc("original", [block("a", 0, 4)], []);
  assert.equal(draftProblem({ ...plain, grid: { ...plain.grid, bpm: 250 } }), null);
  const fast = (b) => draftProblem(mkdoc("original", [b], [], { grid: { bpm: 250, downbeat_s: 0, swing: 50 } }));
  assert.match(fast(rich("a", 0, 4, { drums: { kick: "x".repeat(64) } })), /250/);
  assert.match(fast(rich("a", 0, 4, { notes: [note(0)] })), /250/);
  assert.equal(fast(rich("a", 0, 4, { notes: [] })), null);
  assert.equal(fast(rich("a", 0, 4, { drums: {} })), null);
  assert.equal(fast(rich("a", 0, 4, { drums: {}, notes: [] })), null);
  assert.equal(fast(block("a", 0, 4)), null);
  const slow = mkdoc("original", [rich("a", 0, 4, { drums: { kick: "x".repeat(64) } })], []);
  assert.equal(draftProblem(slow), null);
  assert.equal(draftProblem({ ...slow, grid: { ...slow.grid, bpm: 40 } }), null);
  assert.ok(draftProblem({ ...slow, grid: { ...slow.grid, bpm: 39 } }));
  assert.ok(draftProblem({ ...slow, grid: { ...slow.grid, bpm: 221 } }));
  assert.ok(draftProblem(mkdoc("ours", [block("a", 0, 4)], ["8ths"], { grid: { bpm: 250, downbeat_s: 0, swing: 50 } })));
});

test("a straddling effect stays cut at its old block's end when that block grows", () => {
  const l = { blocks: [block("a", 0, 2), block("b", 2, 4)], patterns: ["8ths", "8ths"], effects: [fx("reverse", 1, 3)] };
  assert.deepEqual(setRange(l, 0, 0, 4, 8).effects.map((e) => [e.from_bar, e.to_bar]), [[1, 2]]);
});
