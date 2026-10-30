import type { Dsp } from "./dsp.ts";

export interface DspOptions {
  instantiateWasm?: (
    imports: WebAssembly.Imports,
    done: (instance: WebAssembly.Instance) => void,
  ) => Record<string, never>;
}

// The beat player's C ABI (apps/redline-dsp/wasm/src/lib.rs). Song frames cross as numbers
// (whole, up to 2^53); failures return 0, -1 or a null pointer with the reason in
// `_rack_error`. JSON results are NUL-terminated pointers for UTF8ToString.
export interface BeatAbi {
  _beat_new(score: number, len: number, rate: number): number;
  _beat_free(handle: number): void;
  _beat_set_score(handle: number, score: number, len: number): number;
  _beat_set_rack(handle: number, sound: number, json: number, len: number): number;
  _beat_seek(handle: number, frame: number): number;
  _beat_process(handle: number, output: number, frames: number): number;
  _beat_latency(handle: number): number;
  _beat_length(handle: number): number;
  _beat_position(handle: number): number;
  _beat_load_zone(
    handle: number,
    library: number,
    libraryLen: number,
    root: number,
    velLo: number,
    velHi: number,
    rate: number,
    samples: number,
    frames: number,
    channels: number,
  ): number;
  _beat_rebuild_samplers(handle: number): number;
  _beat_events_json(score: number, len: number, rate: number): number;
  _beat_instruments_json(): number;
  _beat_check_json(score: number, len: number): number;
}

export default function createRedlineDsp(options?: DspOptions): Promise<Dsp & BeatAbi>;
