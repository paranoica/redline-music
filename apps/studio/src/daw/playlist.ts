// The playlist of an own beat, FL style: one lane per pattern, a clip in each section that plays
// it. A click or a drag over empty sections paints the pattern there, a clip goes by a right
// click, its ✕ or Delete. Pure: the screen draws it and the editor makes the edits.

import {
  drumSteps,
  isNoteRow,
  locate,
  makeUnique,
  MAX_PATTERNS,
  notesOf,
  placePattern,
  playingAt,
  sectionStarts,
  songBars,
  STEPS_PER_BAR,
  usage,
  type NamedEdit,
  type Score,
} from "../beat/model.ts";
import type { StepAt } from "./pattern.ts";
import { drumRow, keyedBlock, type BassMark, type HitMark, type LaneKind } from "./view.ts";

const own = (o: object, key: string): boolean => Object.hasOwn(o, key);

// daw.css tokens, so a pattern's lane matches the sounds' colours in the rack
export const PATTERN_COLOURS: readonly string[] = ["--c-kick", "--c-hat", "--c-snare", "--c-keys", "--c-808", "--c-perc", "--c-ohat", "--c-clap"];

export function patternColour(index: number): string {
  const n = PATTERN_COLOURS.length;
  const i = Number.isInteger(index) && index > 0 ? index % n : 0;
  return PATTERN_COLOURS[i] as string;
}

// ---------- clips ----------

export interface PlaylistClip {
  section: number;
  fromBar: number;
  bars: number;
  // the pattern is the section's fill: a mark on its last bar, not a clip
  fill: boolean;
}

export function patternClips(score: Score, pattern: string): PlaylistClip[] {
  if (!own(score.patterns, pattern)) return [];
  const starts = sectionStarts(score);
  return score.sections.flatMap((s, i) => {
    const bars = typeof s.bars === "number" ? s.bars : 0;
    if (bars < 1) return [];
    const fromBar = starts[i] as number;
    const out: PlaylistClip[] = [];
    if ((s.play ?? []).includes(pattern)) out.push({ section: i, fromBar, bars, fill: false });
    if (s.fill === pattern) out.push({ section: i, fromBar: fromBar + bars - 1, bars: 1, fill: true });
    return out;
  });
}

export function sectionAtBar(score: Score, bar: number): number | null {
  if (!Number.isFinite(bar)) return null;
  return locate(score, Math.floor(bar))?.section ?? null;
}

// the sections a drag went over: from the one it went down on to the one under the pointer
export function dragSections(score: Score, from: number, bar: number): number[] {
  const last = score.sections.length - 1;
  if (last < 0) return [];
  const to = bar < 0 ? 0 : (sectionAtBar(score, bar) ?? (bar >= songBars(score) ? last : from));
  const lo = Math.max(0, Math.min(from, to));
  const hi = Math.min(last, Math.max(from, to));
  return Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
}

// ---------- painting ----------

export interface PaintPlan {
  // the same score when nothing was painted
  score: Score;
  painted: number[];
  refused: { section: number; reason: string }[];
}

// `on` puts the pattern into the play of the sections that lack it, off takes it out of those
// that have it; a refused section is left as it was
export function paintSections(score: Score, pattern: string, sections: readonly number[], on = true): PaintPlan {
  const order = [...new Set(sections)].sort((a, b) => a - b);
  let next = score;
  const painted: number[] = [];
  const refused: PaintPlan["refused"] = [];
  for (const i of order) {
    const plays = (score.sections[i]?.play ?? []).includes(pattern);
    if (score.sections[i] !== undefined && own(score.patterns, pattern) && plays === on) continue;
    const made = placePattern(next, pattern, i, on);
    if (!made.ok) {
      refused.push({ section: i, reason: made.reason });
      continue;
    }
    next = made.score;
    painted.push(i);
  }
  return { score: next, painted, refused };
}

// ---------- a clip picked ----------

export interface ClipPick {
  pattern: string;
  section: number;
}

