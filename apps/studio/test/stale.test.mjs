import assert from "node:assert/strict";
import { test } from "node:test";

import { changedKeys, needsAnalysis, staleKeys } from "../src/core/stale.ts";

const stale = { gate_db: [], makeup_db: ["chain_on", "comp_ratio"], master_gain_db: ["comp_ratio", "beat_db", "rack"] };

test("the stale-making settings are every key any number lists", () => {
  assert.deepEqual([...staleKeys(stale)].sort(), ["beat_db", "chain_on", "comp_ratio", "rack"]);
  assert.equal(staleKeys({}).size, 0);
});

test("changed keys compare values, and the rack by its content", () => {
  const a = { version: 2, beat_db: 0, chain_on: true, rack: [{ effect: "gain", on: true, params: { db: 1 }, automation: [] }] };
  assert.deepEqual(changedKeys(a, structuredClone(a)), []);
  const b = { ...structuredClone(a), beat_db: 3 };
  b.rack[0].params.db = 2;
  assert.deepEqual(changedKeys(a, b).sort(), ["beat_db", "rack"]);
  assert.deepEqual(changedKeys(a, { ...a, extra: 1 }), ["extra"]);
});

test("an analysis is due when a changed key makes a number stale, not otherwise", () => {
  const keys = staleKeys(stale);
  assert.equal(needsAnalysis(["beat_db"], keys), true);
  assert.equal(needsAnalysis(["vocal_db"], keys), false);
  assert.equal(needsAnalysis([], keys), false);
});
