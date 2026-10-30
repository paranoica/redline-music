// Talks to the flip routes. The jobs and the song live beside the base, under the same prefix.

import { apiPath, apiRoot } from "../core/mount.ts";
import { ApiError } from "../song/api.ts";
import type { Job } from "../song/api.ts";
import type { Analysis, Block, FlipDoc } from "./model.ts";

export interface FlipState {
  source: string;
  sha8: string;
  analysis: Analysis | null;
  draft: FlipDoc | null;
  versions: number[];
  renders: number[];
  used: number | null;
  takes_over_other_beat: number;
  presets: string[];
  effects: Record<string, unknown>;
}

export interface Peaks {
  bar_starts_s: number[];
  peaks: [number, number][];
}

// a draft the server refused: the messages carry their paths
export class DraftError extends Error {
  readonly errors: string[];

  constructor(errors: string[]) {
    super(errors[0] ?? "draft refused");
    this.errors = errors;
  }
}

export interface FlipApiOptions {
  base?: string | undefined;
  token: string;
}

export function createFlipApi({ base, token }: FlipApiOptions) {
  const root = apiRoot(base, "/api/flip");
  const parent = root.replace(/\/[^/]*$/, "") || "/api";
  const lifetime = new AbortController();

  async function call(url: string, method: "GET" | "POST", body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { "X-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const init: RequestInit = { method, headers, signal: lifetime.signal };
    if (body !== undefined) init.body = JSON.stringify(body);
    const response = await fetch(url, init);
    if (response.ok) return response;
    const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(response.status, data && typeof data === "object" ? data : {});
  }

  const flip = (path: string, method: "GET" | "POST", body?: unknown) => call(apiPath(root, path), method, body);

  return {
    close: (): void => lifetime.abort(),
    state: async (): Promise<FlipState> => (await flip("/state", "GET")).json() as Promise<FlipState>,
    peaks: async (stem?: string): Promise<Peaks> =>
      (await flip(stem ? `/peaks?stem=${encodeURIComponent(stem)}` : "/peaks?bars=1", "GET")).json() as Promise<Peaks>,
    saveDraft: async (doc: FlipDoc): Promise<void> => {
      try {
        await flip("/draft", "POST", { flip: doc });
      } catch (error) {
        if (error instanceof ApiError && error.status === 422) {
          const list = error.body["errors"];
          throw new DraftError(Array.isArray(list) ? list.map(String) : [error.message]);
        }
        throw error;
      }
    },
    propose: async (): Promise<Block[]> => ((await (await flip("/propose", "POST", {})).json()) as { arrangement: Block[] }).arrangement,
    render: async (): Promise<number> => ((await (await flip("/render", "POST", {})).json()) as { n: number }).n,
    use: async (n: number, rev: number): Promise<void> => {
      await flip("/use", "POST", { n, rev });
    },
    audio: async (n: number): Promise<Blob> => (await flip(`/audio?n=${n}`, "GET")).blob(),
    stemAudio: async (stem: string): Promise<Blob> => (await flip(`/audio?stem=${encodeURIComponent(stem)}`, "GET")).blob(),
    rev: async (): Promise<number> => ((await (await call(apiPath(parent, "/song"), "GET")).json()) as { song: { rev: number } }).song.rev,
    jobs: async (): Promise<Job[]> => ((await (await call(apiPath(parent, "/jobs"), "GET")).json()) as { jobs: Job[] }).jobs,
    startStems: async (): Promise<void> => {
      await call(apiPath(parent, "/jobs/start"), "POST", { kind: "flip_stems" });
    },
  };
}

export type FlipApi = ReturnType<typeof createFlipApi>;

export interface Range {
  low: number;
  high: number;
  def: number | null;
}

// a param range as the server may list it: [low, high] or an object with low/high (min/max)
export function rangeOf(raw: unknown): Range | null {
  let low: unknown;
  let high: unknown;
  let def: unknown = null;
  if (Array.isArray(raw)) [low, high, def] = raw;
  else if (raw && typeof raw === "object") {
    const o = raw as Record<string, unknown>;
    low = o["low"] ?? o["min"];
    high = o["high"] ?? o["max"];
    def = o["default"] ?? o["def"] ?? null;
  }
  if (typeof low !== "number" || typeof high !== "number" || !(high > low)) return null;
  return { low, high, def: typeof def === "number" ? def : null };
}

// the ranges of one effect's params, by name
export function effectRanges(effects: Record<string, unknown>, effect: string): [string, Range][] {
  let raw = effects[effect];
  if (raw && typeof raw === "object" && !Array.isArray(raw) && "params" in raw) raw = (raw as { params: unknown }).params;
  const out: [string, Range][] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const name = (item as { name?: unknown } | null)?.name;
      const r = rangeOf(item);
      if (typeof name === "string" && r) out.push([name, r]);
    }
  } else if (raw && typeof raw === "object") {
    for (const [name, value] of Object.entries(raw as Record<string, unknown>)) {
      const r = rangeOf(value);
      if (r) out.push([name, r]);
    }
  }
  return out;
}
