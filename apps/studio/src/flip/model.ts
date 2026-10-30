// The flip as the page edits it: the document, and the pure edits of its arrangement, effects and grid.

import type { BeatStation, Job } from "../song/api.ts";

export const MAX_BARS = 512;
export const BPM_MIN = 30;
export const BPM_MAX = 300;
export const TAPE_MAX_S = 4;
const BEATS_PER_BAR = 4;

export interface Block {
  label: string;
  from_bar: number;
  to_bar: number;
}

export interface FlipEffect {
  effect: string;
  from_bar: number;
  to_bar: number;
  params: Record<string, number>;
}

export type BassMode = "original" | "ours" | "off";
export type HatsMode = "original" | "removed" | "ours";

export interface FlipDoc {
  format: 1;
  source: string;
  grid: { bpm: number; downbeat_s: number; swing: number };
  bass808: { mode: BassMode; params: Record<string, number> };
  hats: { mode: HatsMode; remove: string[]; patterns: string[] };
  gains: { drums: number; other: number; bass: number };
  arrangement: Block[];
  effects: FlipEffect[];
}

export interface Analysis {
  bpm: number;
  downbeat_s: number;
  bars: number;
  bar_s: number;
  detune_cents: number;
  notes: number;
  low_confidence: number;
  duration_s: number;
}

export interface Layout {
  blocks: Block[];
  // one hat pattern per block, kept in step with the blocks
  patterns: string[];
}

export const totalBars = (blocks: readonly Block[]): number => blocks.reduce((n, b) => n + (b.to_bar - b.from_bar), 0);

const whole = (n: number): boolean => Number.isInteger(n);

export function moveBlock(l: Layout, i: number, dir: -1 | 1): Layout | null {
  const j = i + dir;
  if (!whole(i) || i < 0 || i >= l.blocks.length || j < 0 || j >= l.blocks.length) return null;
  const blocks = [...l.blocks];
  const patterns = [...l.patterns];
  [blocks[i], blocks[j]] = [blocks[j] as Block, blocks[i] as Block];
  [patterns[i], patterns[j]] = [patterns[j] as string, patterns[i] as string];
  return { blocks, patterns };
}

export function duplicateBlock(l: Layout, i: number): Layout | null {
  const b = l.blocks[i];
  if (!b || totalBars(l.blocks) + (b.to_bar - b.from_bar) > MAX_BARS) return null;
  const blocks = [...l.blocks];
  const patterns = [...l.patterns];
  blocks.splice(i + 1, 0, { ...b });
  patterns.splice(i + 1, 0, patterns[i] ?? "8ths");
  return { blocks, patterns };
}

export function deleteBlock(l: Layout, i: number): Layout | null {
  if (l.blocks.length <= 1 || !l.blocks[i]) return null;
  return { blocks: l.blocks.filter((_, k) => k !== i), patterns: l.patterns.filter((_, k) => k !== i) };
}

// cuts block i at the source bar `at`, which must lie strictly inside it
export function splitBlock(l: Layout, i: number, at: number): Layout | null {
  const b = l.blocks[i];
  if (!b || !whole(at) || at <= b.from_bar || at >= b.to_bar) return null;
  const blocks = [...l.blocks];
  const patterns = [...l.patterns];
  blocks.splice(i, 1, { ...b, to_bar: at }, { ...b, from_bar: at });
  patterns.splice(i + 1, 0, patterns[i] ?? "8ths");
  return { blocks, patterns };
}

export function setRange(l: Layout, i: number, from: number, to: number, sourceBars: number): Layout | null {
  const b = l.blocks[i];
  if (!b || !whole(from) || !whole(to) || from < 0 || to <= from || to > sourceBars) return null;
  const blocks = l.blocks.map((x, k) => (k === i ? { ...x, from_bar: from, to_bar: to } : x));
  if (totalBars(blocks) > MAX_BARS) return null;
  return { blocks, patterns: l.patterns };
}

// effects cut to an arrangement of `total` bars; one left with no bar is dropped
export function fitEffects(effects: readonly FlipEffect[], total: number): FlipEffect[] {
  const out: FlipEffect[] = [];
  for (const e of effects) {
    const to = Math.min(e.to_bar, total);
    if (to > e.from_bar) out.push(to === e.to_bar ? e : { ...e, to_bar: to });
  }
  return out;
}

// adds an effect on play-order bars [from, to), clamped to the arrangement
export function paintEffect(effects: readonly FlipEffect[], paint: FlipEffect, total: number): FlipEffect[] | null {
  const from = Math.max(0, Math.round(paint.from_bar));
  const to = Math.min(total, Math.round(paint.to_bar));
  if (to <= from) return null;
  return [...effects, { ...paint, from_bar: from, to_bar: to, params: { ...paint.params } }];
}

// the block and the source bar a play-order bar plays
export function playToSource(blocks: readonly Block[], playBar: number): { block: number; bar: number } | null {
  if (!whole(playBar) || playBar < 0) return null;
  let at = 0;
  for (let i = 0; i < blocks.length; i += 1) {
    const b = blocks[i] as Block;
    const len = b.to_bar - b.from_bar;
    if (playBar < at + len) return { block: i, bar: b.from_bar + (playBar - at) };
    at += len;
  }
  return null;
}

