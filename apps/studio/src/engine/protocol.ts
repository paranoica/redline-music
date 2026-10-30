// Messages between the page and the AudioWorklet's engine.

import type { Lane, Track } from "../types.ts";

export type Source = "result" | "original" | "reference" | "render";
export const SOURCES: readonly Source[] = ["result", "original", "reference", "render"];

export type ToEngine =
  | { type: "stems"; frames: number; vocal: Float32Array; beat: Float32Array; original: Float32Array }
  // a source other than the stems: the exact render (aligned with the track) or a reference,
  // which loops its own span [start, start + length) frames
  | { type: "source"; name: "reference" | "render"; data: Float32Array | null; start: number; length: number }
  | { type: "rack"; track: Track; version: number; json: string; lanes: Lane[] }
  | { type: "set"; track: Track; version: number; slot: number; param: string; value: number }
  | { type: "lanes"; version: number; lanes: Lane[] }
  | { type: "play"; playing: boolean }
  // seeks and loops are numbered by the page, so it can tell the reports of the old spot
  | { type: "seek"; frame: number; seq: number }
  | { type: "loop"; on: boolean; start: number; end: number; seq: number }
  | { type: "listen"; gains: Record<Source, number> };

export interface Level {
  peak: number;
  rms: number;
}

export interface Levels {
  type: "levels";
  // the frame of the track heard at the end of the window
  frame: number;
  // the last seek or loop the frame has caught up with
  seq: number;
  playing: boolean;
  vocal: Level;
  beat: Level;
  master: Level & { gr: number | null };
  // the result's loudness over the last seconds played, for the A/B
  lufs: number;
  latency: number;
}

export type FromEngine =
  | { type: "ready" }
  | { type: "rack"; track: Track; version: number; latency: number }
  // fatal: the engine threw and is stopped for good; the page has to be reloaded
  | { type: "error"; track?: Track; version?: number; message: string; fatal?: boolean }
  | Levels
  | { type: "ended" };
