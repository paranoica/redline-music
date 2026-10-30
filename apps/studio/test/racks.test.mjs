import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { buildLanes, buildRacks, gains, rackJson } from "../src/core/racks.ts";

// what redline.engine.live gives for the same settings: make_racks_fixture.py
const fixture = JSON.parse(readFileSync(new URL("./fixtures/racks-python.json", import.meta.url), "utf8"));

function same(got, want, path) {
  if (typeof want === "number") {
    assert.equal(typeof got, "number", `${path}: ${JSON.stringify(got)}`);
    assert.ok(Math.abs(got - want) <= 1e-9 * Math.max(1, Math.abs(want)), `${path}: ${got} against ${want}`);
  } else if (Array.isArray(want)) {
    assert.ok(Array.isArray(got), `${path} is not a list`);
    assert.equal(got.length, want.length, `${path}: ${got.length} items against ${want.length}`);
    want.forEach((w, i) => same(got[i], w, `${path}[${i}]`));
  } else if (want && typeof want === "object") {
    assert.deepEqual(Object.keys(got).sort(), Object.keys(want).sort(), `${path} keys`);
    for (const k of Object.keys(want)) same(got[k], want[k], `${path}.${k}`);
  } else {
    assert.equal(got, want, path);
  }
}

for (const [name, c] of Object.entries(fixture.cases)) {
  test(`the racks match the Python engine's: ${name}`, () => {
    const racks = buildRacks(c.settings, fixture.numbers);
    for (const track of ["vocal", "beat", "master"]) {
      same({ effects: racks[track] }, c.racks[track], `${name}.${track}`);
      same(JSON.parse(rackJson(racks[track])), c.racks[track], `${name}.${track} json`);
    }
    same(buildLanes(c.settings, fixture.numbers), c.lanes, `${name}.lanes`);
  });
}

test("a gain past the gain effect's range is split; 0 dB is no effect", () => {
  const db = (slots) => slots.map((s) => s.params.db);
  assert.deepEqual(db(gains(30)), [24, 6]);
  assert.deepEqual(db(gains(-70)), [-60, -10]);
  assert.deepEqual(db(gains(48)), [24, 24]);
  assert.deepEqual(gains(0), []);
});

test("a number the settings need and the analysis lacks is refused by name", () => {
  const c = fixture.cases.defaults;
  assert.throws(() => buildRacks(c.settings, { ...fixture.numbers, makeup_db: null }), /makeup_db/);
  // with the master off its gain is not needed
  const off = fixture.cases["master off"];
  assert.doesNotThrow(() => buildRacks(off.settings, { ...fixture.numbers, master_gain_db: null }));
});

test("more than 32 effects in the vocal rack is refused", () => {
  const c = fixture.cases.defaults;
  const rack = Array.from({ length: 25 }, () => ({ effect: "gain", on: true, params: {}, automation: [] }));
  assert.throws(() => buildRacks({ ...c.settings, rack }, fixture.numbers), /32/);
});
