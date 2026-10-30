import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LoudnessMeter,
  abGains,
  integratedLufs,
  kWeighting,
  loudestSpan,
} from "../src/core/loudness.ts";

const RATE = 48000;

// interleaved stereo: a 997 Hz sine of `amp` on both channels for each [seconds, amp] part
function sine(parts, rate = RATE) {
  const frames = parts.reduce((n, [s]) => n + Math.round(s * rate), 0);
  const x = new Float32Array(2 * frames);
  let n = 0;
  for (const [seconds, amp] of parts) {
    const end = n + Math.round(seconds * rate);
    for (; n < end; n++) {
      const v = amp * Math.sin((2 * Math.PI * 997 * n) / rate);
      x[2 * n] = v;
      x[2 * n + 1] = v;
    }
  }
  return x;
}

function close(got, want, tol, what) {
  assert.ok(Math.abs(got - want) <= tol, `${what}: ${got} against ${want}`);
}

test("the K filter at 48 kHz has the coefficients printed in ITU-R BS.1770-4", () => {
  const { shelf, highpass } = kWeighting(48000);
  const spec = {
    shelf: { b: [1.53512485958697, -2.69169618940638, 1.19839281085285], a: [1, -1.69065929318241, 0.73248077421585] },
    highpass: { b: [1, -2, 1], a: [1, -1.99004745483398, 0.99007225036621] },
  };
  for (const [name, got] of [["shelf", shelf], ["highpass", highpass]]) {
    for (const k of ["b", "a"]) {
      spec[name][k].forEach((want, i) => close(got[k][i], want, 1e-8, `${name}.${k}${i}`));
    }
  }
});

test("a 997 Hz sine at amplitude 0.1 on both channels reads -20 LUFS, at 44.1 kHz too", () => {
  close(integratedLufs(sine([[5, 0.1]]), RATE), -20, 0.1, "48 kHz");
  close(integratedLufs(sine([[5, 0.1]], 44100), 44100), -20, 0.1, "44.1 kHz");
});

test("silence reads -Infinity; the relative gate drops the part 20 dB down", () => {
  assert.equal(integratedLufs(new Float32Array(2 * RATE * 2), RATE), -Infinity);
  // ungated the mean would be -23 LUFS; the -40 part falls under the -33 relative gate
  close(integratedLufs(sine([[6, 0.1], [6, 0.01]]), RATE), -20, 0.2, "gated");
  // under the absolute gate of -70 LUFS nothing counts
  assert.equal(integratedLufs(sine([[3, 0.0001]]), RATE), -Infinity);
});

test("a frame range measures only that range", () => {
  const x = sine([[4, 0.01], [4, 0.1]]);
  close(integratedLufs(x, RATE, 4 * RATE, 8 * RATE), -20, 0.1, "the loud half");
  close(integratedLufs(x, RATE, 0, 4 * RATE), -40, 0.1, "the quiet half");
});

test("A/B gains bring every known side down to the quietest; unknown ones stay at 0 dB", () => {
  assert.deepEqual(abGains({ original: -14, result: -9, reference: -8 }), {
    original: 0,
    result: -5,
    reference: -6,
  });
  assert.deepEqual(abGains({ original: -14, result: null }), { original: 0, result: 0 });
  assert.deepEqual(abGains({ original: -Infinity, result: -9 }), { original: 0, result: 0 });
  assert.deepEqual(abGains({}), {});
});

test("the loudest span is where the loud part is", () => {
  const x = sine([[10, 0.01], [5, 0.5], [15, 0.01]]);
  const span = loudestSpan(x, RATE, 5);
  close(span.start, 10, 0.5, "start");
  close(span.end - span.start, 5, 1e-9, "length");
  // a span longer than the audio is the whole of it
  assert.deepEqual(loudestSpan(sine([[2, 0.1]]), RATE, 5), { start: 0, end: 2 });
});

test("the running meter reads the last window only", () => {
  const m = new LoudnessMeter(RATE, 30);
  assert.equal(m.lufs(10), -Infinity);
  const loud = sine([[6, 0.1]]);
  for (let at = 0; at < loud.length / 2; at += 128) m.push(loud.subarray(2 * at, 2 * (at + 128)), 128);
  close(m.lufs(5), -20, 0.15, "steady");
  const quiet = sine([[6, 0.01]]);
  for (let at = 0; at < quiet.length / 2; at += 128) m.push(quiet.subarray(2 * at, 2 * (at + 128)), 128);
  close(m.lufs(5), -40, 0.15, "after the change, over the last 5 s");
  m.reset();
  assert.equal(m.lufs(5), -Infinity);
});
