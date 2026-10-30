// The beat page's engine on the real wasm player, against the ABI driven by hand, and the
// beat worklet and client around it. Needs apps/redline-dsp/wasm/build.sh run.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import * as esbuild from "esbuild";

import createRedlineDsp from "../src/engine/redline_dsp.js";
import { BeatEngine } from "../src/beat/engine.ts";
import { BeatClient } from "../src/beat/client.ts";

const RATE = 48000;
const BLOCK = 128;
const BAR = 96000;
const STEP = 6000;
const M = await createRedlineDsp();

const error = () => M.UTF8ToString(M._rack_error());

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

function abiNew(score) {
  const h = withText(JSON.stringify(score), (p, n) => M._beat_new(p, n, RATE));
  assert.notEqual(h, 0, error());
  return h;
}

// the next `frames` frames of the ABI's mix, in blocks of 128
function abiPlay(h, frames) {
  const ptr = M._malloc(2 * BLOCK * 4);
  const out = new Float32Array(2 * frames);
  try {
    for (let at = 0; at < frames; at += BLOCK) {
      const n = Math.min(BLOCK, frames - at);
      assert.equal(M._beat_process(h, ptr, n), 0, error());
      out.set(M.HEAPF32.subarray(ptr / 4, ptr / 4 + 2 * n), 2 * at);
    }
  } finally {
    M._free(ptr);
  }
  return out;
}

// what the ABI plays of a fresh player of `score`, optionally from a seek
function abiRender(score, frames, seek = null) {
  const h = abiNew(score);
  if (seek !== null) assert.equal(M._beat_seek(h, seek), 0, error());
  const out = abiPlay(h, frames);
  M._beat_free(h);
  return out;
}

const block = new Float32Array(2 * BLOCK);

// `frames` frames of the engine's output, in blocks of 128 as the worklet asks for them
function run(engine, frames) {
  const out = new Float32Array(2 * frames);
  for (let at = 0; at < frames; at += BLOCK) {
    const n = Math.min(BLOCK, frames - at);
    engine.process(block, n);
    out.set(block.subarray(0, 2 * n), 2 * at);
  }
  return out;
}

function sameBits(a, b) {
  if (a.length !== b.length) return false;
  const x = new Uint32Array(a.buffer, a.byteOffset, a.length);
  const y = new Uint32Array(b.buffer, b.byteOffset, b.length);
  return x.every((v, i) => v === y[i]);
}

function maxDiff(a, b) {
  assert.equal(a.length, b.length);
  let worst = 0;
  for (let i = 0; i < a.length; i++) worst = Math.max(worst, Math.abs(a[i] - b[i]));
  return worst;
}

const maxAbs = (x) => x.reduce((m, v) => Math.max(m, Math.abs(v)), 0);

const DRUMS = {
  version: 1,
  bpm: 120,
  seed: 11,
  key: "A minor",
  sounds: {
    kick: { instrument: "kick" },
    snare: { instrument: "snare" },
    hat: { instrument: "hat_closed" },
    open: { instrument: "hat_open", params: { decay: 2 } },
  },
  patterns: {
    p: {
      bars: 1,
      rows: {
        kick: "x.....x...x.....",
        snare: "....x.......x.g.",
        hat: "x3x.x4x.x6xgx2x.",
        open: "..x.......x.....",
      },
    },
  },
  sections: [{ name: "a", bars: 4, play: ["p"] }],
};

// the same with a limiter on the master: 240 frames of latency at 48 kHz
const LATE = { ...structuredClone(DRUMS), mix: { master: [{ effect: "limiter" }] } };

// one saw note held over the first three bars, and an empty kick row
const HELD = {
  version: 1,
  bpm: 120,
  seed: 11,
  key: "A minor",
  sounds: { kick: { instrument: "kick" }, v: { instrument: "saw", params: { release: 0.5 } } },
  patterns: {
    p: {
      bars: 4,
      rows: { kick: ".".repeat(64), v: [{ step: 0, len: 48, note: "C3", vel: 0.9 }] },
    },
  },
  sections: [{ name: "a", bars: 4, play: ["p"] }],
};

const json = (score) => JSON.stringify(score);

function engine(dsp = M) {
  const notices = [];
  const e = new BeatEngine(dsp, RATE, (n) => notices.push(n));
  return { e, notices };
}

function loaded(score, dsp = M) {
  const made = engine(dsp);
  const result = made.e.load(json(score));
  assert.deepEqual(result, { ok: true, rebuilt: false, warnings: [] });
  return made;
}

