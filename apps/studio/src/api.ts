// Talks to the studio server. The token comes once from the page address and is then
// dropped from it, so it does not stay in the history or a screenshot.

import type { Analysis, Lane, Session, Settings, Track } from "./types.ts";

const token = new URLSearchParams(location.search).get("t") ?? "";
history.replaceState(null, "", location.pathname);

export class Superseded extends Error {}

async function call(method: "GET" | "POST", path: string, body?: unknown, signal?: AbortSignal): Promise<Response> {
  const headers: Record<string, string> = { "X-Token": token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const init: RequestInit = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  if (signal) init.signal = signal;
  const response = await fetch(path, init);
  if (response.ok) return response;
  const data = (await response.json().catch(() => ({}))) as { error?: string };
  if (response.status === 409) throw new Superseded(data.error ?? "superseded");
  throw new Error(data.error ?? `${response.status}`);
}

// "-inf" for a silent stem reads as -Infinity, a missing header as null
function headerNumber(response: Response, name: string): number | null {
  const raw = response.headers.get(name);
  if (raw === null) return null;
  if (/^-inf/i.test(raw.trim())) return -Infinity;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

export interface Audio {
  bytes: ArrayBuffer;
  lufs: number | null;
  ms: number | null;
}

async function audio(response: Response): Promise<Audio> {
  return {
    bytes: await response.arrayBuffer(),
    lufs: headerNumber(response, "X-Lufs"),
    ms: headerNumber(response, "X-Render-Ms"),
  };
}

export interface SessionReply extends Omit<Session, "racks"> {
  racks: Record<Track, string> | null;
  racks_error?: string;
}

export const api = {
  session: async (): Promise<SessionReply> => (await call("GET", "/api/session")).json() as Promise<SessionReply>,
  stem: async (name: "vocal" | "beat" | "original"): Promise<Audio> => audio(await call("GET", `/api/stem?name=${name}`)),
  analysis: async (settings: Settings, signal: AbortSignal) =>
    (await call("POST", "/api/analysis", { settings }, signal)).json() as Promise<{
      analysis: Analysis;
      racks: Record<Track, string>;
      lanes: [number, string, [number, number][]][] | Lane[];
    }>,
  render: async (settings: Settings, seq: number, signal: AbortSignal): Promise<Audio> =>
    audio(await call("POST", "/api/render", { settings, seq }, signal)),
  save: async (settings: Settings) => (await call("POST", "/api/save", { settings })).json() as Promise<{ path: string }>,
  preset: async (name: string, settings: Settings) =>
    (await call("POST", "/api/preset", { name, settings })).json() as Promise<{ name: string }>,
  reference: async (index: number): Promise<Audio> => audio(await call("GET", `/api/reference?i=${index}`)),
};
