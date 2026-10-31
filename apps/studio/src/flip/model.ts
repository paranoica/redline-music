// The flip as the page edits it: the document, and the pure edits of its arrangement, effects and grid.

import type { BeatStation, Job } from "../song/api.ts";

export const MAX_BARS = 512;
export const BPM_MIN = 30;
export const BPM_MAX = 300;
export const TAPE_MAX_S = 4;
const BEATS_PER_BAR = 4;

export interface FlipNote {
  step: number;
  len: number;
  note: number;
  vel: number;
}

export const DRUM_ROWS = ["kick", "snare", "clap", "hat", "hat_open", "perc"] as const;
export const STEPS_PER_BAR = 16;
const DEFAULT_PATTERN = "8ths";
const TAPE_DEFAULT_S = 0.5;

export interface Block {
  label: string;
  from_bar: number;
  to_bar: number;
  // steps from the block's start; absent plays the source notes, [] is silent
  notes?: FlipNote[];
  // row -> bars*16 characters of "x" and "."
  drums?: Record<string, string>;
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
  gains: { drums: number; other: number; bass: number; ours?: number };
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
  // play-order effects; when given, an op returns them remapped to the new blocks
  effects?: FlipEffect[];
  // the tempo the tape effects are capped at; without it they are left as they are
  bpm?: number;
}

// what a block op returns: `map[old]` is where each old block went (null: gone), `at` the block
// the op made or moved
export interface BlockEdit extends Layout {
  map: (number | null)[];
  at: number;
}

export interface Refusal {
  ok: false;
  reason: string;
}

export const totalBars = (blocks: readonly Block[]): number => blocks.reduce((n, b) => n + (b.to_bar - b.from_bar), 0);

const whole = (n: number): boolean => Number.isInteger(n);

const barsOf = (b: Block): number => b.to_bar - b.from_bar;

export function cloneBlock(b: Block): Block {
  const out: Block = { ...b };
  if (b.notes) out.notes = b.notes.map((n) => ({ ...n }));
  if (b.drums) out.drums = { ...b.drums };
  return out;
}

// one pattern per block: cut, or padded with the default
export function fitPatterns(patterns: readonly string[], count: number): string[] {
  return Array.from({ length: count }, (_, k) => patterns[k] ?? DEFAULT_PATTERN);
}

// a loaded document with hats.patterns as long as its arrangement, in every hats mode
export function normalisePatterns(doc: FlipDoc): FlipDoc {
  return { ...doc, hats: { ...doc.hats, patterns: fitPatterns(doc.hats.patterns, doc.arrangement.length) } };
}

const cloneEffect = (e: FlipEffect): FlipEffect => ({ ...e, params: { ...e.params } });

const starts = (blocks: readonly Block[]): number[] => {
  let at = 0;
  return blocks.map((b) => {
    const start = at;
    at += barsOf(b);
    return start;
  });
};

// Each effect follows the block that holds its from_bar (`map[old block]` = new block, null when
// the block is gone) and keeps its place inside it. It is cut at the end of that block, old and
// new, and dropped with the block or when the block no longer reaches its start. A tape effect
// is re-capped to its new range as the server checks it; a tape start time past the range goes.
export function remapEffects(
  oldBlocks: readonly Block[],
  newBlocks: readonly Block[],
  effects: readonly FlipEffect[],
  map: readonly (number | null)[],
  bpm?: number,
): FlipEffect[] {
  const oldAt = starts(oldBlocks);
  const newAt = starts(newBlocks);
  const out: FlipEffect[] = [];
  for (const e of effects) {
    const k = oldAt.findIndex((s, n) => e.from_bar >= s && e.from_bar < s + barsOf(oldBlocks[n] as Block));
    const nk = k < 0 ? null : (map[k] ?? null);
    const target = nk === null ? undefined : newBlocks[nk];
    if (k < 0 || nk === null || !target) continue;
    const offset = e.from_bar - (oldAt[k] as number);
    const oldLeft = barsOf(oldBlocks[k] as Block) - offset;
    const newLeft = barsOf(target) - offset;
    if (newLeft <= 0) continue;
    const from = (newAt[nk] as number) + offset;
    const shaped = cloneEffect(e);
    shaped.from_bar = from;
    shaped.to_bar = from + Math.min(e.to_bar - e.from_bar, oldLeft, newLeft);
    if ((e.effect === "tape_stop" || e.effect === "tape_start") && bpm !== undefined && bpm > 0) {
      const bars = shaped.to_bar - shaped.from_bar;
      const cap = tapeCap(bpm, bars);
      if ((shaped.params.duration ?? TAPE_DEFAULT_S) > cap) shaped.params.duration = cap;
      if ((shaped.params.time ?? 0) >= (bars * BEATS_PER_BAR * 60) / bpm) delete shaped.params.time;
    }
    out.push(shaped);
  }
  return out;
}

