// The wasm rack against the native one: tests/redline/core/make_wasm_fixture.py wrote what
// redline_dsp.rack_process gives for the same input. Needs apps/redline-dsp/wasm/build.sh run.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import createRedlineDsp from "../src/engine/redline_dsp.js";

const fixture = JSON.parse(
  readFileSync(new URL("./fixtures/wasm-native.json", import.meta.url), "utf8"),
);
const RATE = fixture.rate;
const BLOCK = 128;
const M = await createRedlineDsp();

// as the fixture's script: 375 Hz at half scale on the left, 625 Hz at a quarter on the right
function sines(frames) {
  const x = new Float32Array(2 * frames);
  for (let n = 0; n < frames; n++) {
    const t = n / RATE;
    x[2 * n] = 0.5 * Math.sin(2 * Math.PI * 375 * t);
    x[2 * n + 1] = 0.25 * Math.sin(2 * Math.PI * 625 * t);
  }
  return x;
}

function decode(text) {
  const bytes = Buffer.from(text, "base64");
  const copy = new Uint8Array(bytes.length);
  copy.set(bytes);
  return new Float32Array(copy.buffer);
}

function error() {
  return M.UTF8ToString(M._rack_error());
}

function withText(text, f) {
  const bytes = Buffer.from(text, "utf8");
  const p = M._malloc(Math.max(bytes.length, 1));
  M.HEAPU8.set(bytes, p);
  try {
    return f(p, bytes.length);
  } finally {
    M._free(p);
  }
}

function newRack(rack, rate = RATE) {
  const json = typeof rack === "string" ? rack : JSON.stringify(rack);
  return withText(json, (p, n) => M._rack_new(rate, p, n));
}

// `frames` frames of output, in blocks of 128 as an AudioWorklet hands them over; the input
// is silent past its end
function run(handle, input, frames) {
  const inPtr = M._malloc(2 * BLOCK * 4);
  const outPtr = M._malloc(2 * BLOCK * 4);
  const out = new Float32Array(2 * frames);
  try {
    for (let at = 0; at < frames; at += BLOCK) {
      const n = Math.min(BLOCK, frames - at);
      const block = new Float32Array(2 * BLOCK);
      block.set(input.subarray(Math.min(2 * at, input.length), Math.min(2 * (at + n), input.length)));
      M.HEAPF32.set(block, inPtr / 4);
      assert.equal(M._rack_process(handle, inPtr, 0, outPtr, n), 0, error());
      out.set(M.HEAPF32.subarray(outPtr / 4, outPtr / 4 + 2 * n), 2 * at);
    }
  } finally {
    M._free(inPtr);
    M._free(outPtr);
  }
  return out;
}

function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      [re[i], re[j]] = [re[j], re[i]];
      [im[i], im[j]] = [im[j], im[i]];
    }
  }
  for (let size = 2; size <= n; size *= 2) {
    const step = (-2 * Math.PI) / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0; k < size / 2; k++) {
        const wr = Math.cos(step * k);
        const wi = Math.sin(step * k);
        const a = start + k;
        const b = a + size / 2;
        const tr = re[b] * wr - im[b] * wi;
        const ti = re[b] * wi + im[b] * wr;
        re[b] = re[a] - tr;
        im[b] = im[a] - ti;
        re[a] += tr;
        im[a] += ti;
      }
    }
  }
}

// the strongest frequency, Hann-windowed, between bins by a parabola through the log magnitudes
function peakHz(x) {
  const n = x.length;
  const re = new Float64Array(n);
  const im = new Float64Array(n);
  for (let i = 0; i < n; i++) re[i] = x[i] * (0.5 - 0.5 * Math.cos((2 * Math.PI * i) / n));
  fft(re, im);
  const mag = (k) => Math.log(Math.hypot(re[k], im[k]) + 1e-20);
  let best = 1;
  for (let k = 2; k < n / 2; k++) if (mag(k) > mag(best)) best = k;
  const [a, b, c] = [mag(best - 1), mag(best), mag(best + 1)];
  return ((best + (0.5 * (a - c)) / (a - 2 * b + c)) * RATE) / n;
}

function rms(x) {
  return Math.sqrt(x.reduce((s, v) => s + v * v, 0) / x.length);
}

test("the gain rack matches the native one within 1e-4", () => {
  const { rack, latency, frames, samples } = fixture.cases.gain;
  const native = decode(samples);
  const h = newRack(rack);
  assert.notEqual(h, 0, error());
  assert.equal(M._rack_latency(h), latency);
  const input = sines(fixture.frames);
  const out = run(h, input, frames[1]);
  M._rack_free(h);
  assert.equal(native.length, out.length);
  const scale = 10 ** (-6 / 20);
  let worst = 0;
  let worstSpec = 0;
  for (let i = 0; i < out.length; i++) {
    worst = Math.max(worst, Math.abs(out[i] - native[i]));
    worstSpec = Math.max(worstSpec, Math.abs(out[i] - input[i] * scale));
  }
  assert.ok(worst <= 1e-4, `max abs difference ${worst}`);
  assert.ok(worstSpec <= 1e-4, `off -6 dB of the input by ${worstSpec}`);
});

test("the pitch rack peaks within 1 % of the native one, an octave up", () => {
  const { rack, latency, frames, samples } = fixture.cases.pitch;
  const native = decode(samples);
  const h = newRack(rack);
  assert.notEqual(h, 0, error());
  assert.equal(M._rack_latency(h), latency, "the latency is the native rack's");
  const out = run(h, sines(fixture.frames), frames[1] + latency);
  M._rack_free(h);
  // the left channel over the native window, shifted by the latency as the native render is
  const left = new Float32Array(frames[1] - frames[0]);
  for (let i = 0; i < left.length; i++) left[i] = out[2 * (frames[0] + latency + i)];
  const want = peakHz(native);
  const got = peakHz(left);
  assert.ok(Math.abs(want - 750) <= 7.5, `the native peak is ${want} Hz, not 750`);
  assert.ok(Math.abs(got - want) <= 0.01 * want, `${got} Hz against the native ${want} Hz`);
  const db = 20 * Math.log10(rms(left) / rms(native));
  assert.ok(Math.abs(db) <= 1, `the level is ${db.toFixed(2)} dB off the native one`);
});

test("a wrong rack, param or handle is refused with a reason", () => {
  assert.equal(newRack("{not json"), 0);
  assert.match(error(), /rack:/);
  assert.equal(newRack({ effects: [{ effect: "nope" }] }), 0);
  assert.match(error(), /no effect nope/);
  assert.equal(newRack(fixture.cases.gain.rack, 1000), 0);
  assert.match(error(), /rate/);

  const h = newRack(fixture.cases.gain.rack);
  assert.equal(withText("x", (p, n) => M._rack_set(h, 0, p, n, 0)), -1);
  assert.match(error(), /gain has no param x/);
  assert.equal(withText("db", (p, n) => M._rack_set(h, 0, p, n, 99)), -1);
  assert.equal(withText("db", (p, n) => M._rack_set(h, 0, p, n, -3)), 0, error());
  M._rack_free(h);
  assert.equal(M._rack_latency(h), -1);
  assert.match(error(), /no rack/);
});

test("the effects list is the core's JSON", () => {
  const effects = JSON.parse(M.UTF8ToString(M._effects_json()));
  const names = effects.map((e) => e.effect);
  assert.ok(names.includes("gain") && names.includes("pitch"));
  assert.ok(effects.every((e) => Array.isArray(e.params)));
});
