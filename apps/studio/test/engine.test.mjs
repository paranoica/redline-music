// The worklet's engine on the real wasm core, driven block by block as an AudioWorklet does.
import assert from "node:assert/strict";
import { test } from "node:test";

import createRedlineDsp from "../src/engine/redline_dsp.js";
import { Engine } from "../src/engine/core.ts";

const RATE = 48000;
const BLOCK = 128;
const dsp = await createRedlineDsp();

function stereo(frames, f) {
  const x = new Float32Array(2 * frames);
  for (let n = 0; n < frames; n++) {
    const [l, r] = f(n);
    x[2 * n] = l;
    x[2 * n + 1] = r;
  }
  return x;
}

const dc = (frames, v) => stereo(frames, () => [v, v]);
const rack = (...effects) => JSON.stringify({ effects });

function setup({ frames = RATE, vocal, beat, original, racks = {} } = {}) {
  const posted = [];
  const engine = new Engine(dsp, RATE, (m) => posted.push(m));
  engine.handle({
    type: "stems",
    frames,
    vocal: vocal ?? dc(frames, 0),
    beat: beat ?? dc(frames, 0),
    original: original ?? dc(frames, 0),
  });
  for (const track of ["vocal", "beat", "master"]) {
    engine.handle({ type: "rack", track, version: 1, json: racks[track] ?? rack(), lanes: [] });
  }
  engine.handle({ type: "listen", gains: { result: 1, original: 0, reference: 0, render: 0 } });
  return { engine, posted };
}

// `frames` of output, rendered in 128-frame blocks
function run(engine, frames) {
  const out = new Float32Array(2 * frames);
  const l = new Float32Array(BLOCK);
  const r = new Float32Array(BLOCK);
  for (let at = 0; at < frames; at += BLOCK) {
    engine.render(l, r);
    for (let i = 0; i < BLOCK && at + i < frames; i++) {
      out[2 * (at + i)] = l[i];
      out[2 * (at + i) + 1] = r[i];
    }
  }
  return out;
}

const left = (x, i) => x[2 * i];

test("racks report their latency once they are in", () => {
  const { engine, posted } = setup({ racks: { vocal: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } }) } });
  run(engine, BLOCK);
  const acks = posted.filter((m) => m.type === "rack");
  assert.deepEqual(
    acks.map((m) => [m.track, m.version, m.latency]).sort(),
    [["beat", 1, 0], ["master", 1, 0], ["vocal", 1, 240]],
  );
});

test("paused, the engine is silent; played, it sums the vocal and the beat", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.25), beat: dc(RATE, 0.125) });
  assert.ok(run(engine, 1024).every((v) => v === 0));
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 4096);
  assert.ok(Math.abs(left(out, 4000) - 0.375) < 1e-6, `${left(out, 4000)}`);
  // it starts from silence with a fade, not a step
  assert.ok(Math.abs(out[0]) < 0.01);
});

test("the vocal and the beat line up after racks of different latency", () => {
  const vocal = stereo(RATE, (n) => (n === 1000 ? [0.5, 0.5] : [0, 0]));
  const beat = stereo(RATE, (n) => (n === 1000 ? [0.25, 0.25] : [0, 0]));
  const { engine } = setup({
    vocal,
    beat,
    racks: {
      vocal: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } }),
      beat: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 1 } }),
    },
  });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 4096);
  let peak = 0;
  for (let i = 0; i < 4096; i++) if (Math.abs(left(out, i)) > Math.abs(left(out, peak))) peak = i;
  assert.equal(peak, 1240, "both arrive with the vocal's 240 frames");
  assert.ok(Math.abs(left(out, 1240) - 0.75) < 1e-4, `${left(out, 1240)}`);
  const elsewhere = out.filter((_, i) => Math.floor(i / 2) !== 1240).reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  assert.ok(elsewhere < 1e-3, `the beat left a copy elsewhere: ${elsewhere}`);
});

test("a param set in place changes the sound without a rebuild", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5), racks: { vocal: rack({ effect: "gain", params: { db: -6 } }) } });
  engine.handle({ type: "play", playing: true });
  let out = run(engine, 2048);
  assert.ok(Math.abs(left(out, 2000) - 0.5 * 10 ** (-6 / 20)) < 1e-3);
  const before = posted.filter((m) => m.type === "rack").length;
  engine.handle({ type: "set", track: "vocal", version: 1, slot: 0, param: "db", value: 0 });
  out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.5) < 1e-3, `${left(out, 9000)}`);
  assert.equal(posted.filter((m) => m.type === "rack").length, before, "no rack was rebuilt");
  assert.equal(posted.filter((m) => m.type === "error").length, 0);
});

test("a set for a version that is gone is dropped, not applied to another rack", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5), racks: { vocal: rack({ effect: "gain", params: { db: -6 } }) } });
  engine.handle({ type: "play", playing: true });
  engine.handle({ type: "set", track: "vocal", version: 7, slot: 0, param: "db", value: 0 });
  const out = run(engine, 4096);
  assert.ok(Math.abs(left(out, 4000) - 0.5 * 10 ** (-6 / 20)) < 1e-3);
  assert.equal(posted.filter((m) => m.type === "error").length, 0);
});

