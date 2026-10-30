// The rail: stations json to lit / dim / folded / current, for the song states of the mockup.
import assert from "node:assert/strict";
import { test } from "node:test";

import { defaultStation, railItems, reachOf } from "../src/song/rail.ts";

const text = (over = {}) => ({ seed: 12, version: null, versions: [], pending: null, command: "cmd", ...over });
const stations = (over = {}) => ({
  beat: { kind: "own", seed: 1, bpm: 90, file: "work/beats/1/beat.wav", rendered: true },
  text: text(),
  voice: { kind: "takes", folder: "input/takes/x", takes: [], chosen: null },
  mix: { ready: false, reason: "no voice chosen", variant: null },
  result: { folder: null },
  ...over,
});
const states = (items) => items.map((i) => i.state);
const lit = (items) => items.map((i) => i.lit);

test("a new song: only the beat is lit, the text is missing", () => {
  const items = railItems({ stations: stations() });
  assert.deepEqual(states(items), ["done", "missing", "todo", "todo", "todo", "todo"]);
  assert.deepEqual(lit(items), [true, false, false, false, false, false]);
  assert.equal(items[0].value, "seed 1 · 90 BPM");
  assert.equal(items[1].value, "нет текста");
  assert.equal(items[2].value, "после текста");
  assert.equal(reachOf(items), 0);
  assert.equal(defaultStation(items), "text");
});

test("texts exist but none is chosen: dim, with the count", () => {
  const items = railItems({ stations: stations({ text: text({ versions: [1, 2, 3] }) }) });
  assert.equal(items[1].state, "todo");
  assert.equal(items[1].value, "3 текста · не выбран");
});

test("the middle: text chosen, takes ranked, voice not chosen", () => {
  const s = stations({
    text: text({ version: 3, versions: [1, 2, 3] }),
    voice: { kind: "takes", folder: "f", takes: ["take-01.wav", "take-02.wav"], chosen: null },
  });
  const ranking = { takes: [{ name: "take-04.wav", total: 12 }] };
  const items = railItems({ stations: s, ranking, current: "takes" });
  assert.deepEqual(states(items), ["done", "done", "done", "done", "todo", "todo"]);
  assert.equal(items[2].value, "2 дубля");
  assert.equal(items[3].value, "дубль 04 · 12 баллов");
  assert.equal(items[4].value, "ждёт выбор дубля");
  assert.equal(reachOf(items), 0.6);
  assert.deepEqual(items.map((i) => i.current), [false, false, false, true, false, false]);
  assert.equal(defaultStation(items), "mix");
});

test("done: every station is lit, the result opens", () => {
  const s = stations({
    text: text({ version: 3, versions: [3] }),
    voice: { kind: "takes", folder: "f", takes: ["take-01.wav"], chosen: "take-01.wav" },
    mix: { ready: true, reason: null, variant: 0 },
    result: { folder: "output/demo/run" },
  });
  const items = railItems({ stations: s });
  assert.deepEqual(states(items), ["done", "done", "done", "done", "done", "done"]);
  assert.equal(reachOf(items), 1);
  assert.equal(items[3].value, "взят дубль 01");
  assert.equal(items[4].value, "оставлен A · авто");
  assert.equal(defaultStation(items), "result");
});

test("a Suno voice folds the recording and the line runs through", () => {
  const s = stations({
    beat: { kind: "file", file: "input/b.wav", bpm: 92 },
    text: text({ version: 1, versions: [1] }),
    voice: { kind: "suno", file: "input/v.wav", separated: true },
  });
  const items = railItems({ stations: s });
  assert.equal(items[2].state, "bypass");
  assert.equal(items[2].folded, true);
  assert.equal(items[2].lit, true);
  assert.equal(items[3].value, "голос Suno · отделён");
  assert.equal(items.filter((i) => i.folded).length, 1);
  assert.equal(defaultStation(items), "mix");
});

test("a take being recorded is the one live station", () => {
  const s = stations({
    text: text({ version: 3, versions: [3] }),
    voice: { kind: "takes", folder: "f", takes: [], chosen: null },
  });
  const rec = { stage: "ready", recording: true, take: "take-07.wav", seconds: 72.4 };
  const items = railItems({ stations: s, rec });
  assert.equal(items[2].state, "live");
  assert.equal(items[2].value, "дубль 07 · 01:12");
  assert.equal(items.filter((i) => i.state === "live").length, 1);
  assert.equal(defaultStation(items), "rec");
  assert.notEqual(railItems({ stations: s, rec: { ...rec, recording: false } })[2].state, "live");
});

test("a running job shows on its station", () => {
  const s = stations({ voice: { kind: "takes", folder: "f", takes: ["take-01.wav"], chosen: null }, text: text({ version: 1, versions: [1] }) });
  const items = railItems({ stations: s, jobs: [{ kind: "rank", state: "running" }] });
  assert.equal(items[3].value, "дубли оцениваются");
  const finished = railItems({ stations: s, jobs: [{ kind: "rank", state: "done" }] });
  assert.equal(finished[3].value, "не оценены");
});