test("a loaded score renders as the ABI does, block by block", () => {
  for (const score of [DRUMS, LATE]) {
    const { e } = loaded(score);
    assert.equal(e.length, 4 * BAR);
    assert.equal(e.latency, score === LATE ? 240 : 0);
    assert.equal(e.play().ok, true);
    const frames = 100 * BLOCK + 17;
    const got = run(e, frames);
    assert.ok(maxAbs(got) > 0.01);
    assert.ok(sameBits(got, abiRender(score, frames)));
    e.dispose();
  }
});

test("stopped, the engine puts out zeros; a stop fades out, then it is silent", () => {
  const { e } = loaded(DRUMS);
  block.fill(7);
  e.process(block, BLOCK);
  assert.ok(block.every((v) => v === 0));
  e.play();
  run(e, 2000);
  e.stop();
  // the fade is short; after it every block is zero and the song does not move on
  run(e, 4 * BLOCK);
  const at = e.position;
  block.fill(7);
  for (let i = 0; i < 20; i++) {
    e.process(block, BLOCK);
    assert.ok(block.every((v) => v === 0));
  }
  assert.equal(e.position, at);
  assert.equal(e.peakLeft, 0);
  assert.equal(e.peakRight, 0);
  // without a score there is nothing to play
  const { e: empty } = engine();
  assert.equal(empty.play().ok, false);
  block.fill(7);
  empty.process(block, BLOCK);
  assert.ok(block.every((v) => v === 0));
  e.dispose();
});

test("seek and play from a bar equals the ABI's seek", () => {
  const { e } = loaded(DRUMS);
  e.play();
  run(e, 5000);
  const at = 2 * BAR + 3 * STEP + 77;
  assert.equal(e.seek(at).ok, true);
  const got = run(e, 30000);
  const h = abiNew(DRUMS);
  abiPlay(h, 5000);
  assert.equal(M._beat_seek(h, at), 0);
  const want = abiPlay(h, 30000);
  M._beat_free(h);
  assert.ok(maxAbs(got) > 0.01);
  assert.ok(sameBits(got, want));
  // and as the whole song does from there, the voices crossing the spot included
  assert.ok(sameBits(got, abiRender(DRUMS, at + 30000).subarray(2 * at)));
  assert.equal(e.seek(-1).ok, false);
  assert.equal(e.seek(1.5).ok, false);
  e.dispose();
});

test("the position is the song frame heard: the player's position less its latency", () => {
  const { e } = loaded(LATE);
  e.play();
  assert.equal(e.position, 0);
  run(e, 200);
  // still inside the latency: nothing of the song is heard yet
  assert.equal(e.position, 0);
  run(e, 800);
  assert.equal(e.position, 1000 - 240);
  e.seek(BAR);
  assert.equal(e.position, BAR - 240);
  run(e, 1000);
  assert.equal(e.position, BAR + 1000 - 240);
  e.dispose();
});

test("a loop wraps exactly at its end frame", () => {
  const start = BAR + 4 * STEP;
  const end = 2 * BAR + 100;
  const { e } = loaded(DRUMS);
  assert.equal(e.loop(start, end).ok, true);
  // the loop sends the player into it
  assert.equal(e.position, start);
  e.play();
  const got = run(e, (end - start) + 20000);
  const full = abiRender(DRUMS, end + 20000);
  assert.ok(sameBits(got.subarray(0, 2 * (end - start)), full.subarray(2 * start, 2 * end)));
  assert.ok(sameBits(got.subarray(2 * (end - start)), full.subarray(2 * start, 2 * (start + 20000))));
  assert.equal(e.loop(5, 5).ok, false);
  assert.equal(e.loop(-1, 5).ok, false);
  e.dispose();
});

test("past a loop's end the heard position runs on to the end, then starts over", () => {
  const start = BAR;
  const end = BAR + 10 * BLOCK;
  const { e } = loaded(LATE);
  e.loop(start, end);
  e.play();
  run(e, 240);
  assert.equal(e.position, start);
  run(e, end - start - 240);
  // the player is at the end; what is heard is 240 frames before it
  assert.equal(e.position, end - 240);
  run(e, 128);
  assert.equal(e.position, end - 240 + 128);
  run(e, 112);
  assert.equal(e.position, start);
  run(e, 100);
  assert.equal(e.position, start + 100);
  e.dispose();
});

