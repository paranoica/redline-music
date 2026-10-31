// Export runs what is stale, in order: the own beat's commit, the mix, then the result; the button's
// words follow the step.
import assert from "node:assert/strict";
import { test } from "node:test";

import { exportPlan, exportText } from "../src/daw/export.ts";

const facts = (over = {}) => ({ beatKind: "file", hasBeatAudio: true, beatDirty: false, mixStale: false, ...over });

test("nothing stale: only the result", () => {
  assert.deepEqual(exportPlan(facts()), ["result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "own" })), ["result"]);
});

test("a stale mix runs before the result", () => {
  assert.deepEqual(exportPlan(facts({ mixStale: true })), ["mix", "result"]);
});

test("an own beat with edits or no render is committed first", () => {
  assert.deepEqual(exportPlan(facts({ beatKind: "own", beatDirty: true, mixStale: true })), ["commit", "mix", "result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "own", beatDirty: true })), ["commit", "result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "own", hasBeatAudio: false })), ["commit", "result"]);
});

test("a flip with edits is committed first, a clean flip or a file beat never", () => {
  assert.deepEqual(exportPlan(facts({ beatKind: "flip", beatDirty: true })), ["commit", "result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "flip", beatDirty: true, mixStale: true })), ["commit", "mix", "result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "flip" })), ["result"]);
  assert.deepEqual(exportPlan(facts({ beatKind: "file", hasBeatAudio: false, beatDirty: true, mixStale: true })), ["mix", "result"]);
});

test("the button says the running step, then done", () => {
  assert.equal(exportText({ kind: "idle" }), "Экспорт");
  assert.equal(exportText({ kind: "running", step: "commit" }), "бит…");
  assert.equal(exportText({ kind: "running", step: "mix" }), "сведение…");
  assert.equal(exportText({ kind: "running", step: "result" }), "рендер…");
  assert.equal(exportText({ kind: "done", folder: "output/x" }), "готово");
  // a failure leaves the button ready for another try
  assert.equal(exportText({ kind: "failed", message: "no" }), "Экспорт");
});
