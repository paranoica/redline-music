// The song page's playback: two backends behind one interface and the owner that picks one,
// swaps them at a stopped moment (a new render or take also while it plays) and keeps a block's
// audition and the song from playing together.
// SimplePlayer plays the beat file and the raw take until the studio is built; StudioPlayer plays
// the chosen take through the studio engine's vocal, beat and master racks, the exact mix.

import { createApi as createMixApi, Superseded } from "../api.ts";
import type { Score } from "../beat/model.ts";
import { catalogueOf } from "../core/diff.ts";
import { intoLoop, MIN_LOOP, type Loop } from "../core/loop.ts";
import { apiPath, apiRoot } from "../core/mount.ts";
import { buildLanes, buildRacks, MAX_RACK_EFFECTS, parseRack } from "../core/racks.ts";
import { changedKeys, needsAnalysis, staleKeys } from "../core/stale.ts";
import { parseWav } from "../core/wav.ts";
import { startHost, type Host } from "../engine/host.ts";
import type { Levels } from "../engine/protocol.ts";
import { ApiError, type SongApi } from "../song/api.ts";
import type { Analysis, Lane, RackSlot, Racks, Settings } from "../types.ts";

export type LaneId = "beat" | "voice";
export type BackendKind = "simple" | "studio";

export interface SongPlayer {
  readonly kind: BackendKind;
  load(): Promise<void>;
  // from the position held when no second is given
  play(from?: number): Promise<void>;
  pause(): void;
  // back to the start (of the loop when one is on)
  stop(): void;
  seek(seconds: number): void;
  setLoop(loop: Loop | null): void;
  // the song second heard, from the context clock
  position(): number;
  readonly playing: boolean;
  readonly duration: number;
  setLaneGain(lane: LaneId, gain: number): void;
  // the master's linear peak heard lately, 0 while stopped
  meter(): number;
  dispose(): Promise<void>;
}

// ---------- pure parts ----------

// Where a take's frame 0 sits on the beat, in seconds. The recorder drops the round trip (the
// sidecar's `offset` frames) before it writes the take, and /api/takes/audio serves the file as
// it is, so take frame 0 is what was sung to beat frame 0.
export const TAKE_LEAD = 0;

// a lane whose frame 0 sits `lead` seconds into the song, played from `position`: when its buffer
// starts (after `delay`) and from where in it; null once it has nothing left
export function laneStart(position: number, lead: number, duration: number): { delay: number; offset: number } | null {
  const at = Math.max(0, position);
  if (at >= lead + duration) return null;
  return at < lead ? { delay: lead - at, offset: 0 } : { delay: 0, offset: at - lead };
}

// the position `elapsed` seconds after it was `anchor`: inside a loop it wraps, the song's end stops it
export function positionAt(anchor: number, elapsed: number, loop: Loop | null, duration: number): number {
  const at = anchor + Math.max(0, elapsed);
  if (loop && anchor < loop.end && at >= loop.end) {
    return loop.start + ((at - loop.start) % (loop.end - loop.start));
  }
  return Math.min(at, duration);
}

export function fitLoop(loop: Loop, duration: number): Loop | null {
  if (!Number.isFinite(loop.start) || !Number.isFinite(loop.end)) return null;
  const start = Math.max(0, loop.start);
  const end = Math.min(duration, loop.end);
  return end - start >= MIN_LOOP ? { start, end } : null;
}

// where play starts from `from`: into a loop that is on, as the studio engine does; at the end, the start
export function startPoint(from: number, loop: Loop | null, duration: number): number {
  const at = Number.isFinite(from) ? Math.max(0, from) : 0;
  if (loop) return intoLoop(at, true, loop);
  return at >= duration ? 0 : at;
}

// where one pass of the sources from `from` ends
export function segmentEnd(from: number, loop: Loop | null, duration: number): number {
  return loop && from < loop.end ? loop.end : duration;
}

export interface LaneState {
  gain: number;
  mute: boolean;
  solo: boolean;
}

export function laneGains(lanes: Record<LaneId, LaneState>): Record<LaneId, number> {
  const solo = lanes.beat.solo || lanes.voice.solo;
  const heard = (l: LaneState): number => (l.mute || (solo && !l.solo) || !(l.gain > 0) ? 0 : l.gain);
  return { beat: heard(lanes.beat), voice: heard(lanes.voice) };
}

