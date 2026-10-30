// The wasm beat player against the native one: tests/redline/core/make_beat_fixture.py wrote
// windows of what redline_dsp.beat_render gives for each score. Needs
// apps/redline-dsp/wasm/build.sh run.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import createRedlineDsp from "../src/engine/redline_dsp.js";

const fixture = JSON.parse(
  readFileSync(new URL("./fixtures/beat-native.json", import.meta.url), "utf8"),
);
const RATE = fixture.rate;
const BLOCK = 128;
const M = await createRedlineDsp();

// Measured over the whole renders (8 s notes and their tails, 128-frame blocks): every voice
// family came out bit-identical to the native build, the racks case (saturate, limiter) off by
// at most 1.8e-7. The bounds leave a few float steps for another libm or toolchain.
const TOLERANCE = {
  bass808: 2.5e-7,
  saw: 2.5e-7,
  pluck: 2.5e-7,
  fm: 2.5e-7,
  drums: 2.5e-7,
  racks: 5e-7,
};

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

function newBeat(score, rate = RATE) {
  const json = typeof score === "string" ? score : JSON.stringify(score);
  return withText(json, (p, n) => M._beat_new(p, n, rate));
}

// the next `frames` frames of the mix, in blocks of 128 as an AudioWorklet asks for them
function play(handle, frames) {
  const ptr = M._malloc(2 * BLOCK * 4);
  const out = new Float32Array(2 * frames);
  try {
    for (let at = 0; at < frames; at += BLOCK) {
      const n = Math.min(BLOCK, frames - at);
      assert.equal(M._beat_process(handle, ptr, n), 0, error());
      out.set(M.HEAPF32.subarray(ptr / 4, ptr / 4 + 2 * n), 2 * at);
    }
  } finally {
    M._free(ptr);
  }
  return out;
}

