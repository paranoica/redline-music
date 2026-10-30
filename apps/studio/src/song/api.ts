// Talks to the song server. Every request carries the page token; the mixing and beat
// surfaces get their own base paths under the same prefix.

import { apiPath, apiRoot } from "../core/mount.ts";

export interface SongFile {
  name: string;
  rev: number;
  [key: string]: unknown;
}

export interface BeatStation {
  kind: "own" | "file";
  seed?: number;
  bpm: number | null;
  file: string | null;
  rendered?: boolean;
}

export interface TextStation {
  seed: number | null;
  version?: number | null;
  versions?: number[];
  pending?: string | null;
  command: string;
}

export interface VoiceStation {
  kind: "takes" | "suno";
  folder?: string;
  takes?: string[];
  chosen?: string | null;
  file?: string;
  separated?: boolean;
}

export interface MixStation {
  ready: boolean;
  reason: string | null;
  variant: number | null;
}

export interface ResultStation {
  folder: string | null;
}

export interface Stations {
  beat: BeatStation;
  text: TextStation;
  voice: VoiceStation;
  mix: MixStation;
  result: ResultStation;
}

export interface SongReply {
  song: SongFile;
  stations: Stations;
}

export type SongChange =
  | { text: { seed: number; version?: number } }
  | { voice_chosen: string }
  | { beat: { own: number } }
  | { mix_variant: number | null };

export interface TextReply {
  seed: number;
  version: number;
  title: string;
  text: string;
}

export interface TextProblem {
  kind: string;
  place: string;
  message: string;
}

export interface RecSnapshot {
  stage: "closed" | "building" | "launching" | "relaunching" | "analysing" | "level_check" | "ready" | "calibrating" | "failed";
  error: string | null;
  out_of_sync: boolean;
  recording: boolean;
  stopping: boolean;
  take: string | null;
  seconds: number;
  peak_db: number | null;
  clip: boolean;
  dropouts: number;
  monitor_ms: number | null;
  round_trip: { label: string; source: "calibrated" | "estimate" } | null;
  rate: number | null;
  quantum: number | null;
  dropped_effects: string[];
  notices: { code: string; values: Record<string, unknown> }[];
  age_ms: number;
}

export interface RecReply {
  code: string;
  values?: Record<string, unknown>;
}

export interface TakeMetrics {
  seconds: number;
  timing_ms: number | null;
  clip_runs: number;
  floor_below_voice_db: number | null;
  dropouts: number | null;
  dropout_ms: number | null;
  bleed: number;
  deep_distance?: number | null;
  notes?: string[];
}

export interface RankedTake {
  name: string;
  file: string;
  rank: number | null;
  total: number;
  points: Record<string, number>;
  headphones: boolean;
  metrics: TakeMetrics;
}

export interface SkippedTake {
  name: string;
  code: string;
  reason: string;
  values: Record<string, number>;
}

export interface Ranking {
  beat: string;
  rate: number | null;
  ranked: boolean;
  takes: RankedTake[];
  skipped: SkippedTake[];
}

export type JobKind = "separate" | "rank" | "auto" | "result" | "beat_render" | "flip_stems" | "flip_render";
// the kinds the page starts through /jobs/start
export type StartKind = Exclude<JobKind, "beat_render" | "flip_render">;
export type JobState = "queued" | "running" | "cancelling" | "done" | "failed" | "cancelled";

export interface Job {
  kind: JobKind;
  state: JobState;
  reason: string | null;
  started: number | null;
  finished: number | null;
}

export interface VariantsReply {
  auto: Record<string, unknown> & { distance_before?: number | null; distance_after?: number | null; at_edge?: string[] };
  variants: { settings: Record<string, unknown>; axis: string; direction: string; value: number; distance: number | null }[];
  seed: number;
}

// a refusal from the server, with the body it sent
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    super(typeof body["error"] === "string" ? body["error"] : `${status}`);
    this.status = status;
    this.code = typeof body["code"] === "string" ? body["code"] : "";
    this.body = body;
  }
}

