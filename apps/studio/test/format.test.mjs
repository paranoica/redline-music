import assert from "node:assert/strict";
import { test } from "node:test";

import { clock, formatValue } from "../src/core/format.ts";

const NB = " ";

test("values carry their unit in Russian, with a real minus sign", () => {
  assert.equal(formatValue(-6, "дБ", 0.5), `−6.0${NB}дБ`);
  assert.equal(formatValue(-6, "dB", 0.5), `−6.0${NB}дБ`);
  assert.equal(formatValue(120, "Гц", 5), `120${NB}Гц`);
  assert.equal(formatValue(4000, "Hz", 100), `4.0${NB}кГц`);
  assert.equal(formatValue(12000, "Hz"), `12.0${NB}кГц`);
  assert.equal(formatValue(40, "ms", 5), `40${NB}мс`);
  assert.equal(formatValue(1.8, "с", 0.1), `1.8${NB}с`);
  assert.equal(formatValue(-9, "LUFS", 0.1), `−9.0${NB}LUFS`);
  assert.equal(formatValue(60, "%", 5), `60${NB}%`);
});

test("ratios and factors are glued to their number", () => {
  assert.equal(formatValue(30, ":1", 0.5), "30.0:1");
  assert.equal(formatValue(1.25, "×", 0.01), "1.25×");
  assert.equal(formatValue(2, "x"), "2.00×");
});

test("zero is not negative; a bare number gets as many decimals as its size needs", () => {
  assert.equal(formatValue(-0, "дБ", 0.5), `0.0${NB}дБ`);
  assert.equal(formatValue(0.35, ""), "0.35");
  assert.equal(formatValue(375, ""), "375");
  assert.equal(formatValue(12.5, ""), "12.5");
});

test("the clock shows minutes, seconds and tenths, carrying 59.96 s to a full minute", () => {
  assert.equal(clock(0), "00:00.0");
  assert.equal(clock(65.34), "01:05.3");
  assert.equal(clock(59.96), "01:00.0");
  assert.equal(clock(-3), "00:00.0");
});
