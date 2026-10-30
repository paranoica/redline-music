// The bundled worklet in a bare scope, as an AudioWorkletGlobalScope is: no process, fetch,
// URL, TextEncoder or TextDecoder. The page's first message brings the wasm bytes.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import * as esbuild from "esbuild";

const RATE = 48000;

async function bundle() {
  const out = await esbuild.build({
    entryPoints: [new URL("../src/worklet/processor.ts", import.meta.url).pathname],
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
    external: ["node:*"],
    logLevel: "silent",
  });
  return out.outputFiles[0].text;
}

test("the worklet starts from wasm bytes alone and plays the stems", async () => {
  const code = await bundle();
  let registered = null;
  const posted = [];
  class AudioWorkletProcessor {
    constructor() {
      this.port = { onmessage: null, postMessage: (m) => posted.push(m) };
    }
  }
  const scope = {
    AudioWorkletProcessor,
    registerProcessor: (name, cls) => (registered = { name, cls }),
    sampleRate: RATE,
    WebAssembly,
    console,
    Math,
    Promise,
  };
  vm.createContext(scope);
  vm.runInContext(code, scope);
  assert.equal(registered?.name, "redline-engine");
  const node = new registered.cls();
  const send = (m) => node.port.onmessage({ data: m });
  const bytes = readFileSync(new URL("../src/engine/redline_dsp.wasm", import.meta.url));
  send({ type: "wasm", bytes: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) });
  // messages before the engine is up wait for it
  const frames = RATE;
  const dc = (v) => new Float32Array(2 * frames).fill(v);
  send({ type: "stems", frames, vocal: dc(0.25), beat: dc(0.125), original: dc(0) });
  for (const track of ["vocal", "beat", "master"]) {
    send({ type: "rack", track, version: 1, json: '{"effects":[{"effect":"gain","params":{"db":0}}]}', lanes: [] });
  }
  send({ type: "listen", gains: { result: 1, original: 0, reference: 0, render: 0 } });
  send({ type: "play", playing: true });
  for (let i = 0; i < 200 && !posted.some((m) => m.type === "ready"); i++) await new Promise((r) => setTimeout(r, 10));
  assert.ok(posted.some((m) => m.type === "ready"), JSON.stringify(posted));
  const out = [new Float32Array(128), new Float32Array(128)];
  for (let i = 0; i < 40; i++) assert.equal(node.process([], [out], {}), true);
  assert.ok(Math.abs(out[0][127] - 0.375) < 1e-6, `${out[0][127]}`);
  assert.ok(posted.some((m) => m.type === "levels"));
  assert.deepEqual(posted.filter((m) => m.type === "error"), []);
});