// moves the first downbeat; before zero it goes a whole bar on, which keeps the grid
export function nudgeDownbeat(downbeat: number, bpm: number, step: { ms: number } | { beats: number }): number {
  const beat = 60 / bpm;
  const delta = "ms" in step ? step.ms / 1000 : step.beats * beat;
  let next = downbeat + delta;
  const bar = beat * BEATS_PER_BAR;
  while (next < 0) next += bar;
  return Math.round(next * 1e6) / 1e6;
}

export function scaleBpm(bpm: number, factor: 0.5 | 2): number | null {
  const next = bpm * factor;
  return next < BPM_MIN || next > BPM_MAX ? null : next;
}

// whole bars that fit between the downbeat and the end of the source
export function barCount(duration: number, downbeat: number, bpm: number): number {
  const bar = (60 / bpm) * BEATS_PER_BAR;
  return Math.max(0, Math.floor((duration - downbeat) / bar + 1e-9));
}

// the longest tape stop or start the range allows, in seconds
export function tapeCap(bpm: number, bars: number): number {
  return Math.min(TAPE_MAX_S, (bars * BEATS_PER_BAR * 60) / bpm);
}

export function beatSurface(b: BeatStation): "beat" | "flip" {
  return b.kind === "file" ? "flip" : "beat";
}

function plural(n: number, one: string, few: string, many: string): string {
  const tail = n % 100;
  if (tail >= 11 && tail <= 14) return many;
  const last = n % 10;
  if (last === 1) return one;
  return last >= 2 && last <= 4 ? few : many;
}

export function useConfirm(n: number, takes: number): string {
  const head = `Сделать версию ${n} битом песни?`;
  if (takes <= 0) return head;
  const what = plural(takes, "дубль станет записанным", "дубля станут записанными", "дублей станут записанными");
  return `${head} ${takes} ${what} под другой бит.`;
}

// the bpm field shows two places: entering the shown value back is no change
export function sameBpm(a: number, b: number): boolean {
  return Math.round(a * 100) === Math.round(b * 100);
}

const OURS_BPM = [40, 220] as const;
const MAX_PLAY_S = 15 * 60;

// the refusals of the server's draft check that the page can see for itself
export function draftProblem(d: FlipDoc): string | null {
  const bpm = d.grid.bpm;
  if ((d.bass808.mode === "ours" || d.hats.mode === "ours") && !(bpm >= OURS_BPM[0] && bpm <= OURS_BPM[1])) {
    return `Наши 808 и хэты играют только на ${OURS_BPM[0]}–${OURS_BPM[1]} BPM, а в сетке ${bpm}.`;
  }
  if (bpm > 0 && (totalBars(d.arrangement) * BEATS_PER_BAR * 60) / bpm > MAX_PLAY_S) {
    return `Расстановка играет дольше ${MAX_PLAY_S / 60} минут: убери такты или поправь темп.`;
  }
  return null;
}

export type RenderOutcome = "failed" | "play" | "ready" | "missing";

// what a finished render means for the page: parked, it stays ready and does not start playing
export function renderOutcome(r: { failed: boolean; present: boolean; parked: boolean }): RenderOutcome {
  if (r.failed) return "failed";
  if (!r.present) return "missing";
  return r.parked ? "ready" : "play";
}

// a job that is over with a stamp newer than the one noted before it was started
export function finishedSince(stamp: number | null, job: Job | undefined): boolean {
  if (!job || job.finished === null) return false;
  if (job.state === "queued" || job.state === "running" || job.state === "cancelling") return false;
  return stamp === null || job.finished > stamp;
}

export interface AdoptState {
  seqBefore: number;
  seqNow: number;
  edited: boolean;
  saving: boolean;
  failed: boolean;
}

// the server's draft may replace the page's only when the page has nothing newer
export function mayAdoptDraft(s: AdoptState): boolean {
  return s.seqBefore === s.seqNow && !s.edited && !s.saving && !s.failed;
}

const FLIP_RENDER = /^work\/flips\/[^/]+\/flip-(\d+)\.wav$/;

// the source file a mounted flip surface belongs to: a render of it keeps the tag
export function flipTag(tag: string | null, file: string): string {
  return FLIP_RENDER.test(file) && tag !== null ? tag : file;
}

// the song's beat moved to another source: the mounted flip surface is not its flip
export function flipStale(tag: string | null, file: string): boolean {
  return tag !== null && !FLIP_RENDER.test(file) && file !== tag;
}

export function beatMeta(file: string | null, bpm: number | null): { text: string; title: string } {
  const tail = bpm === null ? "" : ` · ${Math.round(bpm)} BPM`;
  if (!file) return { text: `файл${tail}`, title: "" };
  const render = FLIP_RENDER.exec(file);
  if (render) return { text: `флип · версия ${render[1]}${tail}`, title: file };
  return { text: `файл · ${file.split("/").pop()}${tail}`, title: file };
}
