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

test("a lane taken away gives its param back to the slot's own value at once", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  const lanes = [{ slot: 0, param: "db", points: [[0, -20]] }];
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "gain", params: { db: 0 } }), lanes });
  engine.handle({ type: "play", playing: true });
  let out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.05) < 2e-3, `${left(out, 9000)}`);
  engine.handle({ type: "lanes", version: 2, lanes: [] });
  out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.5) < 2e-3, `${left(out, 9000)}`);
});

test("a lane taken away goes back to the value last set in place; while it is there it rules", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  const lanes = [{ slot: 0, param: "db", points: [[0, -20]] }];
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "gain", params: { db: 0 } }), lanes });
  engine.handle({ type: "play", playing: true });
  run(engine, 2048);
  engine.handle({ type: "set", track: "vocal", version: 2, slot: 0, param: "db", value: -6 });
  let out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.05) < 2e-3, `the set overrode the lane: ${left(out, 9000)}`);
  engine.handle({ type: "lanes", version: 2, lanes: [] });
  out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.5 * 10 ** (-6 / 20)) < 2e-3, `${left(out, 9000)}`);
});

test("a lane on a param the rack's JSON leaves out goes back to the effect's default", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  const lanes = [{ slot: 0, param: "db", points: [[0, -20]] }];
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "gain" }), lanes });
  engine.handle({ type: "play", playing: true });
  run(engine, 4096);
  engine.handle({ type: "lanes", version: 2, lanes: [] });
  const out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.5) < 2e-3, `${left(out, 9000)}`);
});

test("a lane that stays keeps its param; only the one taken away is given back", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  const both = [
    { slot: 0, param: "db", points: [[0, -20]] },
    { slot: 1, param: "db", points: [[0, -6]] },
  ];
  const json = rack({ effect: "gain", params: { db: 0 } }, { effect: "gain", params: { db: 0 } });
  engine.handle({ type: "rack", track: "vocal", version: 2, json, lanes: both });
  engine.handle({ type: "play", playing: true });
  run(engine, 4096);
  engine.handle({ type: "lanes", version: 2, lanes: [both[1]] });
  const out = run(engine, 9600);
  assert.ok(Math.abs(left(out, 9000) - 0.5 * 10 ** (-6 / 20)) < 2e-3, `${left(out, 9000)}`);
});

test("moving lanes play without allocating on the wasm heap", () => {
  let mallocs = 0;
  const counted = new Proxy(dsp, {
    get(target, key) {
      if (key === "_malloc") return (n) => (mallocs++, target._malloc(n));
      return target[key];
    },
  });
  const engine = new Engine(counted, RATE, () => {});
  engine.handle({ type: "stems", frames: RATE, vocal: dc(RATE, 0.5), beat: dc(RATE, 0), original: dc(RATE, 0) });
  const lanes = [{ slot: 0, param: "db", points: [[0, -20], [1, 0]] }];
  for (const track of ["vocal", "beat", "master"]) {
    const json = track === "vocal" ? rack({ effect: "gain", params: { db: 0 } }) : rack();
    engine.handle({ type: "rack", track, version: 1, json, lanes: track === "vocal" ? lanes : [] });
  }
  engine.handle({ type: "play", playing: true });
  run(engine, 9600);
  mallocs = 0;
  const out = run(engine, 4096);
  assert.ok(left(out, 4000) > left(out, 100) + 0.005, "the lane moved the gain");
  assert.equal(mallocs, 0);
});

// silent for the first half, loud for the second
const lateHalf = (frames, v) => stereo(frames, (n) => (n < frames / 2 ? [0, 0] : [v, v]));

function playToEnd(engine, posted, most = 40000) {
  const out = [];
  for (let at = 0; at < most && !posted.some((m) => m.type === "ended"); at += BLOCK) out.push(...run(engine, BLOCK));
  return Float32Array.from(out);
}

test("played to its end through a rack with latency, the track is heard to its last frame", () => {
  const frames = 9600;
  const { engine, posted } = setup({
    frames,
    vocal: lateHalf(frames, 0.5),
    racks: { vocal: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } }) },
  });
  engine.handle({ type: "play", playing: true });
  const out = playToEnd(engine, posted);
  assert.ok(posted.some((m) => m.type === "ended"));
  // the last frame of the track comes out 240 frames late
  assert.ok(Math.abs(left(out, frames + 239) - 0.5) < 1e-3, `${left(out, frames + 239)}`);
});

