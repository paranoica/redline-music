// Messages between the beat page and its AudioWorklet's BeatEngine.

// A call that failed says why in "path: message" lines when the score was at fault.
// `rebuilt`: the player was made anew (other sounds, or a rack later than it compensated),
// so the first `latency` frames after the call are silent.
export type BeatResult = { ok: true; rebuilt: boolean; warnings: string[] } | { ok: false; errors: string[] };

// A recorded zone for the sampler: i16 frames, interleaved when stereo.
export interface Zone {
  library: string;
  root: number;
  velLo: number;
  velHi: number;
  rate: number;
  samples: Int16Array;
  channels: 1 | 2;
}

// Every message but the wasm bytes is numbered by the page and answered with a result.
export type BeatCommand =
  | { type: "load"; score: string }
  | { type: "update"; score: string }
  | { type: "rack"; sound: string; rack: string }
  | { type: "master"; rack: string }
  | { type: "zone"; zone: Zone }
  | { type: "rebuild" }
  | { type: "seek"; frame: number }
  | { type: "play" }
  | { type: "stop" }
  // null ends the loop
  | { type: "loop"; range: { start: number; end: number } | null }
  // at the song's end start over from 0 rather than stop
  | { type: "repeat"; on: boolean };

export type ToBeat = (BeatCommand & { id: number }) | { type: "wasm"; bytes: ArrayBuffer };

export interface BeatPosition {
  type: "position";
  // the song frame heard
  frame: number;
  playing: boolean;
  // the mix's peaks since the last report
  peakLeft: number;
  peakRight: number;
  // the last command handled before it, so the page can drop reports from before a seek
  after: number;
}

export type FromBeat =
  | { type: "ready" }
  // the worklet did not start
  | { type: "error"; message: string }
  | { type: "result"; id: number; result: BeatResult; length: number; latency: number }
  | BeatPosition
  | { type: "ended" }
  // the engine threw and is stopped for good; the page has to be reloaded
  | { type: "fatal"; message: string };