test("at the song's end the tail rings out and the engine stops, or it starts over", () => {
  const { e, notices } = loaded(DRUMS);
  e.seek(4 * BAR - 2000);
  e.play();
  run(e, 2000 + 5 * RATE);
  assert.equal(e.playing, false);
  assert.deepEqual(notices, [{ type: "ended" }]);
  assert.ok(e.position >= 4 * BAR);
  // a play at the end starts from the top
  e.play();
  assert.equal(e.position, 0);

  const again = loaded(DRUMS);
  again.e.repeat = true;
  again.e.seek(4 * BAR - 1000);
  again.e.play();
  const got = run(again.e, 3000);
  const full = abiRender(DRUMS, 4 * BAR);
  assert.ok(sameBits(got.subarray(0, 2000), full.subarray(2 * (4 * BAR - 1000))));
  assert.ok(sameBits(got.subarray(2000), full.subarray(0, 2 * 2000)));
  assert.equal(again.e.position, 2000);
  assert.deepEqual(again.notices, []);
  e.dispose();
  again.e.dispose();
});

test("an update with the same sounds plays on without a gap, and its new notes follow", () => {
  const withKick = structuredClone(HELD);
  withKick.patterns.p.rows.kick = ".".repeat(32) + "x" + ".".repeat(31);
  const { e } = loaded(HELD);
  e.play();
  const cut = 470 * BLOCK;
  const before = run(e, cut);
  assert.deepEqual(e.update(json(withKick)), { ok: true, rebuilt: false, warnings: [] });
  assert.equal(e.position, cut);
  const after = run(e, 3 * BAR - cut);
  const got = new Float32Array(before.length + after.length);
  got.set(before);
  got.set(after, before.length);

  // no step across the update larger than the saw's own steps just before it
  const left = (i) => got[2 * i];
  let ownStep = 0;
  for (let i = cut - 4800; i < cut; i++) ownStep = Math.max(ownStep, Math.abs(left(i) - left(i - 1)));
  assert.ok(Math.abs(left(cut) - left(cut - 1)) <= ownStep);
  assert.ok(maxAbs(got.subarray(2 * (cut - 1000), 2 * (cut + 1000))) > 0.05, "the note is held across");

  // it is what the new score plays from the top, the kick of bar 3 included
  const want = abiRender(withKick, 3 * BAR);
  assert.ok(maxDiff(got, want) <= 1e-6, `${maxDiff(got, want)}`);
  const old = abiRender(HELD, 3 * BAR);
  assert.ok(maxDiff(got.subarray(2 * 2 * BAR), old.subarray(2 * 2 * BAR)) > 0.05, "the kick is heard");
  e.dispose();
});

test("an update that changes a sound's rack swaps that rack in, by the core's order of sounds", () => {
  const louder = structuredClone(DRUMS);
  louder.mix = { sounds: { kick: { rack: [{ effect: "gain", params: { db: -12 } }] } } };
  const { e } = loaded(DRUMS);
  e.play();
  const cut = 40 * BLOCK;
  const head = run(e, cut);
  assert.deepEqual(e.update(json(louder)), { ok: true, rebuilt: false, warnings: [] });
  const got = run(e, BAR);
  // by hand: the score, then the rack of the kick, second of hat, kick, open, snare
  const h = abiNew(DRUMS);
  assert.ok(sameBits(head, abiPlay(h, cut)));
  assert.equal(withText(json(louder), (p, n) => M._beat_set_score(h, p, n)), 0, error());
  assert.equal(withText(json(louder.mix.sounds.kick.rack), (p, n) => M._beat_set_rack(h, 1, p, n)), 0, error());
  const want = abiPlay(h, BAR);
  M._beat_free(h);
  assert.ok(sameBits(got, want));
  assert.ok(maxDiff(got, abiRender(DRUMS, cut + BAR).subarray(2 * cut)) > 0.05, "the kick is quieter");
  e.dispose();
});

test("an update with a renamed sound rebuilds the player and goes on from the same spot", () => {
  const a = { ...structuredClone(HELD), mix: { master: [{ effect: "limiter" }] } };
  const b = structuredClone(a);
  b.sounds.w = b.sounds.v;
  delete b.sounds.v;
  b.patterns.p.rows.w = b.patterns.p.rows.v;
  delete b.patterns.p.rows.v;
  const { e } = loaded(a);
  e.play();
  run(e, 300 * BLOCK);
  const heard = e.position;
  assert.equal(heard, 300 * BLOCK - 240);
  assert.deepEqual(e.update(json(b)), { ok: true, rebuilt: true, warnings: [] });
  assert.equal(e.position, heard);
  assert.equal(e.playing, true);
  const got = run(e, 20000);
  // a new player of the new score, sent on so the same song frame is heard next
  const want = abiRender(b, 20000, heard + 240);
  assert.ok(sameBits(got, want));
  assert.ok(maxAbs(got) > 0.05);
  e.dispose();
});

