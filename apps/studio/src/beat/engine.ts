// The beat page's engine: one wasm beat player, its transport and its meters.
//
// It has no AudioWorklet in it, so node runs it on the same wasm in the tests. The heap is
// fixed at 256 MB and a worst-case player takes a good part of it, so a player is freed
// before the next one is made; a score is checked first, so a bad one never costs the old.

import { lastError, withText, type Dsp } from "../engine/dsp.ts";
import type { BeatAbi } from "../engine/redline_dsp.js";
import type { BeatResult, Zone } from "./protocol.ts";

export type BeatDsp = Dsp & BeatAbi;

export type BeatNotice = { type: "ended" } | { type: "fatal"; message: string };

// frames a single beat_process call takes; a longer block is played in parts
const CHUNK = 1024;
const STOP_FADE = 256;
// past the song's end the engine stops once the mix stayed this quiet this long, as the
// offline render does, or at the latest this far past the end
const QUIET = 1e-6;
const QUIET_S = 0.5;
const TAIL_S = 4;
const ENDED: BeatNotice = { type: "ended" };

interface ScoreInfo {
  // in the player's order of sounds: by name, as the core sorts them
  names: string[];
  libraries: Set<string>;
  racks: Map<string, string>;
  master: string;
}

type Json = Record<string, unknown>;

function object(v: unknown): Json {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {};
}

// only for a text the core has already taken as a score
function scoreInfo(text: string): ScoreInfo {
  const doc = object(JSON.parse(text));
  const sounds = object(doc["sounds"]);
  const mix = object(doc["mix"]);
  const mixSounds = object(mix["sounds"]);
  // the names are a-z, 0-9 and _, where UTF-16 order is the core's byte order
  const names = Object.keys(sounds).sort();
  const libraries = new Set<string>();
  const racks = new Map<string, string>();
  for (const name of names) {
    const library = object(sounds[name])["library"];
    if (typeof library === "string") libraries.add(library);
    racks.set(name, JSON.stringify(object(mixSounds[name])["rack"] ?? []));
  }
  return { names, libraries, racks, master: JSON.stringify(mix["master"] ?? []) };
}

// the score with one rack replaced; null when the rack is not JSON
function withRack(text: string, sound: string | null, rack: string): string | null {
  let effects: unknown;
  try {
    const parsed: unknown = JSON.parse(rack);
    effects = Array.isArray(parsed) ? parsed : object(parsed)["effects"];
  } catch {
    return null;
  }
  const doc = object(JSON.parse(text));
  const mix = object(doc["mix"]);
  doc["mix"] = mix;
  if (sound === null) {
    mix["master"] = effects;
  } else {
    const sounds = object(mix["sounds"]);
    mix["sounds"] = sounds;
    sounds[sound] = { ...object(sounds[sound]), rack: effects };
  }
  return JSON.stringify(doc);
}

