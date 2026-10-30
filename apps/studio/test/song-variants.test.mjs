// The variant pads: A plus up to three, distances against A, and no verdict wording.
import assert from "node:assert/strict";
import { test } from "node:test";

import { PADS_KEY, PADS_LEDE, padModel, padSettings, relationText } from "../src/song/variants.ts";

const reply = (variants) => ({
  auto: { vocal_db: -6, comp_threshold_db: -32, drive_db: 10, distance_before: 0.5, distance_after: 0.31, at_edge: [] },
  variants,
  seed: 7,
});
const v = (axis, value, distance) => ({ settings: {}, axis, direction: "down", value, distance });

test("no answer from the server: no pads", () => {
  assert.deepEqual(padModel(null, null), []);
});

test("A is the auto result; B to D are closer or further than A by their distance", () => {
  const pads = padModel(reply([v("vocal_db", -9, 0.36), v("comp_threshold_db", -38, 0.28), v("drive_db", 0, 0.31)]), null);
  assert.deepEqual(pads.map((p) => p.letter), ["A", "B", "C", "D"]);
  assert.equal(pads[0].kind, "авто");
  assert.equal(pads[0].relation, null);
  assert.deepEqual(pads.slice(1).map((p) => p.relation), ["further", "closer", "same"]);
  assert.equal(relationText("further", pads[1].delta), "дальше на 0,05");
  assert.equal(relationText("closer", pads[2].delta), "ближе на 0,03");
  assert.equal(relationText("same", 0), "так же близко");
  assert.equal(pads[1].axis, "уровень голоса: −6 → −9 дБ");
  assert.equal(pads[2].axis, "порог компрессора: −32 → −38 дБ");
  assert.equal(pads[3].axis, "драйв: 10 → 0 дБ");
});

test("at most three variants, fewer when the server found fewer", () => {
  const four = reply([v("vocal_db", -9, 0.4), v("vocal_db", -3, 0.4), v("drive_db", 0, 0.4), v("drive_db", 20, 0.4)]);
  assert.equal(padModel(four, null).length, 4);
  assert.equal(padModel(reply([v("drive_db", 0, 0.4)]), null).length, 2);
  assert.equal(padModel(reply([]), null).length, 1);
});

test("bars: the longest distance is 1 and the kept pad is marked", () => {
  const pads = padModel(reply([v("vocal_db", -9, 0.62), v("drive_db", 0, 0.31)]), 1);
  assert.equal(pads[1].bar, 1);
  assert.equal(pads[0].bar, 0.5);
  assert.deepEqual(pads.map((p) => p.kept), [false, true, false]);
  assert.equal(padModel(reply([]), 0)[0].kept, true);
  assert.equal(padModel(reply([]), null)[0].kept, false);
});

test("a variant without a distance has no verdict", () => {
  const pad = padModel(reply([v("drive_db", 0, null)]), null)[1];
  assert.equal(pad.relation, null);
  assert.equal(pad.bar, null);
});

test("no verdict wording anywhere", () => {
  const texts = [PADS_KEY, PADS_LEDE, relationText("closer", -0.1), relationText("further", 0.1), relationText("same", 0)];
  const pads = padModel(reply([v("vocal_db", -9, 0.36), v("drive_db", 0, 0.2)]), 0);
  for (const p of pads) texts.push(p.kind, p.axis);
  for (const t of texts) assert.doesNotMatch(t, /лучш|хуж|better|worse/i);
});

test("a pad's settings are what the mix plays: A without the search's numbers, B to D as drawn", () => {
  const drawn = { vocal_db: -9, comp_threshold_db: -32, drive_db: 10 };
  const r = reply([{ settings: drawn, axis: "vocal_db", direction: "down", value: -9, distance: 0.36 }]);
  assert.deepEqual(padSettings(r, 0), { vocal_db: -6, comp_threshold_db: -32, drive_db: 10 });
  assert.deepEqual(padSettings(r, 1), drawn);
  assert.equal(padSettings(r, 2), null);
  assert.equal(padSettings(null, 0), null);
});