function sameBits(a, b) {
  const x = new Uint32Array(a.buffer, a.byteOffset, a.length);
  const y = new Uint32Array(b.buffer, b.byteOffset, b.length);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

function maxAbs(x) {
  return x.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
}

const SCORE = {
  version: 1,
  bpm: 120,
  seed: 3,
  key: "A minor",
  sounds: { kick: { instrument: "kick" }, bass: { instrument: "bass808" } },
  patterns: {
    p: {
      bars: 1,
      rows: {
        kick: "x.......x.......",
        bass: [{ step: 0, len: 8, note: "A1", vel: 1.0 }],
      },
    },
  },
  sections: [{ name: "a", bars: 2, play: ["p"] }],
};

for (const [name, c] of Object.entries(fixture.cases)) {
  test(`the ${name} score matches the native render within ${TOLERANCE[name]}`, () => {
    const h = newBeat(c.score);
    assert.notEqual(h, 0, error());
    assert.equal(M._beat_latency(h), c.latency);
    assert.equal(M._beat_length(h), c.length);
    const last = Math.max(...c.windows) + fixture.window;
    const out = play(h, c.latency + last);
    assert.equal(M._beat_position(h), c.latency + last);
    M._beat_free(h);
    let worst = 0;
    c.windows.forEach((start, k) => {
      const native = decode(c.samples[k]);
      const from = 2 * (c.latency + start);
      for (let i = 0; i < native.length; i++) {
        worst = Math.max(worst, Math.abs(out[from + i] - native[i]));
      }
    });
    assert.ok(maxAbs(out) > 0.01, "the render is not silent");
    assert.ok(worst <= TOLERANCE[name], `max abs difference ${worst}`);
  });
}

test("seek plays on as the whole run does", () => {
  const score = fixture.cases.drums.score;
  const whole = newBeat(score);
  assert.notEqual(whole, 0, error());
  const want = play(whole, 60000);
  M._beat_free(whole);

  const h = newBeat(score);
  play(h, 5000);
  // inside the open hat struck on step 2 and the decay of the hit on step 4
  const at = 4 * 6000 + 123;
  assert.equal(M._beat_seek(h, at), 0, error());
  assert.equal(M._beat_position(h), at);
  const got = play(h, 20000);
  M._beat_free(h);
  assert.ok(maxAbs(got) > 0.01);
  assert.ok(sameBits(got, want.subarray(2 * at, 2 * (at + 20000))));

  const k = newBeat(score);
  assert.equal(M._beat_seek(k, -1), -1);
  assert.match(error(), /frame/);
  assert.equal(M._beat_seek(k, 1.5), -1);
  assert.equal(M._beat_seek(k, 2 ** 60), -1);
  M._beat_free(k);
});

test("a freed or unknown handle is refused", () => {
  const h = newBeat(SCORE);
  assert.notEqual(h, 0, error());
  M._beat_free(h);
  M._beat_free(h);
  const ptr = M._malloc(2 * BLOCK * 4);
  assert.equal(M._beat_process(h, ptr, BLOCK), -1);
  assert.match(error(), /no beat has the handle/);
  assert.equal(M._beat_latency(h), -1);
  assert.equal(M._beat_seek(h, 0), -1);
  assert.equal(M._beat_rebuild_samplers(h), -1);
  assert.equal(M._beat_process(0, ptr, BLOCK), -1);
  assert.equal(M._beat_process(12345, ptr, BLOCK), -1);
  // a new player in the freed slot has a handle the old one does not match
  const again = newBeat(SCORE);
  assert.notEqual(again, h);
  assert.equal(M._beat_process(h, ptr, BLOCK), -1);
  assert.equal(M._beat_process(again, ptr, BLOCK), 0, error());
  // a neighbouring slot holds no player
  assert.equal(M._beat_process(again + 1, ptr, BLOCK), -1);
  M._beat_free(again);
  M._free(ptr);
});

test("a broken score is refused with every error and its path", () => {
  const broken = structuredClone(SCORE);
  broken.patterns.p.rows.kick = "x.......";
  broken.bpm = 400;
  assert.equal(newBeat(broken), 0);
  const lines = error().split("\n");
  assert.ok(lines.some((l) => l.startsWith("bpm: ")), error());
  assert.ok(lines.some((l) => l.startsWith("patterns.p.rows.kick: ")), error());
  assert.equal(newBeat("{"), 0);
  assert.match(error(), /not JSON/);
  assert.equal(newBeat(SCORE, 0), 0);
  assert.match(error(), /rate/);

  const check = (text) =>
    JSON.parse(M.UTF8ToString(withText(text, (p, n) => M._beat_check_json(p, n))));
  assert.deepEqual(check(JSON.stringify(SCORE)), []);
  const errors = check(JSON.stringify(broken));
  assert.equal(errors.length, 2);
  assert.ok(errors.some((e) => e.startsWith("patterns.p.rows.kick: ")));
  // quotes and newlines in a message survive the JSON
  assert.equal(check('{"bpm": "1\\n\\"').length, 1);
});

test("the event table and the instruments are the core's JSON", () => {
  const events = (score, rate = RATE) =>
    withText(JSON.stringify(score), (p, n) => M._beat_events_json(p, n, rate));
  const table = JSON.parse(M.UTF8ToString(events(SCORE)));
  assert.equal(table.length, 2 * 16 * 6000);
  const kicks = table.events.filter((e) => e.sound === 1).map((e) => e.start);
  assert.deepEqual(kicks, [0, 48000, 96000, 144000]);
  assert.equal(events({ ...SCORE, sections: [] }), 0);
  assert.match(error(), /sections/);
  assert.equal(events(SCORE, -1), 0);
  assert.match(error(), /rate/);

  const instruments = JSON.parse(M.UTF8ToString(M._beat_instruments_json()));
  const bass = instruments.find((i) => i.instrument === "bass808");
  assert.equal(bass.mono, true);
  assert.ok(bass.params.some((p) => p.name === "glide_ms"));
});

test("a new score with the same sounds is taken; other sounds need a rebuild", () => {
  const h = newBeat(SCORE);
  play(h, 1000);
  const louder = structuredClone(SCORE);
  louder.mix = { sounds: { kick: { gain_db: 3 } } };
  assert.equal(withText(JSON.stringify(louder), (p, n) => M._beat_set_score(h, p, n)), 0, error());
  const other = structuredClone(SCORE);
  other.sounds.kick.instrument = "snare";
  assert.equal(withText(JSON.stringify(other), (p, n) => M._beat_set_score(h, p, n)), -1);
  assert.match(error(), /^rebuild/);
  assert.equal(withText("{", (p, n) => M._beat_set_score(h, p, n)), -1);
  assert.match(error(), /not JSON/);
  M._beat_free(h);
});

test("a rack swaps in at the player's rate; a late, offline or unknown one is refused", () => {
  const h = newBeat(SCORE);
  const setRack = (sound, rack) =>
    withText(JSON.stringify(rack), (p, n) => M._beat_set_rack(h, sound, p, n));
  play(h, 1000);
  // the score's own form, a list of effects, and the rack's, an object with them
  assert.equal(setRack(0, [{ effect: "gain", params: { db: -6 } }]), 0, error());
  assert.equal(setRack(0, { effects: [{ effect: "gain", params: { db: -3 } }] }), 0, error());
  assert.ok(maxAbs(play(h, 4096)) > 0.01);
  assert.equal(setRack(0, [{ effect: "limiter" }]), -1);
  assert.match(error(), /^rebuild/);
  assert.equal(setRack(5, []), -1);
  assert.match(error(), /no sound 5/);
  assert.equal(setRack(0, [{ effect: "nope" }]), -1);
  assert.match(error(), /no effect nope/);
  assert.equal(setRack(0, Array(5).fill({ effect: "gain" })), -1);
  assert.match(error(), /at most 4/);
  M._beat_free(h);
});

test("a loaded zone plays the sampler; past the budget it is refused", () => {
  const score = {
    ...SCORE,
    sounds: { keys: { instrument: "sampler", library: "sine" } },
    patterns: { p: { bars: 1, rows: { keys: [{ step: 0, len: 8, note: "C5", vel: 1.0 }] } } },
  };
  const h = newBeat(score);
  assert.notEqual(h, 0, error());
  const frames = RATE;
  const ptr = M._malloc(2 * frames);
  const sine = new Int16Array(frames);
  for (let i = 0; i < frames; i++) sine[i] = Math.round(16383 * Math.sin((2 * Math.PI * 440 * i) / RATE));
  M.HEAPU8.set(new Uint8Array(sine.buffer), ptr);
  const load = (name, root, lo, hi, n, channels) =>
    withText(name, (p, len) => M._beat_load_zone(h, p, len, root, lo, hi, RATE, ptr, n, channels));
  assert.equal(load("sine", 60, 0, 127, frames, 1), 0, error());
  assert.equal(M._beat_rebuild_samplers(h), 0, error());
  const out = play(h, 24000);
  // an octave up: 880 Hz, so about 2 * 880 / RATE sign changes a frame
  let crossings = 0;
  for (let i = 4800; i < 24000; i++) if ((out[2 * i] >= 0) !== (out[2 * i - 2] >= 0)) crossings++;
  const hz = (crossings / 2) * (RATE / (24000 - 4800));
  assert.ok(Math.abs(hz - 880) < 10, `${hz} Hz`);

  assert.equal(load("sine", 200, 0, 127, frames, 1), -1);
  assert.match(error(), /127/);
  assert.equal(load("sine", 60, 0, 127, frames, 3), -1);
  assert.match(error(), /channels/);
  // as many frames as the budget, without the memory: refused before anything is read
  assert.equal(load("sine", 60, 0, 127, 64 * 1024 * 1024, 1), -1);
  assert.match(error(), /budget/);
  M._free(ptr);
  M._beat_free(h);
});