// the studio engine's gain effect spans -60..+24 dB: two of them reach a silence the ear takes for
// one, and a lane is never made louder than one of them allows
const GAIN_FLOOR_DB = -60;
const GAIN_CEILING_DB = 24;

export function laneDbSlots(gain: number): [number, number] {
  if (!(gain > 0)) return [GAIN_FLOOR_DB, GAIN_FLOOR_DB];
  const db = 20 * Math.log10(gain);
  const first = Math.min(GAIN_CEILING_DB, Math.max(GAIN_FLOOR_DB, db));
  const second = Math.min(0, Math.max(GAIN_FLOOR_DB, db - first));
  return [first, second];
}

const gainSlot = (db: number): RackSlot => ({ effect: "gain", on: true, params: { db } });

// The lanes' gains as two slots at the end of the vocal and beat racks: always there, so a move
// is a set in place rather than a rebuild, and after the user's slots so their lanes keep their
// indexes. A rack with no room plays without them.
export function withLaneGains(racks: Racks, gains: Record<LaneId, number>, limit = MAX_RACK_EFFECTS): Racks {
  const add = (slots: RackSlot[], gain: number): RackSlot[] =>
    slots.length + 2 > limit ? [...slots] : [...slots, ...laneDbSlots(gain).map(gainSlot)];
  return { vocal: add(racks.vocal, gains.voice), beat: add(racks.beat, gains.beat), master: [...racks.master] };
}

// ---------- the owner ----------

export interface AuditionLike {
  readonly playing: boolean;
  start(score: Score, fromBar: number, bars: number): Promise<void>;
  update(score: Score): void;
  retarget(score: Score, fromBar: number, bars: number): void;
  hit(score: Score): Promise<boolean>;
  // the song second heard
  position(): number;
  stop(): Promise<void>;
  dispose(): Promise<void>;
}

export interface PlaybackDeps {
  simple: () => SongPlayer;
  studio: () => SongPlayer;
  // whether the studio can be built for the song now
  ready: () => Promise<boolean>;
  audition: AuditionLike;
}

// the song api's probe: /mix/schema answers 409 no_voice until the studio can be built
export function studioReady(api: Pick<SongApi, "mixReady">): () => Promise<boolean> {
  return async () => (await api.mixReady()) === "ready";
}

// A new key for the song's player (a new render, another take). While the song plays a beat the
// player renews its backend in place and the song goes on; otherwise it is built again.
export function onNewKey(f: { had: boolean; playing: boolean; beatAudio: boolean }): "renew" | "rebuild" {
  return f.had && f.playing && f.beatAudio ? "renew" : "rebuild";
}

export type BackendMakers = Pick<PlaybackDeps, "simple" | "studio" | "ready">;

