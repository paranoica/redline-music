// What the song screen shows, worked out from the server's song before any DOM: the facts it
// acts on, the lanes for them, the take lanes it keeps on screen, what a press on the ruler
// means, the chip's words and the marks drawn on the 808 and drum lanes.

import { drumSteps, isNoteRow, notesOf, playingAt, STEPS_PER_BAR, type Score } from "../beat/model.ts";
import type { BeatStation, Job, SongReply } from "../song/api.ts";
import type { NextStep, SongFacts } from "./transport.ts";

export type BeatKind = "own" | "file" | "flip";

const FLIP_RENDER = /^work\/flips\/[0-9a-f]{8}\/flip-[1-9][0-9]*\.wav$/;

export function beatKindOf(b: BeatStation): BeatKind {
  if (b.kind === "own") return "own";
  return b.file !== null && FLIP_RENDER.test(b.file) ? "flip" : "file";
}

export interface ScreenFacts extends SongFacts {
  beatKind: BeatKind;
  bpm: number | null;
  takeNames: string[];
  // an own beat's draft or newest score is not rendered yet
  beatDirty: boolean;
}

// the server's facts sit beside the stations or inside them; the stations alone are the fallback
function given(reply: SongReply): Record<string, unknown> {
  if (reply.facts && typeof reply.facts === "object") return reply.facts;
  const nested = (reply.stations as unknown as Record<string, unknown>)["facts"];
  if (nested && typeof nested === "object") return nested as Record<string, unknown>;
  return reply.stations as unknown as Record<string, unknown>;
}

const bool = (raw: Record<string, unknown>, key: string): boolean | null => (typeof raw[key] === "boolean" ? (raw[key] as boolean) : null);

const running = (j: Job): boolean => j.state === "queued" || j.state === "running" || j.state === "cancelling";

// `flipAnalysed`: whether the flip state has an analysis, null when it was not asked
export function factsOf(reply: SongReply, jobs: readonly Job[], extra: { flipAnalysed: boolean | null }): ScreenFacts {
  const s = reply.stations;
  const raw = given(reply);
  const declared = beatKindOf(s.beat);
  // a beat file already read into a flip is edited as one; its first render becomes the beat
  const kind: BeatKind = declared === "file" && extra.flipAnalysed === true ? "flip" : declared;
  const suno = s.voice.kind === "suno";
  const takeNames = suno ? [] : [...(s.voice.takes ?? [])];
  const refsList = reply.song["references"];
  const refsGiven = raw["refs"];
  const voiceGiven = raw["voice_kind"];
  return {
    beatKind: kind,
    bpm: typeof s.beat.bpm === "number" && s.beat.bpm > 0 ? s.beat.bpm : null,
    takeNames,
    hasBeatAudio: bool(raw, "beat_audio") ?? (kind === "own" ? s.beat.rendered === true : s.beat.file !== null),
    beatAnalysed: bool(raw, "beat_analysed") ?? (declared === "file" ? extra.flipAnalysed === true : true),
    takes: takeNames.length,
    chosen: suno ? null : (s.voice.chosen ?? null),
    voiceKind: voiceGiven === "own" || voiceGiven === "suno" ? voiceGiven : suno ? "suno" : "own",
    studioReady: bool(raw, "studio_ready") ?? s.mix.ready,
    mixStale: bool(raw, "mix_stale") ?? true,
    refsCount: typeof refsGiven === "number" ? refsGiven : Array.isArray(refsList) ? refsList.length : 0,
    exporting: bool(raw, "exporting") ?? jobs.some((j) => j.kind === "result" && running(j)),
    beatDirty: bool(raw, "beat_dirty") ?? false,
  };
}

// ---------- lanes ----------

export type LaneKind = "text" | "voice" | "takes" | "beat" | "bass" | "drums";
// "analyse": one waveform and the offer to analyse; "file": one waveform block; "blocks": sections
export type BeatMode = "analyse" | "file" | "blocks";