test("played again after its end, the track starts from its start, not with the old tail", () => {
  const frames = 9600;
  const { engine, posted } = setup({
    frames,
    vocal: lateHalf(frames, 0.5),
    beat: lateHalf(frames, 0.25),
    racks: { vocal: rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } }) },
  });
  engine.handle({ type: "play", playing: true });
  playToEnd(engine, posted);
  assert.ok(posted.some((m) => m.type === "ended"));
  engine.handle({ type: "play", playing: true });
  const again = run(engine, 4096);
  const loudest = again.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  assert.ok(loudest < 1e-6, `the first frames carry ${loudest}`);
  assert.ok(posted.filter((m) => m.type === "levels").at(-1).frame < 4096);
});

test("a reverb's tail from the end does not ring into the next play", () => {
  const frames = 9600;
  const { engine, posted } = setup({
    frames,
    vocal: lateHalf(frames, 0.5),
    racks: { vocal: rack({ effect: "hall", params: { mix: 1, decay: 10 } }) },
  });
  engine.handle({ type: "play", playing: true });
  const out = playToEnd(engine, posted);
  // the tail rings past the end and fades out, not cut off
  let last = out.length / 2 - 1;
  while (last > 0 && Math.abs(left(out, last)) < 1e-6) last--;
  let before = 0;
  for (let i = last - 2400; i < last - 1600; i++) before = Math.max(before, Math.abs(left(out, i)));
  assert.ok(before > 0.05, `${before}`);
  assert.ok(Math.abs(left(out, last)) < 0.01, `cut off at ${left(out, last)}`);
  engine.handle({ type: "play", playing: true });
  const again = run(engine, 2048);
  const loudest = again.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  assert.ok(loudest < 1e-6, `the tail rang on at ${loudest}`);
});

function quietest(out) {
  let m = Infinity;
  for (let i = 0; i < out.length / 2; i++) m = Math.min(m, Math.abs(left(out, i)));
  return m;
}

test("a rebuilt vocal rack warms up on the beat its rack put out, as the live one hears it", () => {
  // the beat's rack takes it down to -66 dBFS, under the duck's threshold; the raw beat is over it
  const { engine } = setup({
    vocal: dc(RATE, 0.5),
    beat: dc(RATE, 0.5),
    racks: { vocal: rack({ effect: "duck" }), beat: rack({ effect: "gain", params: { db: -60 } }) },
  });
  engine.handle({ type: "play", playing: true });
  run(engine, 9600);
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "duck", params: { release: 500 } }), lanes: [] });
  const out = run(engine, RATE / 2);
  assert.ok(quietest(out) > 0.45, `the new rack ducked the vocal to ${quietest(out)}`);
});

test("a rebuilt master rack warms up on the mix the racks put out, as the live one hears it", () => {
  // the mix after the racks is at -40 dBFS, under the limiter's ceiling; the raw stems at 0 dBFS
  const limiter = (release) => rack({ effect: "limiter", params: { ceiling: -20, lookahead: 5, release } });
  const { engine } = setup({
    vocal: dc(RATE, 0.5),
    beat: dc(RATE, 0.5),
    racks: {
      vocal: rack({ effect: "gain", params: { db: -40 } }),
      beat: rack({ effect: "gain", params: { db: -40 } }),
      master: limiter(50),
    },
  });
  engine.handle({ type: "play", playing: true });
  run(engine, 9600);
  engine.handle({ type: "rack", track: "master", version: 2, json: limiter(500), lanes: [] });
  const out = run(engine, RATE / 2);
  assert.ok(quietest(out) > 0.009, `the new rack limited the mix to ${quietest(out)}`);
});

const peak = (x, from = 0, to = x.length / 2) => {
  let m = 0;
  for (let i = from; i < to; i++) m = Math.max(m, Math.abs(left(x, i)));
  return m;
};
const gainRack = rack({ effect: "gain", params: { db: 0 } });
// 0.5 at -40 dB
const AT_LANE = 0.5 * 10 ** (-40 / 20);