export class Playback {
  #deps: PlaybackDeps;
  #player: SongPlayer | null = null;
  // a renewed backend is starting where the old one played
  #handing = false;
  // the last play, pause or stop and how many there were, for one asked during a hand-over
  #asked: { count: number; how: "play" | "pause" | "stop" } = { count: 0, how: "play" };
  #loop: Loop | null = null;
  readonly #lanes: Record<LaneId, LaneState> = {
    beat: { gain: 1, mute: false, solo: false },
    voice: { gain: 1, mute: false, solo: false },
  };
  #queue: Promise<void> = Promise.resolve();
  #closed = false;

  constructor(deps: PlaybackDeps) {
    this.#deps = deps;
  }

  get kind(): BackendKind | null {
    return this.#player?.kind ?? null;
  }

  // the backend that plays now; a renewed one only once it took over
  get current(): SongPlayer | null {
    return this.#player;
  }

  get playing(): boolean {
    return this.#handing || (this.#player?.playing ?? false);
  }

  get duration(): number {
    return this.#player?.duration ?? 0;
  }

  position(): number {
    return this.#player?.position() ?? 0;
  }

  meter(): number {
    return this.#player?.meter() ?? 0;
  }

  // asks whether the studio is ready and swaps to the backend that fits, unless the song plays;
  // the new one goes on from the old one's position
  sync(): Promise<void> {
    return this.#enqueue(false);
  }

  // new backends (a new render, another take): one is built even of the same kind and takes over
  // at the old one's position, playing on if the song plays
  renew(make: BackendMakers): Promise<void> {
    this.#deps = { ...this.#deps, simple: make.simple, studio: make.studio, ready: make.ready };
    return this.#enqueue(true);
  }

  #enqueue(renew: boolean): Promise<void> {
    const run = this.#queue.then(() => this.#sync(renew));
    this.#queue = run.catch(() => {});
    return run;
  }

  async #sync(renew: boolean): Promise<void> {
    if (this.#closed) return;
    let ready: boolean;
    try {
      ready = await this.#deps.ready();
    } catch (error) {
      // the probe failed: what plays now goes on playing, or is renewed as it is
      if (!this.#player) throw error;
      if (!renew) return;
      ready = this.#player.kind === "studio";
    }
    const want: BackendKind = ready ? "studio" : "simple";
    const old = this.#player;
    if (this.#closed || (!renew && (old?.kind === want || old?.playing))) return;
    const next = want === "studio" ? this.#deps.studio() : this.#deps.simple();
    try {
      await next.load();
    } catch (error) {
      await next.dispose().catch(() => {});
      throw error;
    }
    // closed, or the old one started playing while the new one loaded: try again later
    if (this.#closed || this.#player !== old || (!renew && old?.playing)) {
      await next.dispose();
      return;
    }
    const at = old?.position() ?? 0;
    const playing = old?.playing ?? false;
    if (playing) old?.pause();
    this.#player = next;
    this.#applyLoop();
    this.#applyLanes();
    const from = startPoint(at, this.#loop && fitLoop(this.#loop, next.duration), next.duration);
    try {
      if (playing) await this.#handOver(next, from);
      else next.seek(from);
    } finally {
      if (old) await old.dispose();
    }
  }

  async #handOver(next: SongPlayer, from: number): Promise<void> {
    const asked = this.#asked.count;
    this.#handing = true;
    try {
      await next.play(from);
    } finally {
      this.#handing = false;
    }
    if (this.#asked.count === asked) return;
    if (this.#asked.how === "stop") next.stop();
    else if (this.#asked.how === "pause") next.pause();
  }

  async play(from?: number): Promise<void> {
    if (this.#closed) return;
    this.#asked = { count: this.#asked.count + 1, how: "play" };
    // also cancels an audition still starting
    await this.#deps.audition.stop();
    if (!this.#player) await this.sync();
    await this.#player?.play(from);
  }

  pause(): void {
    this.#asked = { count: this.#asked.count + 1, how: "pause" };
    this.#player?.pause();
  }

  stop(): void {
    this.#asked = { count: this.#asked.count + 1, how: "stop" };
    this.#player?.stop();
    // also cancels an audition still starting
    void this.#deps.audition.stop().catch(() => {});
  }

  seek(seconds: number): void {
    this.#player?.seek(seconds);
  }

  setLoop(loop: Loop | null): void {
    this.#loop = loop;
    this.#applyLoop();
  }

  setLane(lane: LaneId, change: Partial<LaneState>): void {
    this.#lanes[lane] = { ...this.#lanes[lane], ...change };
    this.#applyLanes();
  }

  async audition(score: Score, fromBar: number, bars: number): Promise<void> {
    if (this.#closed) return;
    if (this.#player?.playing) this.#player.pause();
    await this.#deps.audition.start(score, fromBar, bars);
  }

  updateAudition(score: Score): void {
    if (this.#deps.audition.playing) this.#deps.audition.update(score);
  }

  // another score and loop for the audition that plays, without a restart
  retargetAudition(score: Score, fromBar: number, bars: number): void {
    if (this.#deps.audition.playing) this.#deps.audition.retarget(score, fromBar, bars);
  }

  // One hit of a sound. It plays over the song, which is another engine; while the audition
  // loops it is skipped rather than cut in.
  async hit(score: Score): Promise<boolean> {
    if (this.#closed || this.#deps.audition.playing) return false;
    return this.#deps.audition.hit(score);
  }

  // the audition's song second, null when it does not play
  auditionPosition(): number | null {
    return this.#deps.audition.playing ? this.#deps.audition.position() : null;
  }

  async stopAudition(): Promise<void> {
    if (this.#deps.audition.playing) await this.#deps.audition.stop();
  }

  async dispose(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    const player = this.#player;
    this.#player = null;
    await Promise.all([player?.dispose(), this.#deps.audition.dispose()]);
  }

  #applyLoop(): void {
    const p = this.#player;
    if (p) p.setLoop(this.#loop && fitLoop(this.#loop, p.duration));
  }

  #applyLanes(): void {
    const p = this.#player;
    if (!p) return;
    const gains = laneGains(this.#lanes);
    p.setLaneGain("beat", gains.beat);
    p.setLaneGain("voice", gains.voice);
  }
}

// ---------- the backends ----------

export interface SongSource {
  // the song api's base, as createSongApi takes it
  base?: string | undefined;
  token: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function fetchBytes(url: string, token: string, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(url, { headers: { "X-Token": token }, signal });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(response.status, body && typeof body === "object" ? body : {});
  }
  return response.arrayBuffer();
}

// a source played a little after it is scheduled, so every lane starts on the same sample
const START_LEAD = 0.03;
// the next pass of a loop is scheduled this long before the current one ends
const PASS_AHEAD = 0.25;
const GAIN_GLIDE = 0.01;

export class SimplePlayer implements SongPlayer {
  readonly kind = "simple";
  readonly #source: SongSource;
  readonly #take: string | null;
  readonly #lifetime = new AbortController();
  #ctx: AudioContext | null = null;
  #buffers: { beat: AudioBuffer | null; voice: AudioBuffer | null } = { beat: null, voice: null };
  #nodes: Record<LaneId, GainNode> | null = null;
  #analyser: AnalyserNode | null = null;
  #scratch: Float32Array<ArrayBuffer> = new Float32Array(0);
  readonly #gains: Record<LaneId, number> = { beat: 1, voice: 1 };
  #sources: AudioBufferSourceNode[] = [];
  #timer: ReturnType<typeof setTimeout> | null = null;
  #loop: Loop | null = null;
  #playing = false;
  #anchor = { position: 0, at: 0 };
  #duration = 0;

  // `take`: the chosen take's name, or null for the beat alone
  constructor(source: SongSource, take: string | null) {
    this.#source = source;
    this.#take = take;
  }

  get playing(): boolean {
    return this.#playing;
  }

  get duration(): number {
    return this.#duration;
  }

  async load(): Promise<void> {
    const root = apiRoot(this.#source.base, "/api");
    const signal = this.#lifetime.signal;
    const ctx = new AudioContext({ latencyHint: "interactive" });
    this.#ctx = ctx;
    const get = async (path: string): Promise<AudioBuffer> => ctx.decodeAudioData(await fetchBytes(apiPath(root, path), this.#source.token, signal));
    const take = this.#take;
    const [beat, voice] = await Promise.all([get("/song/beat-audio"), take === null ? null : get(`/takes/audio?name=${encodeURIComponent(take)}`)]);
    this.#buffers = { beat, voice };
    this.#duration = Math.max(beat.duration, voice ? TAKE_LEAD + voice.duration : 0);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.connect(ctx.destination);
    this.#analyser = analyser;
    this.#scratch = new Float32Array(analyser.fftSize);
    const node = (lane: LaneId): GainNode => {
      const g = ctx.createGain();
      g.gain.value = this.#gains[lane];
      g.connect(analyser);
      return g;
    };
    this.#nodes = { beat: node("beat"), voice: node("voice") };
  }

  async play(from?: number): Promise<void> {
    const ctx = this.#ctx;
    if (!ctx || !this.#nodes) return;
    await ctx.resume();
    const start = startPoint(from ?? this.position(), this.#loop, this.#duration);
    this.#halt();
    this.#playing = true;
    this.#pass(start, ctx.currentTime + START_LEAD, true);
  }

  pause(): void {
    const at = this.position();
    this.#halt();
    this.#playing = false;
    this.#anchor = { position: at, at: 0 };
  }

  stop(): void {
    this.#halt();
    this.#playing = false;
    this.#anchor = { position: startPoint(0, this.#loop, this.#duration), at: 0 };
  }

  seek(seconds: number): void {
    const at = startPoint(seconds, this.#loop, this.#duration);
    if (this.#playing) void this.play(at);
    else this.#anchor = { position: at, at: 0 };
  }

  setLoop(loop: Loop | null): void {
    this.#loop = loop;
    // the passes scheduled were cut for the old loop
    if (this.#playing) void this.play(this.position());
    else this.#anchor = { position: startPoint(this.#anchor.position, loop, this.#duration), at: 0 };
  }

  position(): number {
    const ctx = this.#ctx;
    if (!this.#playing || !ctx) return this.#anchor.position;
    const elapsed = ctx.currentTime - this.#anchor.at - (ctx.outputLatency || 0);
    return positionAt(this.#anchor.position, elapsed, this.#loop, this.#duration);
  }

  meter(): number {
    const a = this.#analyser;
    if (!a || !this.#playing) return 0;
    a.getFloatTimeDomainData(this.#scratch);
    let peak = 0;
    for (const v of this.#scratch) peak = Math.max(peak, Math.abs(v));
    return peak;
  }

  setLaneGain(lane: LaneId, gain: number): void {
    this.#gains[lane] = gain;
    const node = this.#nodes?.[lane];
    if (node && this.#ctx) node.gain.setTargetAtTime(gain, this.#ctx.currentTime, GAIN_GLIDE);
  }

  async dispose(): Promise<void> {
    this.#lifetime.abort();
    this.#halt();
    this.#playing = false;
    const ctx = this.#ctx;
    this.#ctx = null;
    if (ctx && ctx.state !== "closed") await ctx.close();
  }

  // one pass from `from` at context time `when`, to the loop's end or the song's
  #pass(from: number, when: number, first: boolean): void {
    const ctx = this.#ctx;
    const nodes = this.#nodes;
    if (!ctx || !nodes) return;
    const end = segmentEnd(from, this.#loop, this.#duration);
    const until = when + (end - from);
    const lanes: [LaneId, AudioBuffer | null, number][] = [
      ["beat", this.#buffers.beat, 0],
      ["voice", this.#buffers.voice, TAKE_LEAD],
    ];
    for (const [lane, buffer, lead] of lanes) {
      if (!buffer) continue;
      const start = laneStart(from, lead, buffer.duration);
      if (!start || when + start.delay >= until) continue;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(nodes[lane]);
      source.start(when + start.delay, start.offset);
      source.stop(until);
      source.onended = () => {
        source.disconnect();
        this.#sources = this.#sources.filter((s) => s !== source);
      };
      this.#sources.push(source);
    }
    // later passes of a loop are where the clock wraps to anyway
    if (first) this.#anchor = { position: from, at: when };
    const loop = this.#loop;
    const wait = Math.max(0, (until - PASS_AHEAD - ctx.currentTime) * 1000);
    this.#timer = setTimeout(() => {
      this.#timer = null;
      if (!this.#playing) return;
      if (loop && end === loop.end) this.#pass(loop.start, until, false);
      else this.#timer = setTimeout(() => this.#ended(), PASS_AHEAD * 1000);
    }, wait);
  }

  #ended(): void {
    this.#timer = null;
    this.#halt();
    this.#playing = false;
    this.#anchor = { position: 0, at: 0 };
  }

  #halt(): void {
    if (this.#timer !== null) clearTimeout(this.#timer);
    this.#timer = null;
    for (const s of this.#sources) {
      s.onended = null;
      try {
        s.stop();
      } catch {
        // a source that never started
      }
      s.disconnect();
    }
    this.#sources = [];
  }
}

export interface StudioOptions extends SongSource {
  // a refusal or a failure the page should show; the sound goes on as before it
  onError?: (message: string) => void;
}

const ANALYSIS_WAIT_MS = 300;
// a report from before the last press of play may still be on its way
const SETTLE_MS = 150;

export class StudioPlayer implements SongPlayer {
  readonly kind = "studio";
  readonly #api: ReturnType<typeof createMixApi>;
  readonly #onError: (message: string) => void;
  #host: Host | null = null;
  #rate = 0;
  #frames = 0;
  #defaults: Settings = {};
  #settings: Settings = {};
  #numbers: Analysis = {};
  #stale: Set<string> = new Set();
  // the server's racks for the settings they were built for; any other settings build their own
  #served: { key: string; racks: Racks; lanes: Lane[] } | null = null;
  readonly #gains: Record<LaneId, number> = { beat: 1, voice: 1 };
  #loop: Loop | null = null;
  #playing = false;
  #held = false;
  #toggledAt = 0;
  #anchor = { frame: 0, at: 0 };
  #analysing: AbortController | null = null;
  #analysisTimer: ReturnType<typeof setTimeout> | null = null;
  #ready = false;
  #peak = 0;

  constructor(options: StudioOptions) {
    this.#api = createMixApi({ base: apiPath(apiRoot(options.base, "/api"), "/mix"), token: options.token });
    this.#onError = options.onError ?? (() => {});
  }

  // the racks are on the engine and the stems loaded: knobs can be heard
  get ready(): boolean {
    return this.#ready;
  }

  get playing(): boolean {
    return this.#playing;
  }

  get duration(): number {
    return this.#rate ? this.#frames / this.#rate : 0;
  }

  get settings(): Settings {
    return this.#settings;
  }

  async load(): Promise<void> {
    const session = await this.#api.session();
    const rate = session.track.rate;
    const stem = async (name: "vocal" | "beat" | "original") => {
      const wav = parseWav((await this.#api.stem(name)).bytes);
      if (wav.rate !== rate) throw new Error(`стем ${name} в ${wav.rate} Гц, трек в ${rate}`);
      return wav;
    };
    const started = startHost(rate, catalogueOf(session.schema.effects));
    let stems: Awaited<ReturnType<typeof stem>>[];
    try {
      stems = await Promise.all([stem("vocal"), stem("beat"), stem("original")]);
    } catch (error) {
      started.then((h) => h.dispose(), () => {});
      throw error;
    }
    const host = await started;
    if (host.ctx.sampleRate !== rate) {
      await host.dispose();
      throw new Error(`браузер не открыл звук на ${rate} Гц (дал ${host.ctx.sampleRate})`);
    }
    this.#host = host;
    this.#rate = rate;
    const [vocal, beat, original] = stems as [(typeof stems)[0], (typeof stems)[0], (typeof stems)[0]];
    this.#frames = vocal.frames;
    const scalars = Object.fromEntries(session.schema.settings.map((s) => [s.key, s.default]));
    this.#defaults = { version: 2, ...scalars, rack: [] };
    this.#settings = { ...this.#defaults, ...session.settings };
    this.#numbers = session.analysis;
    this.#stale = staleKeys(session.stale);
    if (session.racks) {
      const r = session.racks;
      this.#served = {
        key: JSON.stringify(this.#settings),
        racks: { vocal: parseRack(r.vocal), beat: parseRack(r.beat), master: parseRack(r.master) },
        lanes: buildLanes(this.#settings, this.#numbers),
      };
    } else {
      this.#onError(`Живые рэки не собрались: ${session.racks_error ?? "без причины"}`);
    }
    this.#wire(host);
    host.client.loadStems(vocal.frames, vocal.data, beat.data, original.data);
    this.#push();
    host.client.loop(false, 0, this.#frames);
    host.client.listen({ result: 1, original: 0, reference: 0, render: 0 });
    this.#ready = true;
  }

  // the knobs' settings: heard at once with the numbers there are, then with fresh ones when the
  // change needs a new analysis
  applySettings(settings: Settings): void {
    this.#settings = { ...this.#defaults, ...structuredClone(settings) };
    this.#push();
    const analysed = this.#numbers.settings;
    if (analysed && !needsAnalysis(changedKeys(analysed, this.#settings), this.#stale)) return;
    if (this.#analysisTimer !== null) clearTimeout(this.#analysisTimer);
    this.#analysisTimer = setTimeout(() => void this.#analyse(), ANALYSIS_WAIT_MS);
  }

  async play(from?: number): Promise<void> {
    const host = this.#host;
    if (!host || host.client.dead) return;
    await host.ctx.resume();
    if (from !== undefined) this.seek(from);
    this.#playing = true;
    this.#toggledAt = performance.now();
    this.#anchor = { frame: this.#anchor.frame, at: host.ctx.currentTime };
    this.#held = true;
    host.client.play(true);
  }

  pause(): void {
    const host = this.#host;
    if (!host) return;
    const frame = Math.round(this.position() * this.#rate);
    this.#playing = false;
    this.#toggledAt = performance.now();
    this.#anchor = { frame, at: host.ctx.currentTime };
    host.client.play(false);
  }

  stop(): void {
    this.pause();
    this.seek(0);
  }

  seek(seconds: number): void {
    const host = this.#host;
    if (!host) return;
    const frame = Math.round(startPoint(seconds, this.#loop, this.duration) * this.#rate);
    this.#anchor = { frame, at: host.ctx.currentTime };
    this.#held = true;
    host.client.seek(frame);
  }

  setLoop(loop: Loop | null): void {
    const host = this.#host;
    this.#loop = loop;
    if (!host) return;
    if (!loop) {
      host.client.loop(false, 0, this.#frames);
      return;
    }
    const start = Math.round(loop.start * this.#rate);
    const end = Math.round(loop.end * this.#rate);
    // the engine jumps into a loop the position is outside of; the clock follows it
    const at = this.position();
    if (at < loop.start || at >= loop.end) {
      this.#anchor = { frame: start, at: host.ctx.currentTime };
      this.#held = true;
    }
    host.client.loop(true, start, end);
  }

  position(): number {
    const host = this.#host;
    if (!host || !this.#rate) return 0;
    const anchor = this.#anchor.frame / this.#rate;
    if (!this.#playing) return anchor;
    const elapsed = this.#held ? 0 : host.ctx.currentTime - this.#anchor.at - (host.ctx.outputLatency || 0);
    return positionAt(anchor, elapsed, this.#loop, this.duration);
  }

  setLaneGain(lane: LaneId, gain: number): void {
    this.#gains[lane] = gain;
    if (this.#host) this.#push();
  }

  meter(): number {
    return this.#playing ? this.#peak : 0;
  }

  async dispose(): Promise<void> {
    this.#ready = false;
    this.#analysing?.abort();
    if (this.#analysisTimer !== null) clearTimeout(this.#analysisTimer);
    this.#api.close();
    const host = this.#host;
    this.#host = null;
    this.#playing = false;
    if (host) {
      host.client.onLevels = () => {};
      host.client.onEnded = () => {};
      host.client.onFatal = () => {};
      host.client.onError = () => {};
      await host.dispose();
    }
  }

  #wire(host: Host): void {
    const c = host.client;
    c.onLevels = (l) => this.#levels(host, l);
    c.onEnded = () => {
      this.#playing = false;
      this.#anchor = { frame: 0, at: host.ctx.currentTime };
    };
    c.onFatal = (reason) => {
      this.#playing = false;
      this.#ready = false;
      this.#onError(`Движок звука остановился: ${reason}`);
    };
    c.onError = (e) => this.#onError(`Движок: ${e.track ? `${e.track} — ` : ""}${e.message}`);
  }

  #levels(host: Host, l: Levels): void {
    this.#peak = l.master.peak;
    this.#anchor = { frame: l.frame, at: host.ctx.currentTime };
    if (l.playing) this.#held = l.held;
    if (this.#playing && !l.playing && performance.now() - this.#toggledAt > SETTLE_MS) this.#playing = false;
  }

  #push(): void {
    const host = this.#host;
    if (!host) return;
    try {
      const served = this.#served?.key === JSON.stringify(this.#settings) ? this.#served : null;
      const racks = served?.racks ?? buildRacks(this.#settings, this.#numbers);
      const lanes = served?.lanes ?? buildLanes(this.#settings, this.#numbers);
      host.client.setRacks(withLaneGains(racks, this.#gains), lanes);
    } catch (error) {
      this.#onError(`Рэк с этими настройками не собрать: ${message(error)}`);
    }
  }

  async #analyse(): Promise<void> {
    this.#analysisTimer = null;
    this.#analysing?.abort();
    const control = new AbortController();
    this.#analysing = control;
    const asked = structuredClone(this.#settings);
    try {
      const reply = await this.#api.analysis(asked, control.signal);
      if (control.signal.aborted) return;
      this.#numbers = reply.analysis;
      const lanes = reply.lanes.map((l) => (Array.isArray(l) ? { slot: l[0], param: l[1], points: l[2] } : l));
      const r = reply.racks;
      this.#served = { key: JSON.stringify(asked), racks: { vocal: parseRack(r.vocal), beat: parseRack(r.beat), master: parseRack(r.master) }, lanes };
      this.#push();
    } catch (error) {
      if (control.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
      if (error instanceof Superseded) this.#onError("Анализ занят другой вкладкой студии. Звук идёт с прошлыми числами.");
      else this.#onError(`Анализ не прошёл: ${message(error)}. Звук идёт с прошлыми числами.`);
    }
  }
}