export function visibleLanes(f: Pick<ScreenFacts, "beatKind" | "beatAnalysed" | "voiceKind">): { lanes: LaneKind[]; beat: BeatMode } {
  const lanes: LaneKind[] = ["text", "voice"];
  if (f.voiceKind === "own") lanes.push("takes");
  lanes.push("beat");
  if (f.beatKind === "own") lanes.push("bass", "drums");
  const beat: BeatMode = f.beatKind === "file" ? (f.beatAnalysed ? "file" : "analyse") : "blocks";
  return { lanes, beat };
}

// ---------- take lanes ----------

export const TAKES_SHOWN = 3;

export interface TakeChoice {
  names: readonly string[];
  // place in the ranking, 1 = best
  ranks: Readonly<Record<string, number>>;
  chosen: string | null;
  // the take being recorded, maybe not in `names` yet
  live: string | null;
  all?: boolean;
}

// best first: ranked by place, then unranked newest first; the live and the chosen take pinned
export function capTakes(c: TakeChoice): { shown: string[]; hidden: number } {
  const names = [...new Set([...c.names, ...(c.live ? [c.live] : [])])];
  const rank = (n: string): number => c.ranks[n] ?? Infinity;
  const order = [...names].sort((a, b) => rank(a) - rank(b) || (rank(a) === Infinity ? b.localeCompare(a) : 0));
  const pinned = [c.live, c.chosen].filter((n): n is string => n !== null && names.includes(n));
  const head = [...new Set(pinned)];
  const rest = order.filter((n) => !head.includes(n));
  const room = c.all ? rest.length : Math.max(0, TAKES_SHOWN - head.length);
  const shown = [...head, ...rest.slice(0, room)];
  return { shown, hidden: names.length - shown.length };
}

// ---------- the ruler ----------

// moves shorter than this are a click
export const DRAG_PX = 4;

export type RulerGesture = { kind: "seek"; bar: number } | { kind: "loop"; fromBar: number; toBar: number };

// from where a press went down to where it came up, in timeline pixels
export function rulerGesture(downX: number, upX: number, barPx: number, totalBars: number): RulerGesture {
  const clamp = (bar: number): number => Math.min(totalBars, Math.max(0, bar));
  if (Math.abs(upX - downX) < DRAG_PX) return { kind: "seek", bar: clamp(upX / barPx) };
  const lo = clamp(Math.min(downX, upX) / barPx);
  const hi = clamp(Math.max(downX, upX) / barPx);
  let fromBar = Math.floor(lo + 1e-9);
  let toBar = Math.ceil(hi - 1e-9);
  if (toBar <= fromBar) toBar = fromBar + 1;
  if (toBar > totalBars) {
    toBar = Math.max(1, Math.floor(totalBars));
    fromBar = Math.min(fromBar, toBar - 1);
  }
  return { kind: "loop", fromBar, toBar };
}

// ---------- the chip ----------

const CHIP: Record<NextStep, string> = {
  analyse: "разобрать бит",
  listen: "послушать",
  record: "запись (R)",
  pick: "выбрать дубль ★",
  "prepare-mix": "свести",
  export: "экспорт",
  done: "",
};

export function chipText(s: NextStep): string {
  return s === "done" ? "готово" : `дальше: ${CHIP[s]}`;
}

// ---------- marks of an own beat ----------

export interface BassMark {
  bar: number;
  step: number;
  len: number;
  midi: number;
}

export interface HitMark {
  bar: number;
  step: number;
  // 0 kick, 1 snare and claps, 2 hats and metal
  row: number;
}

const DRUM_ROW: Record<string, number> = { kick: 0, snare: 1, clap: 1, rim: 1, hat_closed: 2, hat_open: 2, cowbell: 2 };

