import assert from "node:assert/strict";
import { test } from "node:test";

import createRedlineDsp from "../src/engine/redline_dsp.js";
import { FAMILIES, addable, effectInfo } from "../src/core/families.ts";

const M = await createRedlineDsp();
const effects = JSON.parse(M.UTF8ToString(M._effects_json()));

test("every effect of the core has a family and a Russian name", () => {
  for (const e of effects) {
    const info = effectInfo(e.effect);
    assert.ok(FAMILIES.includes(info.family), `${e.effect}: ${info.family}`);
    assert.notEqual(info.family, "Прочее", `${e.effect} has no family`);
    assert.match(info.label, /[а-яё]/i, `${e.effect}: ${info.label}`);
  }
});

test("an effect the page does not know is shown under its own name", () => {
  assert.deepEqual(effectInfo("brand_new"), { family: "Прочее", label: "brand_new", internal: false });
});

test("the chain's own stages are not offered for the user's rack", () => {
  const names = addable(effects).map((e) => e.effect);
  for (const internal of ["chain_compressor", "chain_clip", "bass_drive", "master"]) {
    assert.ok(!names.includes(internal), internal);
  }
  assert.ok(names.includes("delay") && names.includes("gain"));
  assert.equal(names.length, effects.length - 4);
});