test("a master rack the core cannot build after the check leaves the old score playing at its spot", () => {
  // 4 throws on the kick (96000 frames) take the player; 2 more on the master pass the
  // check but put the mix past the core's latency cap, so only the new player fails
  const old = structuredClone(DRUMS);
  old.mix = { sounds: { kick: { rack: Array(4).fill({ effect: "throws" }) } } };
  const next = structuredClone(old);
  next.mix.master = Array(2).fill({ effect: "throws" });
  next.patterns.p.rows.kick = "x.x.x.x.x.x.x.x.";
  const { e } = loaded(old);
  e.play();
  run(e, 1000 * BLOCK);
  const heard = e.position;
  const result = e.update(json(next));
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((l) => l.includes("racks delay the mix")), result.errors.join("\n"));
  assert.equal(e.loaded, true);
  assert.equal(e.playing, true);
  assert.equal(e.position, heard);
  // a new player's racks start empty: its first `latency` frames are silent
  const latency = e.latency;
  const frames = latency + 20000;
  const got = run(e, frames);
  assert.ok(sameBits(got, abiRender(old, frames, heard + latency)));
  assert.ok(maxAbs(got.subarray(2 * latency)) > 0.01);
  e.dispose();
});

test("a broken score comes back as path lines, and the engine plays on", () => {
  const broken = structuredClone(DRUMS);
  broken.patterns.p.rows.kick = "x.......";
  broken.bpm = 400;
  let made = 0;
  const counted = Object.create(M);
  counted._beat_new = (...a) => (made++, M._beat_new(...a));
  counted._beat_free = (...a) => (made++, M._beat_free(...a));
  const { e } = loaded(DRUMS, counted);
  e.play();
  run(e, 3000);
  made = 0;
  for (const f of [(s) => e.update(s), (s) => e.load(s)]) {
    const result = f(json(broken));
    assert.equal(result.ok, false);
    assert.ok(result.errors.some((l) => l.startsWith("bpm: ")), result.errors.join("\n"));
    assert.ok(result.errors.some((l) => l.startsWith("patterns.p.rows.kick: ")), result.errors.join("\n"));
  }
  assert.equal(e.update("{").ok, false);
  // the old player was neither freed nor made again
  assert.equal(made, 0);
  const got = run(e, 5000);
  assert.ok(sameBits(got, abiRender(DRUMS, 8000).subarray(2 * 3000)));
  assert.equal(e.playing, true);

  // a first load that fails leaves no player, and a good one after it works
  const { e: fresh } = engine();
  const result = fresh.load(json(broken));
  assert.equal(result.ok, false);
  assert.equal(fresh.loaded, false);
  assert.equal(fresh.load(json(DRUMS)).ok, true);
  fresh.play();
  assert.ok(sameBits(run(fresh, 2000), abiRender(DRUMS, 2000)));
  e.dispose();
  fresh.dispose();
});

test("a rack swaps in by sound name; one later than the player compensates rebuilds it", () => {
  const { e } = loaded(DRUMS);
  e.play();
  run(e, 2000);
  assert.deepEqual(e.setRack("kick", json([{ effect: "gain", params: { db: -6 } }])), { ok: true, rebuilt: false, warnings: [] });
  assert.equal(e.setRack("nope", "[]").ok, false);
  assert.equal(e.setRack("kick", json([{ effect: "nope" }])).ok, false);
  const heard = e.position;
  const result = e.setRack("kick", json([{ effect: "limiter" }]));
  assert.deepEqual(result, { ok: true, rebuilt: true, warnings: [] });
  assert.equal(e.position, heard);
  assert.ok(e.latency > 0);
  // the rebuilt player keeps both racks: they are in the score it was made of
  const want = structuredClone(DRUMS);
  want.mix = { sounds: { kick: { rack: [{ effect: "limiter" }] } } };
  assert.ok(sameBits(run(e, 4000), abiRender(want, 4000, heard + e.latency)));
  assert.deepEqual(e.setMasterRack(json([{ effect: "limiter" }])).ok, true);
  e.dispose();
});