export function scoreMarks(score: Score): { bass: BassMark[]; drums: HitMark[] } {
  const total = score.sections.reduce((n, s) => n + (typeof s.bars === "number" ? s.bars : 0), 0);
  const bass: BassMark[] = [];
  const drums: HitMark[] = [];
  for (let bar = 0; bar < total; bar++) {
    for (const r of playingAt(score, bar)) {
      if (r.muted || r.replaced) continue;
      const instrument = score.sounds[r.sound]?.instrument;
      const row = score.patterns[r.pattern]?.rows[r.sound];
      if (instrument === undefined || row === undefined) continue;
      const from = r.patternBar * STEPS_PER_BAR;
      if (instrument === "bass808" && isNoteRow(row)) {
        for (const n of notesOf(score, r.pattern, r.sound)) {
          if (n.step >= from && n.step < from + STEPS_PER_BAR) bass.push({ bar, step: n.step - from, len: n.len, midi: n.midi });
        }
      } else if (instrument in DRUM_ROW && !isNoteRow(row)) {
        const lane = DRUM_ROW[instrument] as number;
        drumSteps(score, r.pattern, r.sound)
          .slice(from, from + STEPS_PER_BAR)
          .forEach((hit, step) => {
            if (hit !== ".") drums.push({ bar, step, row: lane });
          });
      }
    }
  }
  drums.sort((a, b) => a.bar - b.bar || a.row - b.row || a.step - b.step);
  return { bass, drums };
}

// ---------- keys and the editor's tabs ----------

const BEAT_LANE_KINDS: readonly LaneKind[] = ["beat", "bass", "drums"];

// the block a Delete or Ctrl+D acts on: the picked one, while a beat lane is selected and it exists
export function keyedBlock(selected: LaneKind | null, picked: number | null, blocks: number): number | null {
  if (selected === null || !BEAT_LANE_KINDS.includes(selected)) return null;
  return picked !== null && picked >= 0 && picked < blocks ? picked : null;
}

// an undo can put another block where the picked one was: the pick follows the block the editor
// shows, so the bar on the lane and the editor agree
export function pickAfterUndo(picked: number | null, editorBlock: number | null): number | null {
  return picked === null ? null : editorBlock;
}

export type DawKey = "play" | "record" | "loop" | "home" | "undo" | "redo" | "duplicate" | "delete";

export interface KeyLike {
  code: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

// `typing`: the focus is in a field that keeps its own keys
export function dawKey(e: KeyLike, typing: boolean): DawKey | null {
  if (typing || e.altKey) return null;
  if (e.ctrlKey || e.metaKey) {
    if (e.code === "KeyZ") return e.shiftKey ? "redo" : "undo";
    if (e.code === "KeyD" && !e.shiftKey) return "duplicate";
    return e.code === "KeyY" ? "redo" : null;
  }
  switch (e.code) {
    case "Space":
      return "play";
    case "KeyR":
      return "record";
    case "KeyL":
      return "loop";
    case "Home":
      return "home";
    case "Delete":
      return "delete";
    default:
      return null;
  }
}

export type EditorTab = "rack" | "roll" | "drums" | "fx" | "text";

export const TAB_NAMES: Record<EditorTab, string> = { rack: "Каналы", roll: "Ноты", drums: "Барабаны", fx: "Эффекты", text: "Текст" };

export function editorTabs(lane: LaneKind, beat: BeatKind): EditorTab[] {
  if (beat === "own" && BEAT_LANE_KINDS.includes(lane)) return ["rack", "roll", "fx"];
  switch (lane) {
    case "text":
      return ["text"];
    case "bass":
      return ["roll"];
    case "drums":
      return ["drums"];
    case "beat":
      return beat === "flip" ? ["roll", "drums", "fx"] : ["fx"];
    default:
      return ["fx"];
  }
}

// the editor opens on a lane newly selected; the same lane again leaves it as the user set it
export function editorOpenAfter(open: boolean, was: LaneKind | null, now: LaneKind): boolean {
  return now !== was || open;
}

// what the song's player is built from; an own beat renders to the same file, so `renders` counts
// the renders the screen waited for
export function playbackKey(f: Pick<ScreenFacts, "chosen" | "studioReady" | "hasBeatAudio">, beatFile: string | null, renders: number): string {
  return JSON.stringify([f.chosen, f.studioReady, beatFile, f.hasBeatAudio, renders]);
}
