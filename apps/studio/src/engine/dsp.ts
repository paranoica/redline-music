// The part of the emscripten module of redline_dsp the engine calls.

export interface Dsp {
  HEAPU8: Uint8Array;
  HEAPF32: Float32Array;
  _malloc(bytes: number): number;
  _free(ptr: number): void;
  _rack_new(rate: number, json: number, len: number): number;
  _rack_free(handle: number): void;
  _rack_latency(handle: number): number;
  _rack_process(handle: number, input: number, side: number, output: number, frames: number): number;
  _rack_set(handle: number, slot: number, name: number, len: number, value: number): number;
  _rack_reset(handle: number, rate: number): number;
  _rack_error(): number;
  _effects_json(): number;
  UTF8ToString(ptr: number): string;
}

// TextEncoder is not there in an AudioWorkletGlobalScope
export function utf8(text: string): Uint8Array {
  const out: number[] = [];
  for (const ch of text) {
    const c = ch.codePointAt(0) as number;
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return Uint8Array.from(out);
}

export function withText<R>(dsp: Dsp, text: string, f: (ptr: number, len: number) => R): R {
  const bytes = utf8(text);
  const ptr = dsp._malloc(Math.max(1, bytes.length));
  if (!ptr) throw new Error("the wasm heap is full");
  dsp.HEAPU8.set(bytes, ptr);
  try {
    return f(ptr, bytes.length);
  } finally {
    dsp._free(ptr);
  }
}

export function lastError(dsp: Dsp): string {
  return dsp.UTF8ToString(dsp._rack_error());
}