const SAMPLED = {
  version: 1,
  bpm: 120,
  seed: 3,
  key: "A minor",
  sounds: { keys: { instrument: "sampler", library: "sine" }, kick: { instrument: "kick" } },
  patterns: { p: { bars: 1, rows: { keys: [{ step: 0, len: 8, note: "C5", vel: 1.0 }], kick: "x..............." } } },
  sections: [{ name: "a", bars: 2, play: ["p"] }],
};

function sineZone() {
  const samples = new Int16Array(RATE);
  for (let i = 0; i < RATE; i++) samples[i] = Math.round(16383 * Math.sin((2 * Math.PI * 440 * i) / RATE));
  return { library: "sine", root: 60, velLo: 0, velHi: 127, rate: RATE, samples, channels: 1 };
}

function hz(out, from, to) {
  let crossings = 0;
  for (let i = from + 1; i < to; i++) if ((out[2 * i] >= 0) !== (out[2 * i - 2] >= 0)) crossings++;
  return (crossings / 2) * (RATE / (to - from));
}

test("a zone plays in the sampler, and it is loaded again into a rebuilt player", () => {
  const { e } = loaded(SAMPLED);
  assert.equal(e.loadZone(sineZone()).ok, true);
  assert.equal(e.rebuildSamplers().ok, true);
  e.play();
  const out = run(e, 24000);
  assert.ok(Math.abs(hz(out, 9600, 24000) - 880) < 10);
  const bad = { ...sineZone(), root: 200 };
  assert.equal(e.loadZone(bad).ok, false);

  // the kick renamed: a new player, which needs the zone again
  const renamed = structuredClone(SAMPLED);
  renamed.sounds.bd = renamed.sounds.kick;
  delete renamed.sounds.kick;
  renamed.patterns.p.rows.bd = renamed.patterns.p.rows.kick;
  delete renamed.patterns.p.rows.kick;
  e.seek(0);
  const result = e.update(json(renamed));
  assert.deepEqual(result, { ok: true, rebuilt: true, warnings: [] });
  const again = run(e, 24000);
  assert.ok(Math.abs(hz(again, 9600, 24000) - 880) < 10);
  e.dispose();
});

test("process allocates nothing: no malloc, the same heap and the same views over 1000 calls", () => {
  let calls = 0;
  const counted = Object.create(M);
  counted._malloc = (n) => {
    calls++;
    return M._malloc(n);
  };
  counted._free = (p) => {
    calls++;
    M._free(p);
  };
  const { e } = loaded(LATE, counted);
  e.loop(BAR, BAR + 20000);
  e.play();
  run(e, 1000);
  const heap = M.HEAPU8.buffer;
  const size = heap.byteLength;
  const scratch = e.scratch;
  const probe = M._malloc(64);
  M._free(probe);
  calls = 0;
  for (let i = 0; i < 1000; i++) e.process(block, BLOCK);
  assert.equal(calls, 0);
  assert.equal(M.HEAPU8.buffer, heap);
  assert.equal(M.HEAPU8.buffer.byteLength, size);
  assert.equal(e.scratch, scratch);
  // nothing was left allocated inside the module either: the next block is where it was
  const again = M._malloc(64);
  M._free(again);
  assert.equal(again, probe);
  e.dispose();
});

test("a throw inside the wasm is reported once as fatal, and the output goes silent", () => {
  const broken = Object.create(M);
  broken._beat_process = () => {
    throw new WebAssembly.RuntimeError("unreachable");
  };
  const { e, notices } = loaded(DRUMS, broken);
  e.play();
  block.fill(7);
  for (let i = 0; i < 10; i++) {
    e.process(block, BLOCK);
    assert.ok(block.every((v) => v === 0));
  }
  assert.deepEqual(notices, [{ type: "fatal", message: "unreachable" }]);
  assert.equal(e.dead, true);
  assert.equal(e.play().ok, false);
  assert.equal(e.load(json(DRUMS)).ok, false);
});

// --- the worklet and the client

const workletSource = readFileSync(new URL("../src/worklet/beat-processor.ts", import.meta.url), "utf8");
const engineSource = readFileSync(new URL("../src/beat/engine.ts", import.meta.url), "utf8");