export function clipAlive(score: Score, pick: ClipPick): boolean {
  const s = Number.isInteger(pick.section) ? score.sections[pick.section] : undefined;
  return s !== undefined && own(score.patterns, pick.pattern) && (s.play ?? []).includes(pick.pattern);
}

export type DeleteTarget = { kind: "clip"; pattern: string; section: number } | { kind: "section"; index: number } | null;

// a picked clip owns Delete, even when it is gone: then nothing goes rather than a section
export function deleteTarget(clip: ClipPick | null, score: Score | null, lane: LaneKind | null, picked: number | null, blocks: number): DeleteTarget {
  if (clip !== null) return score !== null && clipAlive(score, clip) ? { kind: "clip", pattern: clip.pattern, section: clip.section } : null;
  const index = keyedBlock(lane, picked, blocks);
  return index === null ? null : { kind: "section", index };
}

export function uniqueClip(score: Score, pick: ClipPick): NamedEdit {
  if (!clipAlive(score, pick)) return { ok: false, reason: "Этого клипа уже нет." };
  if (usage(score, pick.pattern).length < 2) return { ok: false, reason: "Паттерн играет только здесь: он и так свой." };
  if (Object.keys(score.patterns).length >= MAX_PATTERNS) return { ok: false, reason: `Паттернов уже ${MAX_PATTERNS}, больше нельзя.` };
  const made = makeUnique(score, pick.section, pick.pattern);
  return { ok: true, score: made.score, name: made.name };
}

// ---------- the cut ----------

// the bar inside a section it is cut after: the bar line nearest a pointer inside it, else the
// middle; a one-bar section gets 1, which the model refuses with its reason
export function sectionCutAt(fromBar: number, bars: number, pointerBar: number | null): number {
  if (bars < 2) return 1;
  const over = pointerBar !== null && pointerBar > fromBar && pointerBar < fromBar + bars;
  const k = over ? Math.round(pointerBar - fromBar) : Math.floor(bars / 2);
  return Math.min(bars - 1, Math.max(1, k));
}

// ---------- the marks in a clip ----------

// What a pattern plays where it is played, not as a fill: every note sound's notes and every drum
// sound's hits, a drum the 808 and drum marks do not know on the middle row.
export function clipMarks(score: Score, pattern: string): { notes: BassMark[]; hits: HitMark[] } {
  const notes: BassMark[] = [];
  const hits: HitMark[] = [];
  if (!own(score.patterns, pattern)) return { notes, hits };
  const total = songBars(score);
  for (let bar = 0; bar < total; bar++) {
    for (const r of playingAt(score, bar)) {
      if (r.pattern !== pattern || r.fill || r.muted || r.replaced) continue;
      const row = score.patterns[pattern]?.rows[r.sound];
      if (row === undefined) continue;
      const from = r.patternBar * STEPS_PER_BAR;
      if (isNoteRow(row)) {
        for (const n of notesOf(score, pattern, r.sound)) {
          if (n.step >= from && n.step < from + STEPS_PER_BAR) notes.push({ bar, step: n.step - from, len: n.len, midi: n.midi });
        }
      } else {
        const lane = drumRow(score.sounds[r.sound]?.instrument ?? "") ?? 1;
        drumSteps(score, pattern, r.sound)
          .slice(from, from + STEPS_PER_BAR)
          .forEach((hit, step) => {
            if (hit !== ".") hits.push({ bar, step, row: lane });
          });
      }
    }
  }
  notes.sort((a, b) => a.bar - b.bar || a.step - b.step || a.midi - b.midi);
  hits.sort((a, b) => a.bar - b.bar || a.row - b.row || a.step - b.step);
  return { notes, hits };
}

// ---------- the rack's pager ----------

// While a song or a pattern plays, the rack's page follows the playing bar; a turn by hand keeps
// the page where the user put it until the next play.
export class PagerFollow {
  #follow = true;
  #playing = false;

  // the pattern bar to show, null to leave the page as it is
  next(playing: boolean, at: StepAt | null): number | null {
    if (playing && !this.#playing) this.#follow = true;
    this.#playing = playing;
    return playing && this.#follow && at !== null ? at.bar : null;
  }

  turned(): void {
    this.#follow = false;
  }
}
