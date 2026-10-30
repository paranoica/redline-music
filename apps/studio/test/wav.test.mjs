import assert from "node:assert/strict";
import { test } from "node:test";

import { parseWav } from "../src/core/wav.ts";

// a RIFF/WAVE file by hand: fmt, then optional extra chunks, then data
function wav({ format, channels, rate, bits, samples, extra = [] }) {
  const bytesPer = bits / 8;
  const data = new DataView(new ArrayBuffer(samples.length * bytesPer));
  samples.forEach((v, i) => {
    if (format === 3) data.setFloat32(i * 4, v, true);
    else data.setInt16(i * 2, v, true);
  });
  const chunks = [];
  const fmt = new DataView(new ArrayBuffer(16));
  fmt.setUint16(0, format, true);
  fmt.setUint16(2, channels, true);
  fmt.setUint32(4, rate, true);
  fmt.setUint32(8, rate * channels * bytesPer, true);
  fmt.setUint16(12, channels * bytesPer, true);
  fmt.setUint16(14, bits, true);
  chunks.push(["fmt ", new Uint8Array(fmt.buffer)]);
  for (const e of extra) chunks.push(e);
  chunks.push(["data", new Uint8Array(data.buffer)]);
  const size = chunks.reduce((n, [, b]) => n + 8 + b.length + (b.length % 2), 4);
  const out = new Uint8Array(8 + size);
  const view = new DataView(out.buffer);
  const tag = (at, s) => [...s].forEach((c, i) => (out[at + i] = c.charCodeAt(0)));
  tag(0, "RIFF");
  view.setUint32(4, size, true);
  tag(8, "WAVE");
  let at = 12;
  for (const [id, body] of chunks) {
    tag(at, id);
    view.setUint32(at + 4, body.length, true);
    out.set(body, at + 8);
    at += 8 + body.length + (body.length % 2);
  }
  return out.buffer;
}

test("float32 stereo comes back as written", () => {
  const got = parseWav(wav({ format: 3, channels: 2, rate: 44100, bits: 32, samples: [0.5, -0.25, 1, -1] }));
  assert.equal(got.rate, 44100);
  assert.equal(got.frames, 2);
  assert.deepEqual([...got.data], [0.5, -0.25, 1, -1]);
});

test("16-bit PCM is scaled by 1/32768", () => {
  const got = parseWav(wav({ format: 1, channels: 2, rate: 48000, bits: 16, samples: [16384, -32768, 0, 32767] }));
  assert.deepEqual([...got.data], [0.5, -1, 0, 32767 / 32768]);
});

test("mono is spread to both channels; an odd-sized chunk before data is skipped with its pad byte", () => {
  const got = parseWav(
    wav({
      format: 3,
      channels: 1,
      rate: 48000,
      bits: 32,
      samples: [0.5, -0.5],
      extra: [["LIST", new Uint8Array([1, 2, 3])]],
    }),
  );
  assert.equal(got.frames, 2);
  assert.deepEqual([...got.data], [0.5, 0.5, -0.5, -0.5]);
});

test("not a WAV, or a format it does not read, is refused with a reason", () => {
  assert.throws(() => parseWav(new ArrayBuffer(4)), /WAV/);
  assert.throws(() => parseWav(wav({ format: 1, channels: 2, rate: 48000, bits: 8, samples: [] })), /8-bit/);
});