function body(source, name) {
  const start = source.indexOf(`\n  ${name}(`);
  assert.ok(start >= 0, `no ${name}`);
  const open = source.indexOf("{", source.indexOf(")", start));
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(open, i + 1);
  }
  throw new Error(`${name} does not close`);
}

test("the audio callbacks make no closure per call", () => {
  for (const code of [body(workletSource, "process"), body(engineSource, "process")]) {
    assert.doesNotMatch(code, /=>|function\s*\(/, code);
  }
});

async function startWorklet() {
  const out = await esbuild.build({
    entryPoints: [new URL("../src/worklet/beat-processor.ts", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
    external: ["node:*"],
    logLevel: "silent",
  });
  let registered = null;
  const posted = [];
  class AudioWorkletProcessor {
    constructor() {
      this.port = { onmessage: null, postMessage: (m) => posted.push(m) };
    }
  }
  const scope = { AudioWorkletProcessor, registerProcessor: (name, cls) => (registered = { name, cls }), sampleRate: RATE, WebAssembly, console, Math, Promise };
  vm.createContext(scope);
  vm.runInContext(out.outputFiles[0].text, scope);
  assert.equal(registered.name, "redline-beat");
  const node = new registered.cls();
  const bytes = readFileSync(new URL("../src/engine/redline_dsp.wasm", import.meta.url));
  node.port.onmessage({ data: { type: "wasm", bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) } });
  for (let i = 0; i < 200 && !posted.some((m) => m.type === "ready"); i++) await new Promise((r) => setTimeout(r, 10));
  assert.ok(posted.some((m) => m.type === "ready"), JSON.stringify(posted));
  return { node, posted };
}

test("the client drives the worklet: results as values, positions and meters while it plays", async () => {
  const { node, posted } = await startWorklet();
  // the worklet's port and the page's, joined as a MessageChannel would: copies, a turn later
  const port = { onmessage: null, postMessage: (m) => queueMicrotask(() => node.port.onmessage({ data: structuredClone(m) })) };
  const forward = posted.length;
  node.port.postMessage = (m) => queueMicrotask(() => port.onmessage?.({ data: structuredClone(m) }));
  const client = new BeatClient(port);
  assert.ok(forward > 0);

  const broken = structuredClone(DRUMS);
  broken.bpm = 400;
  const bad = await client.load(json(broken));
  assert.equal(bad.ok, false);
  assert.ok(bad.errors.some((l) => l.startsWith("bpm: ")));
  assert.deepEqual(await client.load(json(LATE)), { ok: true, rebuilt: false, warnings: [] });
  assert.equal(client.latency, 240);
  assert.equal(client.length, 4 * BAR);

  const positions = [];
  const off = client.subscribe((p) => positions.push(p));
  assert.equal((await client.seek(BAR)).ok, true);
  assert.equal((await client.play()).ok, true);
  const out = [new Float32Array(BLOCK), new Float32Array(BLOCK)];
  for (let i = 0; i < 200; i++) node.process([], [out], {});
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(positions.length >= 5, `${positions.length} reports`);
  const last = positions.at(-1);
  assert.equal(last.playing, true);
  assert.ok(last.frame > BAR && last.frame <= BAR + 200 * BLOCK - 240, `${last.frame}`);
  assert.ok(positions.some((p) => p.peakLeft > 0.01 && p.peakRight > 0.01));

  // a report from before a seek is not passed on
  const count = positions.length;
  client.seek(0);
  port.onmessage({ data: { ...last, type: "position" } });
  assert.equal(positions.length, count);

  off();
  for (let i = 0; i < 50; i++) node.process([], [out], {});
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(positions.length, count);
  assert.equal((await client.stop()).ok, true);
  assert.equal((await client.update(json({ ...DRUMS, bpm: 400 }))).ok, false);
});

test("after a fatal error the client answers every call with it and sends nothing", async () => {
  const sent = [];
  const port = { onmessage: null, postMessage: (m) => sent.push(m) };
  const client = new BeatClient(port);
  const fatal = [];
  client.onFatal = (m) => fatal.push(m);
  const waiting = client.play();
  port.onmessage({ data: { type: "fatal", message: "boom" } });
  assert.deepEqual(await waiting, { ok: false, errors: ["boom"] });
  assert.equal(client.dead, true);
  const count = sent.length;
  assert.deepEqual(await client.seek(10), { ok: false, errors: ["boom"] });
  assert.equal(sent.length, count);
  port.onmessage({ data: { type: "fatal", message: "again" } });
  assert.deepEqual(fatal, ["boom"]);
});