test("a lane rules its param from the first frame of a play, without a glide from the slot's value", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  engine.handle({ type: "rack", track: "vocal", version: 2, json: gainRack, lanes: [{ slot: 0, param: "db", points: [[0, -40]] }] });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 8192);
  assert.ok(peak(out) < AT_LANE * 1.02, `${peak(out)}`);
  assert.ok(Math.abs(left(out, 8000) - AT_LANE) < 1e-4, `${left(out, 8000)}`);
});

test("played again after its end, a lane's param starts at its value at the start, not at the end", () => {
  const frames = 9600;
  const { engine, posted } = setup({ frames, vocal: dc(frames, 0.5) });
  const lanes = [{ slot: 0, param: "db", points: [[0, -40], [0.1, -40], [0.1, 0]] }];
  engine.handle({ type: "rack", track: "vocal", version: 2, json: gainRack, lanes });
  engine.handle({ type: "play", playing: true });
  playToEnd(engine, posted);
  assert.ok(posted.some((m) => m.type === "ended"));
  engine.handle({ type: "play", playing: true });
  const again = run(engine, 4096);
  assert.ok(peak(again) < AT_LANE * 1.02, `${peak(again)}`);
});

test("after a seek a lane's param starts at its value at the new spot", () => {
  const { engine } = setup({ vocal: dc(RATE, 0.5) });
  const lanes = [{ slot: 0, param: "db", points: [[0, 0], [0.5, 0], [0.5, -40]] }];
  engine.handle({ type: "rack", track: "vocal", version: 2, json: gainRack, lanes });
  engine.handle({ type: "play", playing: true });
  assert.ok(left(run(engine, 4096), 4000) > 0.49);
  engine.handle({ type: "play", playing: false });
  run(engine, 2048);
  engine.handle({ type: "seek", frame: 0.75 * RATE });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 4096);
  assert.ok(peak(out) < AT_LANE * 1.02, `${peak(out)}`);
});

// a loud sine over [from, to) seconds, silence elsewhere
const burst = (frames, from, to) =>
  stereo(frames, (n) => (n >= from * RATE && n < to * RATE ? [0.5 * Math.sin(n * 0.05), 0.5 * Math.sin(n * 0.05)] : [0, 0]));
const plate = rack({ effect: "plate", params: { mix: 1, decay: 0.9 } });

test("a seek leaves the reverb's tail of the old spot behind", () => {
  const frames = 6 * RATE;
  const { engine } = setup({ frames, vocal: burst(frames, 0, 1), racks: { vocal: plate } });
  engine.handle({ type: "play", playing: true });
  assert.ok(peak(run(engine, RATE)) > 0.1);
  engine.handle({ type: "play", playing: false });
  run(engine, 2048);
  engine.handle({ type: "seek", frame: 3 * RATE });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, RATE / 2);
  assert.ok(peak(out) < 1e-5, `the old tail rang at ${peak(out)}`);
});

test("after a seek the reverb rings with what the track has just before the new spot", () => {
  const frames = 2 * RATE;
  const through = setup({ frames, vocal: burst(frames, 0.9, 1), racks: { vocal: plate } });
  through.engine.handle({ type: "play", playing: true });
  const whole = run(through.engine, 1.2 * RATE);
  const heard = peak(whole, RATE + 1000, 1.2 * RATE);
  assert.ok(heard > 0.02, `${heard}`);
  const sought = setup({ frames, vocal: burst(frames, 0.9, 1), racks: { vocal: plate } });
  sought.engine.handle({ type: "seek", frame: RATE });
  sought.engine.handle({ type: "play", playing: true });
  const out = run(sought.engine, 0.2 * RATE);
  assert.ok(peak(out, 1000) > 0.5 * heard, `${peak(out, 1000)} against ${heard} played through`);
});

test("a rack rebuilt after a seek, before the play, does not let the frames before the spot be heard", () => {
  const frames = 2 * RATE;
  const { engine, posted } = setup({ frames, vocal: stereo(frames, (n) => (n < RATE ? [0.5, 0.5] : [0, 0])) });
  engine.handle({ type: "seek", frame: RATE });
  engine.handle({ type: "rack", track: "vocal", version: 2, json: gainRack, lanes: [] });
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 4096);
  assert.ok(peak(out) < 1e-6, `${peak(out)}`);
  assert.ok(posted.filter((m) => m.type === "levels").every((m) => m.frame >= RATE), "the position went back");
});

