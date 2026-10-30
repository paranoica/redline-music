// Plays two stems through the studio's worklet engine on the wasm core, 128 frames at a time
// as an AudioWorklet asks for them, and writes what it outputs.
//
//   node live_engine.mjs spec.json out.f32
//
// spec.json: {"rate", "vocal": path, "beat": path, "racks": {vocal, beat, master}, "lanes"};
// the stems are interleaved little-endian float32. Prints {"latency"} on success.
import { readFileSync, writeFileSync } from "node:fs";

import { Engine } from "../../../apps/studio/src/engine/core.ts";
import createRedlineDsp from "../../../apps/studio/src/engine/redline_dsp.js";

const BLOCK = 128;
const [, , specPath, outPath] = process.argv;
const spec = JSON.parse(readFileSync(specPath, "utf8"));

function stem(path) {
  const bytes = new Uint8Array(readFileSync(path));
  return new Float32Array(bytes.buffer, 0, bytes.byteLength / 4);
}

const vocal = stem(spec.vocal);
const beat = stem(spec.beat);
const frames = vocal.length / 2;
const dsp = await createRedlineDsp();
const posted = [];
const engine = new Engine(dsp, spec.rate, (m) => posted.push(m));
engine.handle({ type: "stems", frames, vocal, beat, original: new Float32Array(2 * frames) });
for (const track of ["vocal", "beat", "master"]) {
  const lanes = track === "vocal" ? spec.lanes : [];
  engine.handle({ type: "rack", track, version: 1, json: spec.racks[track], lanes });
}
engine.handle({ type: "listen", gains: { result: 1, original: 0, reference: 0, render: 0 } });
engine.handle({ type: "play", playing: true });

const out = new Float32Array(2 * frames);
const left = new Float32Array(BLOCK);
const right = new Float32Array(BLOCK);
for (let at = 0; at < frames; at += BLOCK) {
  engine.render(left, right);
  for (let i = 0; i < BLOCK && at + i < frames; i++) {
    out[2 * (at + i)] = left[i];
    out[2 * (at + i) + 1] = right[i];
  }
}

const errors = posted.filter((m) => m.type === "error");
if (errors.length) {
  console.error(JSON.stringify(errors));
  process.exit(1);
}
const latency = Math.max(0, ...posted.filter((m) => m.type === "levels").map((m) => m.latency));
writeFileSync(outPath, Buffer.from(out.buffer));
console.log(JSON.stringify({ latency }));