const lines = (message: string): string[] => message.split("\n").filter((l) => l.length > 0);
const fail = (errors: string[]): BeatResult => ({ ok: false, errors });
const done = (warnings: string[] = [], rebuilt = false): BeatResult => ({ ok: true, rebuilt, warnings });
const messageOf = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export class BeatEngine {
  readonly #dsp: BeatDsp;
  readonly #rate: number;
  readonly #notify: (n: BeatNotice) => void;
  readonly #ptr: number;
  #view: Float32Array;
  #handle = 0;
  #text = "";
  #score: ScoreInfo | null = null;
  // kept to load again into a rebuilt player
  readonly #zones: Zone[] = [];
  // the song frame the player's next block starts at
  #pos = 0;
  #length = 0;
  #latency = 0;
  #playing = false;
  // frames of the stop's fade left, 0 when not stopping
  #stopping = 0;
  #loopStart = 0;
  #loopEnd = -1;
  #repeat = false;
  // a loop's wrap: for `latency` frames after it the frames before the end are still heard
  #jumped = false;
  #jumpFrom = 0;
  #jumpTo = 0;
  #quiet = 0;
  #dead: string | null = null;
  // the mix's peaks over the last process call
  peakLeft = 0;
  peakRight = 0;

  constructor(dsp: BeatDsp, rate: number, notify: (n: BeatNotice) => void = () => {}) {
    this.#dsp = dsp;
    this.#rate = rate;
    this.#notify = notify;
    const ptr = dsp._malloc(2 * CHUNK * 4);
    if (!ptr) throw new Error("the wasm heap is full");
    this.#ptr = ptr;
    this.#view = dsp.HEAPF32.subarray(ptr >> 2, (ptr >> 2) + 2 * CHUNK);
  }

  get loaded(): boolean {
    return this.#handle !== 0;
  }

  get playing(): boolean {
    return this.#playing;
  }

  get dead(): boolean {
    return this.#dead !== null;
  }

  get length(): number {
    return this.#length;
  }

  get latency(): number {
    return this.#latency;
  }

  // the heap view every block is copied through, made once
  get scratch(): Float32Array {
    return this.#view;
  }

  // the song frame being heard: the player's less its latency
  get position(): number {
    const latency = this.#latency;
    if (this.#jumped) {
      const k = this.#pos - this.#jumpTo;
      if (k >= 0 && k < latency) return Math.max(0, this.#jumpFrom - latency + k);
      this.#jumped = false;
    }
    return Math.max(0, this.#pos - latency);
  }

  get repeat(): boolean {
    return this.#repeat;
  }

  set repeat(on: boolean) {
    this.#repeat = on;
  }

  load(text: string): BeatResult {
    return this.#guard(() => {
      const errors = this.#check(text);
      if (errors.length) return fail(errors);
      this.#playing = false;
      this.#stopping = 0;
      this.#loopEnd = -1;
      return this.#build(text, 0);
    });
  }

  // the same sounds swap the event table in at the next block and ringing voices go on;
  // other sounds, or a master rack changed, make a new player at the spot being heard
  update(text: string): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return this.load(text);
      const dsp = this.#dsp;
      const handle = this.#handle;
      if (withText(dsp, text, (p, n) => dsp._beat_set_score(handle, p, n)) !== 0) {
        const message = lastError(dsp);
        return message.startsWith("rebuild") ? this.#rebuild(text) : fail(lines(message));
      }
      const before = this.#score;
      const score = scoreInfo(text);
      this.#text = text;
      this.#score = score;
      this.#length = dsp._beat_length(handle);
      if (before && before.master !== score.master) return this.#rebuild(text);
      // the player does not read racks from a score; the changed ones go in as rack swaps
      const warnings: string[] = [];
      for (const [k, name] of score.names.entries()) {
        const rack = score.racks.get(name) ?? "[]";
        if (before?.racks.get(name) === rack) continue;
        if (withText(dsp, rack, (p, n) => dsp._beat_set_rack(handle, k, p, n)) === 0) continue;
        const message = lastError(dsp);
        if (message.startsWith("rebuild")) return this.#rebuild(text);
        warnings.push(`mix.sounds.${name}.rack: ${message}`);
      }
      return done(warnings);
    });
  }

  // a sound's rack, crossfaded in; one later than the player compensates rebuilds it
  setRack(sound: string, rack: string): BeatResult {
    return this.#guard(() => {
      const index = this.#score?.names.indexOf(sound) ?? -1;
      if (!this.#handle || index < 0) return fail([`no sound ${sound} in the score`]);
      const dsp = this.#dsp;
      const handle = this.#handle;
      const code = withText(dsp, rack, (p, n) => dsp._beat_set_rack(handle, index, p, n));
      const message = code === 0 ? "" : lastError(dsp);
      if (code !== 0 && !message.startsWith("rebuild")) return fail(lines(message));
      const text = withRack(this.#text, sound, rack);
      if (text === null) return fail(["the rack is not JSON"]);
      if (code !== 0) return this.#rebuild(text);
      this.#text = text;
      this.#score = scoreInfo(text);
      return done();
    });
  }

  // the master rack is made with the player, so a new one is a new player
  setMasterRack(rack: string): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return fail(["no score is loaded"]);
      const text = withRack(this.#text, null, rack);
      return text === null ? fail(["the rack is not JSON"]) : this.#rebuild(text);
    });
  }

  // samplers hear the zone after rebuildSamplers
  loadZone(zone: Zone): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return fail(["no score is loaded"]);
      const error = this.#loadZone(zone);
      if (error) return fail(lines(error));
      this.#zones.push(zone);
      return done();
    });
  }

  rebuildSamplers(): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return fail(["no score is loaded"]);
      if (this.#dsp._beat_rebuild_samplers(this.#handle) !== 0) return fail(lines(lastError(this.#dsp)));
      return done();
    });
  }

  seek(frame: number): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return fail(["no score is loaded"]);
      if (!Number.isSafeInteger(frame) || frame < 0) return fail([`the frame ${frame} is not a whole number from 0`]);
      return this.#seek(frame) ? done() : fail(lines(lastError(this.#dsp)));
    });
  }

  play(): BeatResult {
    return this.#guard(() => {
      if (!this.#handle) return fail(["no score is loaded"]);
      const looping = this.#loopEnd >= 0 || this.#repeat;
      if (!this.#playing && !looping && this.position >= this.#length && !this.#seek(0)) {
        return fail(lines(lastError(this.#dsp)));
      }
      this.#playing = true;
      this.#stopping = 0;
      this.#quiet = 0;
      return done();
    });
  }

  stop(): BeatResult {
    if (this.#playing && this.#stopping === 0) this.#stopping = STOP_FADE;
    return this.#dead === null ? done() : fail([this.#dead]);
  }

  // plays [start, end) of the song over and over; the player is sent into it
  loop(start: number, end: number): BeatResult {
    return this.#guard(() => {
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end <= start) {
        return fail([`the loop ${start}..${end} is not whole frames with the start before the end`]);
      }
      this.#loopStart = start;
      this.#loopEnd = end;
      if (this.#handle && (this.#pos < start || this.#pos >= end) && !this.#seek(start)) {
        return fail(lines(lastError(this.#dsp)));
      }
      return done();
    });
  }

  clearLoop(): void {
    this.#loopEnd = -1;
  }

  // fills `frames` interleaved stereo frames of `out`: silence when stopped
  process(out: Float32Array, frames: number): void {
    const total = Math.min(frames, out.length >> 1);
    this.peakLeft = 0;
    this.peakRight = 0;
    let at = 0;
    if (this.#handle !== 0 && this.#playing && this.#dead === null) {
      try {
        while (at < total && this.#playing) {
          let n = Math.min(CHUNK, total - at);
          let end = this.#loopEnd;
          let start = this.#loopStart;
          if (end < 0 && this.#repeat) {
            end = this.#length;
            start = 0;
          }
          if (end >= 0) {
            if (this.#pos >= end) this.#wrap(start);
            n = Math.min(n, end - this.#pos);
          }
          if (this.#dsp._beat_process(this.#handle, this.#ptr, n) !== 0) {
            this.#die(lastError(this.#dsp));
            break;
          }
          const peak = this.#copy(out, at, n);
          this.#pos += n;
          at += n;
          if (this.#stopping === 0 && end < 0) this.#watchEnd(peak, n);
          else if (this.#stopping === 1) {
            this.#stopping = 0;
            this.#playing = false;
          }
        }
      } catch (error) {
        this.#die(messageOf(error));
      }
    }
    if (at < total) out.fill(0, 2 * at, 2 * total);
  }

  dispose(): void {
    if (this.#dead === null) {
      if (this.#handle) this.#dsp._beat_free(this.#handle);
      this.#dsp._free(this.#ptr);
    }
    this.#handle = 0;
    this.#dead ??= "the engine was disposed";
  }

  // a stop's fade leaves `#stopping` at 1 once it reached silence
  #copy(out: Float32Array, at: number, n: number): number {
    let view = this.#view;
    if (view.buffer !== this.#dsp.HEAPF32.buffer) {
      view = this.#dsp.HEAPF32.subarray(this.#ptr >> 2, (this.#ptr >> 2) + 2 * CHUNK);
      this.#view = view;
    }
    let peakLeft = this.peakLeft;
    let peakRight = this.peakRight;
    let peak = 0;
    const base = 2 * at;
    for (let i = 0; i < n; i++) {
      let gain = 1;
      if (this.#stopping > 1) {
        gain = (this.#stopping - 1) / STOP_FADE;
        this.#stopping--;
      } else if (this.#stopping === 1) {
        gain = 0;
      }
      const l = (view[2 * i] as number) * gain;
      const r = (view[2 * i + 1] as number) * gain;
      out[base + 2 * i] = l;
      out[base + 2 * i + 1] = r;
      const al = Math.abs(l);
      const ar = Math.abs(r);
      if (al > peakLeft) peakLeft = al;
      if (ar > peakRight) peakRight = ar;
      if (al > peak) peak = al;
      if (ar > peak) peak = ar;
    }
    this.peakLeft = peakLeft;
    this.peakRight = peakRight;
    return peak;
  }

  #watchEnd(peak: number, n: number): void {
    const past = this.#pos - this.#latency - this.#length;
    if (past < 0) return;
    this.#quiet = peak < QUIET ? this.#quiet + n : 0;
    if (this.#quiet >= QUIET_S * this.#rate || past >= TAIL_S * this.#rate) {
      this.#playing = false;
      this.#notify(ENDED);
    }
  }

  #wrap(start: number): void {
    const from = this.#pos;
    if (this.#dsp._beat_seek(this.#handle, start) !== 0) throw new Error(lastError(this.#dsp));
    this.#jumped = true;
    this.#jumpFrom = from;
    this.#jumpTo = start;
    this.#pos = start;
  }

  #seek(frame: number): boolean {
    if (this.#dsp._beat_seek(this.#handle, frame) !== 0) return false;
    this.#pos = frame;
    this.#jumped = false;
    this.#quiet = 0;
    return true;
  }

  #check(text: string): string[] {
    const dsp = this.#dsp;
    const ptr = withText(dsp, text, (p, n) => dsp._beat_check_json(p, n));
    if (!ptr) return lines(lastError(dsp));
    return JSON.parse(dsp.UTF8ToString(ptr)) as string[];
  }

  // a new player at the spot being heard, the old one freed first
  #rebuild(text: string): BeatResult {
    const errors = this.#check(text);
    if (errors.length) return fail(errors);
    const result = this.#build(text, this.position);
    return result.ok ? done(result.warnings, true) : result;
  }

  #build(text: string, heard: number): BeatResult {
    const previous = this.#handle ? this.#text : null;
    if (this.#handle) this.#dsp._beat_free(this.#handle);
    this.#handle = 0;
    const result = this.#make(text, heard);
    // what the core refuses past the check (a rate, a rack's params) leaves the old score playing
    if (!result.ok && previous !== null) this.#make(previous, heard);
    return result;
  }

  #make(text: string, heard: number): BeatResult {
    const dsp = this.#dsp;
    const handle = withText(dsp, text, (p, n) => dsp._beat_new(p, n, this.#rate));
    if (!handle) return fail(lines(lastError(dsp)));
    this.#handle = handle;
    this.#text = text;
    this.#score = scoreInfo(text);
    this.#latency = Math.max(0, dsp._beat_latency(handle));
    this.#length = Math.max(0, dsp._beat_length(handle));
    this.#quiet = 0;
    const warnings = this.#reloadZones();
    // so the frame heard next is `heard`; the new racks are empty, so `latency` frames are silent
    const at = heard > 0 ? heard + this.#latency : 0;
    this.#pos = 0;
    this.#jumped = false;
    if (at > 0 && !this.#seek(at)) warnings.push(lastError(dsp));
    return done(warnings);
  }

  // only the zones of the libraries the score plays, as the bank's budget is shared
  #reloadZones(): string[] {
    const used = this.#score?.libraries ?? new Set<string>();
    const warnings: string[] = [];
    let any = false;
    for (const zone of this.#zones) {
      if (!used.has(zone.library)) continue;
      const error = this.#loadZone(zone);
      if (error) warnings.push(`zone ${zone.library} ${zone.root}: ${error}`);
      else any = true;
    }
    if (any && this.#dsp._beat_rebuild_samplers(this.#handle) !== 0) warnings.push(lastError(this.#dsp));
    return warnings;
  }

  #loadZone(zone: Zone): string | null {
    const dsp = this.#dsp;
    const { samples, channels } = zone;
    if (samples.length % channels !== 0) return `${samples.length} samples are not whole frames of ${channels} channels`;
    const bytes = samples.length * 2;
    const ptr = dsp._malloc(Math.max(2, bytes));
    if (!ptr) return "the wasm heap is full";
    try {
      dsp.HEAPU8.set(new Uint8Array(samples.buffer, samples.byteOffset, bytes), ptr);
      const handle = this.#handle;
      const code = withText(dsp, zone.library, (p, n) =>
        dsp._beat_load_zone(handle, p, n, zone.root, zone.velLo, zone.velHi, zone.rate, ptr, samples.length / channels, channels),
      );
      return code === 0 ? null : lastError(dsp);
    } finally {
      dsp._free(ptr);
    }
  }

  // a throw out of the wasm leaves the module not to be trusted: told once, then silence
  #guard(f: () => BeatResult): BeatResult {
    if (this.#dead !== null) return fail([this.#dead]);
    try {
      return f();
    } catch (error) {
      this.#die(messageOf(error));
      return fail([messageOf(error)]);
    }
  }

  #die(message: string): void {
    if (this.#dead !== null) return;
    this.#dead = message;
    this.#handle = 0;
    this.#playing = false;
    this.#notify({ type: "fatal", message });
  }
}