test("a rebuilt rack takes over at a block boundary, smoothly, and is acknowledged", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5), racks: { vocal: rack({ effect: "gain", params: { db: -6 } }) } });
  engine.handle({ type: "play", playing: true });
  run(engine, 2048);
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "gain", params: { db: -6 } }, { effect: "gain", params: { db: -6 } }), lanes: [] });
  const out = run(engine, RATE / 2);
  const want = 0.5 * 10 ** (-12 / 20);
  assert.ok(Math.abs(left(out, 0) - 0.5 * 10 ** (-6 / 20)) < 1e-3, "no jump at the message");
  assert.ok(Math.abs(left(out, RATE / 2 - 10) - want) < 1e-3, `${left(out, RATE / 2 - 10)}`);
  let step = 0;
  for (let i = 1; i < RATE / 2; i++) step = Math.max(step, Math.abs(left(out, i) - left(out, i - 1)));
  assert.ok(step < 0.01, `the swap jumped by ${step}`);
  assert.ok(posted.some((m) => m.type === "rack" && m.track === "vocal" && m.version === 2 && m.latency === 0));
});

test("a rack that does not build is reported, and the old one keeps playing", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5) });
  engine.handle({ type: "play", playing: true });
  engine.handle({ type: "rack", track: "vocal", version: 2, json: "{nope", lanes: [] });
  const out = run(engine, 2048);
  assert.ok(Math.abs(left(out, 2000) - 0.5) < 1e-6);
  const error = posted.find((m) => m.type === "error");
  assert.ok(error && error.track === "vocal" && error.version === 2 && /rack/.test(error.message), JSON.stringify(error));
});

test("A/B crossfades: no step, the original after the fade", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5), original: dc(RATE, -0.5) });
  engine.handle({ type: "play", playing: true });
  run(engine, 2048);
  engine.handle({ type: "listen", gains: { result: 0, original: 1, reference: 0, render: 0 } });
  const out = run(engine, 4096);
  // the last sample before the switch was the result's 0.5
  let step = Math.abs(left(out, 0) - 0.5);
  for (let i = 1; i < 4096; i++) step = Math.max(step, Math.abs(left(out, i) - left(out, i - 1)));
  assert.ok(step < 0.005, `the switch jumped by ${step}`);
  assert.ok(Math.abs(left(out, 4000) + 0.5) < 1e-6, `${left(out, 4000)}`);
});

test("levels arrive about 30 times a second, with the position heard", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5), beat: dc(RATE, 0.25) });
  engine.handle({ type: "play", playing: true });
  run(engine, RATE / 2);
  const levels = posted.filter((m) => m.type === "levels");
  assert.ok(levels.length >= 13 && levels.length <= 16, `${levels.length} in half a second`);
  const last = levels.at(-1);
  assert.ok(Math.abs(last.vocal.peak - 0.5) < 1e-6 && Math.abs(last.beat.peak - 0.25) < 1e-6);
  assert.ok(Math.abs(last.master.peak - 0.75) < 1e-6);
  assert.ok(last.frame > RATE / 2 - 2000 && last.frame <= RATE / 2, `${last.frame}`);
  assert.equal(last.playing, true);
});

test("the master's gain reduction is the level it took off the peaks", () => {
  const { engine, posted } = setup({
    vocal: stereo(RATE, (n) => [0.5 * Math.sin(n / 10), 0.5 * Math.sin(n / 10)]),
    racks: { master: rack({ effect: "master", params: { gain: 12, trim: 0, ceiling: -1 } }) },
  });
  engine.handle({ type: "play", playing: true });
  run(engine, RATE / 2);
  const last = posted.filter((m) => m.type === "levels").at(-1);
  // in at -6 dBFS peak, +12 dB of gain, out at the -1 dBFS ceiling: about 7 dB taken off
  assert.ok(last.master.gr > 5 && last.master.gr < 9, `${last.master.gr}`);
});

test("the loop keeps the position inside it; without it the track ends", () => {
  const frames = 9600;
  const { engine, posted } = setup({ frames, vocal: dc(frames, 0.5) });
  engine.handle({ type: "loop", on: true, start: 1000, end: 4000 });
  engine.handle({ type: "seek", frame: 1000 });
  engine.handle({ type: "play", playing: true });
  run(engine, 20000);
  const frames2 = posted.filter((m) => m.type === "levels").map((m) => m.frame);
  assert.ok(frames2.every((f) => f >= 1000 && f < 4000), `${frames2}`);
  engine.handle({ type: "loop", on: false, start: 1000, end: 4000 });
  const out = run(engine, 20000);
  assert.ok(posted.some((m) => m.type === "ended"));
  assert.ok(out.subarray(2 * 18000).every((v) => v === 0), "silent after the end");
});

test("an automation lane moves its param over the track", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  engine.handle({
    type: "rack",
    track: "vocal",
    version: 2,
    json: rack({ effect: "gain", params: { db: 0 } }),
    lanes: [{ slot: 0, param: "db", points: [[0, -20], [0.5, -20], [0.5, 0]] }],
  });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, RATE);
  assert.ok(Math.abs(left(out, 20000) - 0.05) < 2e-3, `${left(out, 20000)}`);
  assert.ok(Math.abs(left(out, 47000) - 0.5) < 2e-3, `${left(out, 47000)}`);
});

test("a rebuilt rack of another latency takes over through silence and lines up again", () => {
  const { engine, posted } = setup({ vocal: dc(RATE, 0.5), beat: dc(RATE, 0.25) });
  engine.handle({ type: "play", playing: true });
  run(engine, 2048);
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } }), lanes: [] });
  const out = run(engine, RATE / 2);
  let step = Math.abs(left(out, 0) - 0.75);
  for (let i = 1; i < RATE / 2; i++) step = Math.max(step, Math.abs(left(out, i) - left(out, i - 1)));
  assert.ok(step < 0.01, `the swap jumped by ${step}`);
  assert.ok(posted.some((m) => m.type === "rack" && m.version === 2 && m.latency === 240));
  assert.equal(posted.filter((m) => m.type === "levels").at(-1).latency, 240);
  assert.ok(Math.abs(left(out, RATE / 2 - 10) - 0.75) < 1e-3, `${left(out, RATE / 2 - 10)}`);
});
