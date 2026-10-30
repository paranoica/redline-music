import assert from "node:assert/strict";
import { test } from "node:test";

import { DelayLine, align } from "../src/core/latency.ts";

test("the rack with less latency is delayed by the difference", () => {
  assert.deepEqual(align(5760, 20, 24), { vocalPad: 0, beatPad: 5740, total: 5784 });
  assert.deepEqual(align(0, 20, 0), { vocalPad: 20, beatPad: 0, total: 20 });
  assert.deepEqual(align(0, 0, 0), { vocalPad: 0, beatPad: 0, total: 0 });
  assert.deepEqual(align(300, 300, 7), { vocalPad: 0, beatPad: 0, total: 307 });
});

test("a negative latency (a failed query) is refused", () => {
  assert.throws(() => align(-1, 0, 0), /latency/);
});

// interleaved stereo with an impulse at `at` on the left and half of it on the right
function impulse(frames, at) {
  const x = new Float32Array(2 * frames);
  x[2 * at] = 1;
  x[2 * at + 1] = 0.5;
  return x;
}

test("a delay line moves an impulse by its delay across block boundaries", () => {
  const d = new DelayLine(1024);
  d.setDelay(200);
  const input = impulse(512, 100);
  const out = new Float32Array(input.length);
  for (let at = 0; at < 512; at += 128) {
    const block = input.slice(2 * at, 2 * (at + 128));
    d.process(block, 128);
    out.set(block, 2 * at);
  }
  for (let i = 0; i < 512; i++) {
    const want = i === 300 ? [1, 0.5] : [0, 0];
    assert.deepEqual([out[2 * i], out[2 * i + 1]], want, `frame ${i}`);
  }
});

test("a delay of 0 passes the block through unchanged", () => {
  const d = new DelayLine(64);
  const block = impulse(128, 0);
  d.process(block, 128);
  assert.equal(block[0], 1);
  assert.equal(block[1], 0.5);
});

test("the delay is the capacity at most; setting it clears what it held", () => {
  const d = new DelayLine(256);
  assert.throws(() => d.setDelay(257), /256/);
  d.setDelay(256);
  const a = impulse(128, 5);
  d.process(a, 128);
  d.setDelay(10);
  const b = new Float32Array(2 * 512);
  d.process(b, 512);
  assert.ok(b.every((v) => v === 0), "the impulse held before the change is dropped");
});
