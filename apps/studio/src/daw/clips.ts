// The blocks of the beat lane, from what the server gives for each kind of beat.

import type { Score } from "../beat/model.ts";
import { sectionStarts, usage } from "../beat/model.ts";
import type { FlipDoc } from "../flip/model.ts";
import type { Grid } from "./time.ts";
import { barSeconds, toBars } from "./time.ts";

export interface Clip {
  // the section or the arrangement block it shows
  index: number;
  label: string;
  // play-order bars
  fromBar: number;
  bars: number;
  patterns: string[];
  fill: string | null;
  // a flip block's source bars, [from, to)
  source: { fromBar: number; toBar: number } | null;
}

export function ownClips(score: Score): Clip[] {
  const starts = sectionStarts(score);
  return score.sections.map((s, i) => ({
    index: i,
    label: s.name,
    fromBar: starts[i] as number,
    bars: typeof s.bars === "number" ? s.bars : 0,
    patterns: [...(s.play ?? [])],
    fill: s.fill ?? null,
    source: null,
  }));
}

export function flipClips(doc: Pick<FlipDoc, "arrangement">): Clip[] {
  let at = 0;
  return doc.arrangement.map((b, i) => {
    const bars = b.to_bar - b.from_bar;
    const clip: Clip = { index: i, label: b.label, fromBar: at, bars, patterns: [], fill: null, source: { fromBar: b.from_bar, toBar: b.to_bar } };
    at += bars;
    return clip;
  });
}

// The source bar a flip block is cut at: the bar line nearest to the pointer when it is over the
// block, else the middle. Null for a block of one bar, which has no inside.
export function splitAt(clip: Pick<Clip, "fromBar" | "bars" | "source">, pointerBar: number | null): number | null {
  const src = clip.source;
  if (!src) return null;
  const bars = src.toBar - src.fromBar;
  if (bars < 2) return null;
  const over = pointerBar !== null && pointerBar > clip.fromBar && pointerBar < clip.fromBar + clip.bars;
  const k = over ? Math.round(pointerBar - clip.fromBar) : Math.floor(bars / 2);
  return src.fromBar + Math.min(bars - 1, Math.max(1, k));
}

// a file with no score or arrangement: one block from its first sample to its last
export function fileClips(g: Grid, duration: number, label: string): Clip[] {
  if (!(duration > 0)) return [];
  return [{ index: 0, label, fromBar: toBars(g, 0), bars: duration / barSeconds(g), patterns: [], fill: null, source: null }];
}

export interface SharedPattern {
  pattern: string;
  sections: number[];
}

// the patterns an edit would change in more than one place, by name
export function sharedPatterns(score: Score): SharedPattern[] {
  const names = new Set(score.sections.flatMap((s) => [...(s.play ?? []), ...(s.fill ? [s.fill] : [])]));
  return [...names]
    .sort()
    .map((pattern) => ({ pattern, sections: usage(score, pattern) }))
    .filter((p) => p.sections.length > 1);
}

// the places an edit of the pattern lands in
export function shareCount(score: Score, pattern: string): number {
  return usage(score, pattern).length;
}