// the song a 412 carries: song and stations sit at the top level of its body
export function staleSong(body: Record<string, unknown>): SongReply | null {
  const song = body["song"];
  const stations = body["stations"];
  if (song && typeof song === "object" && stations && typeof stations === "object") {
    return { song: song as SongFile, stations: stations as Stations };
  }
  return null;
}

// an older read never replaces a newer song
export function acceptSong(held: SongReply | null, next: SongReply): SongReply {
  return held && next.song.rev < held.song.rev ? held : next;
}

export interface SongApiOptions {
  base?: string | undefined;
  token: string;
}

export function createSongApi({ base, token }: SongApiOptions) {
  const root = apiRoot(base, "/api");
  const lifetime = new AbortController();

  async function call(method: "GET" | "POST", path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { "X-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const init: RequestInit = { method, headers, signal: lifetime.signal };
    if (body !== undefined) init.body = JSON.stringify(body);
    const response = await fetch(apiPath(root, path), init);
    if (response.ok) return response;
    const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(response.status, data && typeof data === "object" ? data : {});
  }

  const json = async <T>(r: Promise<Response>): Promise<T> => (await r).json() as Promise<T>;

  return {
    // the paths the mounted surfaces build theirs from
    mixBase: apiPath(root, "/mix"),
    beatBase: apiPath(root, "/beat"),
    flipBase: apiPath(root, "/flip"),
    close: (): void => lifetime.abort(),
    song: () => json<SongReply>(call("GET", "/song")),
    setSong: (rev: number, set: SongChange) => json<SongReply>(call("POST", "/song", { rev, set })),
    text: (version: number) => json<TextReply>(call("GET", `/text?version=${version}`)),
    saveText: (seed: number, base: number, text: string) =>
      json<{ version: number }>(call("POST", "/text/save", { seed, base, text })),
    recState: () => json<RecSnapshot>(call("GET", "/rec/state")),
    recOpen: () => json<RecReply>(call("POST", "/rec/open", {})),
    recToggle: () => json<RecReply>(call("POST", "/rec/toggle", {})),
    recDiscard: () => json<RecReply>(call("POST", "/rec/discard", {})),
    recCalibrate: (mode: "cup" | "loopback" | "speakers") => json<RecReply>(call("POST", "/rec/calibrate", { mode })),
    recClose: () => json<RecReply>(call("POST", "/rec/close", {})),
    takes: () => json<{ ranking: Ranking | null; job: Job | null }>(call("GET", "/takes")),
    takeAudio: async (name: string, withBeat = false): Promise<Blob> =>
      (await call("GET", `/takes/audio?name=${encodeURIComponent(name)}${withBeat ? "&with=beat" : ""}`)).blob(),
    jobs: async (): Promise<Job[]> => ((await json<{ jobs: Job[] }>(call("GET", "/jobs"))).jobs),
    startJob: (kind: StartKind, seed?: number) =>
      json<unknown>(call("POST", "/jobs/start", seed === undefined ? { kind } : { kind, seed })),
    cancelJob: (kind: JobKind) => json<unknown>(call("POST", "/jobs/cancel", { kind })),
    // null before the first auto run
    // null before the first auto run; "stale" when they were made for another voice or beat
    variants: async (): Promise<VariantsReply | null | "stale"> => {
      try {
        return await json<VariantsReply>(call("GET", "/mix/variants"));
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        if (error instanceof ApiError && error.code === "stale_variants") return "stale";
        throw error;
      }
    },
    // whether the mixing studio can be built: "no_voice" while no voice is chosen
    mixReady: async (): Promise<"ready" | "no_voice"> => {
      try {
        await call("GET", "/mix/schema");
        return "ready";
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && error.code === "no_voice") return "no_voice";
        throw error;
      }
    },
  };
}

export type SongApi = ReturnType<typeof createSongApi>;