test("a pause and a play on the same spot keep the reverb's tail", () => {
  const frames = 2 * RATE;
  const { engine } = setup({ frames, vocal: burst(frames, 0, 0.5), racks: { vocal: plate } });
  engine.handle({ type: "play", playing: true });
  run(engine, 0.55 * RATE);
  engine.handle({ type: "play", playing: false });
  run(engine, 2048);
  engine.handle({ type: "play", playing: true });
  const out = run(engine, 0.1 * RATE);
  assert.ok(peak(out) > 0.02, `${peak(out)}`);
});

for (const [when, before] of [["after the end was reached", 0], ["just before the end is reached", BLOCK]]) {
  test(`a seek ${when}, in the fade of the play-out, wins over the end`, () => {
    const frames = 9600;
    const { engine, posted } = setup({ frames, vocal: dc(frames, 0.5) });
    engine.handle({ type: "play", playing: true });
    run(engine, frames - before);
    engine.handle({ type: "seek", frame: 2000 });
    const out = run(engine, 4096);
    assert.ok(!posted.some((m) => m.type === "ended"), "ended was posted");
    assert.ok(Math.abs(left(out, 4000) - 0.5) < 1e-6, `${left(out, 4000)}`);
    const frame = posted.filter((m) => m.type === "levels").at(-1).frame;
    assert.ok(frame >= 2000 && frame < 2000 + 4096, `${frame}`);
  });
}

test("a forward seek while a rebuilt rack warms up does not warm it through the gap", () => {
  const frames = 20 * RATE;
  const { engine, posted } = setup({ frames, vocal: dc(frames, 0.5) });
  engine.handle({ type: "play", playing: true });
  run(engine, 2048);
  engine.handle({ type: "rack", track: "vocal", version: 2, json: rack({ effect: "gain", params: { db: -6 } }), lanes: [] });
  engine.handle({ type: "seek", frame: 15 * RATE });
  const out = run(engine, RATE / 4);
  assert.ok(posted.some((m) => m.type === "rack" && m.track === "vocal" && m.version === 2), "the new rack is not in yet");
  assert.ok(Math.abs(left(out, RATE / 4 - 1) - 0.5 * 10 ** (-6 / 20)) < 1e-3, `${left(out, RATE / 4 - 1)}`);
});

// each frame says which frame of the track it is: the left channel over the right
const indexed = (frames) => stereo(frames, (n) => [(0.5 * n) / frames, 0.5]);

// the track frame of the first frame heard; with `afterQuiet`, the first after a silence
function firstHeard(engine, frames, afterQuiet = false) {
  const l = new Float32Array(BLOCK);
  const r = new Float32Array(BLOCK);
  let quiet = !afterQuiet;
  for (let at = 0; at < 4 * RATE; at += BLOCK) {
    engine.render(l, r);
    for (let i = 0; i < BLOCK; i++) {
      if (Math.abs(r[i]) <= 1e-4) quiet = true;
      else if (quiet) return Math.round((l[i] / r[i]) * frames);
    }
  }
  return null;
}

test("with the loop on, a seek outside it plays from the loop's start; one inside stays where it is", () => {
  const frames = 4 * RATE;
  for (const [to, want] of [[30000, 10000], [5000, 10000], [20000, 10000], [10000, 10000], [15000, 15000]]) {
    const { engine, posted } = setup({ frames, vocal: indexed(frames) });
    engine.handle({ type: "loop", on: true, start: 10000, end: 20000 });
    engine.handle({ type: "seek", frame: to });
    engine.handle({ type: "play", playing: true });
    assert.equal(firstHeard(engine, frames), want, `seek to ${to}`);
    run(engine, RATE / 4);
    const shown = posted.filter((m) => m.type === "levels").map((m) => m.frame);
    assert.ok(shown.length > 0 && shown.every((f) => f >= 10000 && f < 20000), `seek to ${to}: ${shown}`);
  }
});

test("with the loop on, a seek outside it while playing goes on from the loop's start", () => {
  const frames = 4 * RATE;
  const { engine } = setup({ frames, vocal: indexed(frames) });
  engine.handle({ type: "loop", on: true, start: 10000, end: 20000 });
  engine.handle({ type: "seek", frame: 12000 });
  engine.handle({ type: "play", playing: true });
  run(engine, 4096);
  engine.handle({ type: "seek", frame: 30000 });
  assert.equal(firstHeard(engine, frames, true), 10000);
});