function done(l: Layout, blocks: Block[], patterns: string[], map: (number | null)[], at: number): BlockEdit {
  const out: BlockEdit = { blocks, patterns, map, at };
  if (l.bpm !== undefined) out.bpm = l.bpm;
  if (l.effects) out.effects = remapEffects(l.blocks, blocks, l.effects, map, l.bpm);
  return out;
}

const inList = (l: Layout, i: number): boolean => whole(i) && i >= 0 && i < l.blocks.length;

// Moves block i so that it sits at index j of the list without it (0..n-1), the index it ends at.
export function moveBlockTo(l: Layout, i: number, j: number): BlockEdit | Refusal {
  const n = l.blocks.length;
  if (!inList(l, i) || !whole(j) || j < 0 || j >= n) return { ok: false, reason: "Такого места нет." };
  const order = l.blocks.map((_, k) => k);
  order.splice(i, 1);
  order.splice(j, 0, i);
  const pats = fitPatterns(l.patterns, n);
  const map: (number | null)[] = [];
  order.forEach((old, now) => {
    map[old] = now;
  });
  return done(
    l,
    order.map((k) => cloneBlock(l.blocks[k] as Block)),
    order.map((k) => pats[k] as string),
    map,
    j,
  );
}

export function copyRefusal(l: Layout, i: number): string | null {
  const b = l.blocks[i];
  if (!b) return "Такого блока нет.";
  if (totalBars(l.blocks) + barsOf(b) > MAX_BARS) return `Копия не влезет: в расстановке не больше ${MAX_BARS} тактов.`;
  return null;
}

export function deleteRefusal(l: Layout, i: number): string | null {
  if (!l.blocks[i]) return "Такого блока нет.";
  if (l.blocks.length <= 1) return "Последний блок удалить нельзя.";
  return null;
}

// Puts a deep copy of block i into the gap j (0..n) of the list as it is, before block j. The
// copy has no effects: they stay on the original.
export function copyBlockTo(l: Layout, i: number, j: number): BlockEdit | Refusal {
  const why = copyRefusal(l, i);
  if (why) return { ok: false, reason: why };
  const n = l.blocks.length;
  if (!whole(j) || j < 0 || j > n) return { ok: false, reason: "Такого места нет." };
  const blocks = l.blocks.map(cloneBlock);
  const pats = fitPatterns(l.patterns, n);
  blocks.splice(j, 0, cloneBlock(l.blocks[i] as Block));
  pats.splice(j, 0, pats[i] as string);
  return done(l, blocks, pats, l.blocks.map((_, k) => (k < j ? k : k + 1)), j);
}

export function moveBlock(l: Layout, i: number, dir: -1 | 1): BlockEdit | null {
  const j = i + dir;
  if (!whole(i) || i < 0 || i >= l.blocks.length || j < 0 || j >= l.blocks.length) return null;
  const out = moveBlockTo(l, i, j);
  return "ok" in out ? null : out;
}

export function duplicateBlock(l: Layout, i: number): BlockEdit | null {
  if (!whole(i) || copyRefusal(l, i)) return null;
  const out = copyBlockTo(l, i, i + 1);
  return "ok" in out ? null : out;
}

export function deleteBlock(l: Layout, i: number): BlockEdit | null {
  if (!whole(i) || deleteRefusal(l, i)) return null;
  const pats = fitPatterns(l.patterns, l.blocks.length);
  const map = l.blocks.map((_, k) => (k === i ? null : k < i ? k : k - 1));
  return done(
    l,
    l.blocks.filter((_, k) => k !== i).map(cloneBlock),
    pats.filter((_, k) => k !== i),
    map,
    Math.min(i, l.blocks.length - 2),
  );
}

const sliceRows = (rows: Record<string, string>, from: number, to?: number): Record<string, string> =>
  Object.fromEntries(Object.entries(rows).map(([k, v]) => [k, v.slice(from, to)]));

