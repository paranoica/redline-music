import assert from "node:assert/strict";
import { test } from "node:test";

import { catalogueOf, planRack } from "../src/core/diff.ts";

const catalogue = catalogueOf([
  { effect: "gain", offline_only: false, params: [{ name: "db", low: -60, high: 24, default: 0, unit: "dB" }] },
  {
    effect: "eq",
    offline_only: false,
    params: [
      { name: "kind", low: 0, high: 2, default: 0, unit: "choice", choices: ["peak", "lowshelf", "highshelf"] },
      { name: "freq", low: 20, high: 20000, default: 1000, unit: "Hz" },
      { name: "gain", low: -24, high: 24, default: 0, unit: "dB" },
    ],
  },
  {
    effect: "drift",
    offline_only: false,
    params: [
      { name: "depth", low: 0, high: 100, default: 20, unit: "cents" },
      { name: "seed", low: 0, high: 65535, default: 0, unit: "", fixed_while_playing: true },
    ],
  },
]);

const gain = (db, on = true) => ({ effect: "gain", on, params: { db } });
const eq = (params) => ({ effect: "eq", on: true, params });

test("nothing changed is the same rack", () => {
  assert.deepEqual(planRack([gain(-6), eq({ kind: "peak", freq: 400 })], [gain(-6), eq({ kind: "peak", freq: 400 })], catalogue), {
    kind: "same",
  });
  assert.deepEqual(planRack([], [], catalogue), { kind: "same" });
});

test("numbers that moved are set in place, at their slots", () => {
  const plan = planRack([gain(-6), eq({ freq: 400, gain: 1 })], [gain(-3), eq({ freq: 400, gain: 2.5 })], catalogue);
  assert.deepEqual(plan, {
    kind: "set",
    sets: [
      { slot: 0, param: "db", value: -3 },
      { slot: 1, param: "gain", value: 2.5 },
    ],
  });
});

test("a param left out counts as its default", () => {
  assert.deepEqual(planRack([eq({ freq: 1000 })], [eq({})], catalogue), { kind: "same" });
  assert.deepEqual(planRack([eq({})], [eq({ gain: 3 })], catalogue), {
    kind: "set",
    sets: [{ slot: 0, param: "gain", value: 3 }],
  });
});

test("a choice by name equals the same choice by index", () => {
  assert.deepEqual(planRack([eq({ kind: "lowshelf" })], [eq({ kind: 1 })], catalogue), { kind: "same" });
});

test("structure rebuilds: an effect added, removed, reordered or switched, a choice, a fixed param", () => {
  const rebuild = { kind: "rebuild" };
  assert.deepEqual(planRack([gain(-6)], [gain(-6), gain(0)], catalogue), rebuild);
  assert.deepEqual(planRack([gain(-6), eq({})], [gain(-6)], catalogue), rebuild);
  assert.deepEqual(planRack([gain(-6), eq({})], [eq({}), gain(-6)], catalogue), rebuild);
  assert.deepEqual(planRack([gain(-6)], [gain(-6, false)], catalogue), rebuild);
  assert.deepEqual(planRack([eq({ kind: "peak" })], [eq({ kind: "highshelf" })], catalogue), rebuild);
  const drift = (seed) => ({ effect: "drift", on: true, params: { seed } });
  assert.deepEqual(planRack([drift(1)], [drift(2)], catalogue), rebuild);
  assert.deepEqual(planRack([{ effect: "nope", on: true, params: {} }], [{ effect: "nope", on: true, params: { x: 1 } }], catalogue), rebuild);
});
