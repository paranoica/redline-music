// Talks to the beat page's server. The token is given by the page, as on the studio page.

import { apiPath, apiRoot } from "../core/mount.ts";
import type { EventTable, InstrumentSpec, Score } from "./model.ts";
import type { EffectSpec } from "../types.ts";

// the server's answer to a call it refused, with the score's errors when it gave them
export class Refused extends Error {
  readonly status: number;
  readonly errors: string[];

  constructor(status: number, message: string, errors: string[]) {
    super(message);
    this.status = status;
    this.errors = errors;
  }
}

const json = async <T>(r: Promise<Response>): Promise<T> => (await r).json() as Promise<T>;

export interface Caution {
  kind: string;
  where: string;
  message: string;
  source: string;
  text: string;
}

export interface Mark {
  seed: number;
  bar: number;
  verdict: "good" | "bad";
  note: string;
  score: string;
  time: string;
}

export interface ZoneInfo {
  library: string;
  file: string;
  root: number;
  vel: [number, number];
  rate: number;
  channels: number;
  frames: number;
}

export interface Library {
  name: string;
  sounds: string[];
  licence: string | null;
  commercial: boolean;
  kind: string | null;
  fetched: boolean;
  zones: ZoneInfo[];
  error: string | null;
}

export interface BeatSession {
  seed: number;
  score_file: string;
  score: Score;
  recipe: Record<string, unknown>;
  instruments: InstrumentSpec[];
  effects: EffectSpec[];
  rate: number;
  events: EventTable | null;
  events_error: string | null;
  marks: Mark[];
  libraries: Library[];
}

export interface RenderStatus {
  state: "idle" | "running" | "done" | "failed";
  score_file: string | null;
  error: string | null;
  frames?: number;
  seconds?: number;
  sounds?: string[];
  non_commercial?: string[];
}

export interface ZoneData {
  pcm: ArrayBuffer;
  rate: number;
  channels: number;
  frames: number;
  root: number;
}

const header = (r: Response, name: string): number => {
  const v = Number(r.headers.get(name));
  if (!Number.isFinite(v)) throw new Error(`the zone came without ${name}`);
  return v;
};

export interface BeatApiOptions {
  base?: string | undefined;
  token: string;
}

export function createApi({ base, token }: BeatApiOptions) {
  const root = apiRoot(base, "/api/beat");
  const lifetime = new AbortController();

  async function call(method: "GET" | "POST", path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { "X-Token": token };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const init: RequestInit = { method, headers, signal: lifetime.signal };
    if (body !== undefined) init.body = JSON.stringify(body);
    const response = await fetch(apiPath(root, path), init);
    if (response.ok) return response;
    const data = (await response.json().catch(() => ({}))) as { error?: string; errors?: unknown };
    const errors = Array.isArray(data.errors) ? data.errors.filter((e): e is string => typeof e === "string") : [];
    throw new Refused(response.status, data.error ?? `${response.status}`, errors);
  }

  return {
    // cancels every request still out
    close: (): void => lifetime.abort(),
    session: () => json<BeatSession>(call("GET", "/session")),
    check: (score: Score) => json<{ errors: string[]; warnings: Caution[] }>(call("POST", "/check", { score })),
    save: (score: Score, base: string) => json<{ file: string; warnings: Caution[] }>(call("POST", "/save", { score, base })),
    mark: (bar: number, verdict: "good" | "bad", note: string, base: string) =>
      json<Mark>(call("POST", "/mark", { bar, verdict, note, base })),
    render: (scoreFile: string) => json<RenderStatus>(call("POST", "/render", { score_file: scoreFile })),
    renderStatus: () => json<RenderStatus>(call("GET", "/render/status")),
    renderFile: async (file: string): Promise<Blob> => (await call("GET", `/render?file=${encodeURIComponent(file)}`)).blob(),
    zone: async (library: string, file: string): Promise<ZoneData> => {
      const r = await call("GET", `/zone?library=${encodeURIComponent(library)}&file=${encodeURIComponent(file)}`);
      return {
        pcm: await r.arrayBuffer(),
        rate: header(r, "X-Rate"),
        channels: header(r, "X-Channels"),
        frames: header(r, "X-Frames"),
        root: header(r, "X-Root"),
      };
    },
  };
}

export type Api = ReturnType<typeof createApi>;