// cuts block i at the source bar `at`, which must lie strictly inside it; notes and drums are
// cut at the same bar, a note over the cut is shortened to end there. Effects keep their bars.
export function splitBlock(l: Layout, i: number, at: number): BlockEdit | null {
  const b = l.blocks[i];
  if (!b || !whole(at) || at <= b.from_bar || at >= b.to_bar) return null;
  const cut = (at - b.from_bar) * STEPS_PER_BAR;
  const first: Block = { ...cloneBlock(b), to_bar: at };
  const second: Block = { ...cloneBlock(b), from_bar: at };
  if (b.notes) {
    first.notes = b.notes.filter((n) => n.step < cut).map((n) => ({ ...n, len: Math.min(n.len, cut - n.step) }));
    second.notes = b.notes.filter((n) => n.step >= cut).map((n) => ({ ...n, step: n.step - cut }));
  }
  if (b.drums) {
    first.drums = sliceRows(b.drums, 0, cut);
    second.drums = sliceRows(b.drums, cut);
  }
  const blocks = l.blocks.map(cloneBlock);
  blocks.splice(i, 1, first, second);
  const pats = fitPatterns(l.patterns, l.blocks.length);
  pats.splice(i + 1, 0, pats[i] as string);
  const out: BlockEdit = { blocks, patterns: pats, map: l.blocks.map((_, k) => (k <= i ? k : k + 1)), at: i + 1 };
  if (l.bpm !== undefined) out.bpm = l.bpm;
  if (l.effects) out.effects = l.effects.map(cloneEffect);
  return out;
}

// Changes block i to source bars [from, to). Notes stay on the source bar they played, so a
// front trim shifts them and drops the ones that leave the block; bars a growth adds get
// `sourceNotes(source bar)` (steps inside that bar). Drum rows are cut or padded with rests.
export function setRange(
  l: Layout,
  i: number,
  from: number,
  to: number,
  sourceBars: number,
  sourceNotes?: (bar: number) => readonly FlipNote[],
): BlockEdit | null {
  const b = l.blocks[i];
  if (!b || !whole(from) || !whole(to) || from < 0 || to <= from || to > sourceBars) return null;
  const next: Block = { ...cloneBlock(b), from_bar: from, to_bar: to };
  const steps = (to - from) * STEPS_PER_BAR;
  if (b.notes) {
    const shift = (b.from_bar - from) * STEPS_PER_BAR;
    const room = (step: number, len: number): number => Math.min(len, steps + STEPS_PER_BAR - step);
    const kept = b.notes
      .map((n) => ({ ...n, step: n.step + shift }))
      .filter((n) => n.step >= 0 && n.step < steps)
      .map((n) => ({ ...n, len: room(n.step, n.len) }));
    if (sourceNotes) {
      for (let bar = from; bar < to; bar += 1) {
        if (bar >= b.from_bar && bar < b.to_bar) continue;
        for (const n of sourceNotes(bar)) {
          const step = (bar - from) * STEPS_PER_BAR + n.step;
          if (n.step >= 0 && n.step < STEPS_PER_BAR) kept.push({ ...n, step, len: room(step, n.len) });
        }
      }
    }
    next.notes = kept;
  }
  if (b.drums) {
    next.drums = Object.fromEntries(
      Object.entries(b.drums).map(([row, v]) => [row, v.padEnd(steps, ".").slice(0, steps)]),
    );
  }
  const blocks = l.blocks.map((x, k) => (k === i ? next : cloneBlock(x)));
  if (totalBars(blocks) > MAX_BARS) return null;
  return done(l, blocks, fitPatterns(l.patterns, blocks.length), l.blocks.map((_, k) => k), i);
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

// the first bar line may sit this far before the file (the server's DOWNBEAT_LEAD_S)
export const DOWNBEAT_LEAD_S = 0.02;

// moves the first downbeat; further before the file than the lead it goes a whole bar on,
// which keeps the grid
export function nudgeDownbeat(downbeat: number, bpm: number, step: { ms: number } | { beats: number }): number {
  const beat = 60 / bpm;
  const delta = "ms" in step ? step.ms / 1000 : step.beats * beat;
  let next = downbeat + delta;
  const bar = beat * BEATS_PER_BAR;
  while (next < -DOWNBEAT_LEAD_S) next += bar;
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
  const own = d.bass808.mode === "ours" || d.hats.mode === "ours";
  const layers = d.arrangement.some((b) => Object.keys(b.drums ?? {}).length > 0 || (b.notes?.length ?? 0) > 0);
  if ((own || layers) && !(bpm >= OURS_BPM[0] && bpm <= OURS_BPM[1])) {
    const what = own ? "Наши 808 и хэты" : "Наши ноты и барабаны";
    return `${what} играют только на ${OURS_BPM[0]}–${OURS_BPM[1]} BPM, а в сетке ${bpm}.`;
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