const limiter = rack({ effect: "limiter", params: { ceiling: 0, lookahead: 5 } });

for (const [where, racks] of [["no latency", {}], ["a master with latency", { master: limiter }], ["a vocal rack with latency", { vocal: limiter }]]) {
  test(`after a seek through ${where} the first frame heard is the frame sought`, () => {
    const frames = 4 * RATE;
    for (const to of [30001, 30000 + 127, 96000]) {
      const stopped = setup({ frames, vocal: indexed(frames), racks });
      stopped.engine.handle({ type: "seek", frame: to });
      stopped.engine.handle({ type: "play", playing: true });
      assert.equal(firstHeard(stopped.engine, frames), to, `stopped, seek to ${to}`);
      const playing = setup({ frames, vocal: indexed(frames), racks });
      playing.engine.handle({ type: "play", playing: true });
      run(playing.engine, 4096);
      playing.engine.handle({ type: "seek", frame: to });
      assert.equal(firstHeard(playing.engine, frames, true), to, `playing, seek to ${to}`);
    }
  });
}

test("through the pre-roll after a seek the playhead stays on the frame sought, not before it", () => {
  const frames = 4 * RATE;
  const stopped = setup({ frames, vocal: indexed(frames), racks: { master: limiter } });
  stopped.engine.handle({ type: "seek", frame: 96000 });
  stopped.engine.handle({ type: "play", playing: true });
  run(stopped.engine, RATE / 4);
  const shown = stopped.posted.filter((m) => m.type === "levels").map((m) => m.frame);
  assert.ok(shown.length > 0 && shown.every((f) => f >= 96000), `${shown}`);

  const playing = setup({ frames, vocal: indexed(frames), racks: { master: limiter } });
  playing.engine.handle({ type: "play", playing: true });
  run(playing.engine, 9600);
  playing.posted.length = 0;
  playing.engine.handle({ type: "seek", frame: 96000 });
  run(playing.engine, RATE / 4);
  const after = playing.posted.filter((m) => m.type === "levels").map((m) => m.frame);
  assert.ok(after.some((f) => f >= 96000), `${after}`);
  assert.ok(after.every((f) => f < 9600 + RATE / 4 || f >= 96000), `${after}`);
});

test("a pre-roll is not metered: no level, loudness or gain reduction comes from before the spot", () => {
  const frames = 4 * RATE;
  // loud everywhere but just after the spot sought
  const loud = (n) => (n >= RATE && n < RATE + 24000 ? [0, 0] : [0.5 * Math.sin(n / 10), 0.5 * Math.sin(n / 10)]);
  const played = (racks) => {
    const { engine, posted } = setup({ frames, vocal: stereo(frames, loud), racks });
    engine.handle({ type: "seek", frame: RATE });
    engine.handle({ type: "play", playing: true });
    run(engine, 9600);
    const early = posted.filter((m) => m.type === "levels");
    assert.ok(early.length > 0);
    run(engine, RATE / 2);
    return { early, later: posted.filter((m) => m.type === "levels").at(-1) };
  };

  const bare = played({});
  for (const m of bare.early) {
    assert.equal(m.vocal.peak, 0, `vocal at ${m.frame}`);
    assert.equal(m.master.peak, 0, `master at ${m.frame}`);
    assert.ok(!Number.isFinite(m.lufs), `loudness ${m.lufs} at ${m.frame}`);
  }
  assert.ok(bare.later.vocal.peak > 0.49 && Number.isFinite(bare.later.lufs), JSON.stringify(bare.later));

  // the master's own tail of what came before the spot is heard after it, so only its input is checked
  const mastered = played({ master: rack({ effect: "master", params: { gain: 12, trim: 0, ceiling: -1 } }) });
  for (const m of mastered.early) {
    assert.equal(m.vocal.peak, 0, `vocal at ${m.frame}`);
    assert.equal(m.master.gr, 0, `gain reduction at ${m.frame}`);
  }
  assert.ok(mastered.later.master.gr > 5, JSON.stringify(mastered.later));
});
