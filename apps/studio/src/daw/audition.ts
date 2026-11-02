// A block or a pattern of an own beat played live from its score, round and round, in its own
// 48 kHz context through BeatEngine: an edit sent while it plays is heard at once. Between them
// the same engine plays one hit of a sound.

import type { Library, ZoneData } from "../beat/api.ts";
import { startBeat, type BeatClient, type BeatHost, type BeatResult } from "../beat/client.ts";
import { Latest } from "../beat/flow.ts";
import { barFrame, serialize, type Score } from "../beat/model.ts";
import { positionAt } from "./player.ts";

export const AUDITION_RATE = 48000;

export interface Range {
  start: number;
  end: number;
}

// [start, end) frames of `bars` whole bars from `fromBar`, cut at the song's last frame
export function auditionRange(bpm: number, fromBar: number, bars: number, rate: number, length: number): Range {
  if (!Number.isSafeInteger(fromBar) || fromBar < 0) throw new RangeError(`a block cannot start at bar ${fromBar}`);
  if (!Number.isSafeInteger(bars) || bars < 1) throw new RangeError(`a block of ${bars} bars`);
  const start = barFrame(fromBar, bpm, rate);
  const end = Math.min(barFrame(fromBar + bars, bpm, rate), length);
  if (end <= start) throw new RangeError(`bar ${fromBar + 1} is past the end of the beat`);
  return { start, end };
}

// a zone to load: a session's listed one, or a pack sound's single file
export interface ZoneRef {
  library: string;
  file: string;
  vel: [number, number];
}

const PACK = "pack-";

// The zones of the libraries the score plays: those the session lists as fetched, and the one file
// of a pack sound the session does not list yet (added since the newest saved score).
export function wantedZones(libraries: readonly Library[], score: Score): ZoneRef[] {
  const used = new Set<string>();
  for (const s of Object.values(score.sounds)) if (typeof s.library === "string") used.add(s.library);
  const listed = new Set(libraries.map((l) => l.name));
  const out: ZoneRef[] = libraries.filter((l) => l.fetched && used.has(l.name)).flatMap((l) => l.zones.map((z) => ({ library: z.library, file: z.file, vel: z.vel })));
  for (const name of used) if (name.startsWith(PACK) && !listed.has(name)) out.push({ library: name, file: "0", vel: [0, 127] });
  return out;
}

export type ZoneClient = Pick<BeatClient, "loadZone" | "rebuildSamplers">;

// One engine's zones: each is asked for once, whether it loaded or not, so an edit never fetches
// again what the engine has; the samplers are rebuilt only after a new one.
// Loads each zone into an engine once. A zone on its way is waited for by a second load rather
// than skipped; one that failed is asked for again next time, so a passing error does not
// silence a sound for the whole session.
export class ZoneLoader {
  readonly #fetch: (library: string, file: string) => Promise<ZoneData>;
  readonly #done = new Set<string>();
  readonly #pending = new Map<string, Promise<string | null>>();

  constructor(fetch: (library: string, file: string) => Promise<ZoneData>) {
    this.#fetch = fetch;
  }

  async load(client: ZoneClient, wanted: readonly ZoneRef[]): Promise<string[]> {
    const problems: string[] = [];
    const mine: Promise<string | null>[] = [];
    const theirs: Promise<string | null>[] = [];
    // one after another, as the engine takes them
    let chain: Promise<unknown> = Promise.resolve();
    for (const z of wanted) {
      const key = `${z.library}\u0000${z.file}`;
      if (this.#done.has(key)) continue;
      const pending = this.#pending.get(key);
      if (pending) {
        theirs.push(pending);
        continue;
      }
      const job = chain.then(() => this.#one(client, z, key));
      chain = job;
      this.#pending.set(key, job);
      mine.push(job);
    }
    let loaded = 0;
    for (const job of mine) {
      const problem = await job;
      if (problem === null) loaded++;
      else problems.push(problem);
    }
    await Promise.all(theirs);
    if (loaded) {
      const rebuilt = await client.rebuildSamplers();
      if (!rebuilt.ok) problems.push(...rebuilt.errors);
    }
    return problems;
  }

  async #one(client: ZoneClient, z: ZoneRef, key: string): Promise<string | null> {
    try {
      const data = await this.#fetch(z.library, z.file);
      const result = await client.loadZone({
        library: z.library,
        root: data.root,
        velLo: z.vel[0],
        velHi: z.vel[1],
        rate: data.rate,
        samples: new Int16Array(data.pcm),
        channels: data.channels === 2 ? 2 : 1,
      });
      if (!result.ok) return `${z.library} ${z.file}: ${result.errors.join("; ")}`;
      this.#done.add(key);
      return null;
    } catch (error) {
      return `${z.library} ${z.file}: ${error instanceof Error ? error.message : String(error)}`;
    } finally {
      this.#pending.delete(key);
    }
  }
}

export interface AuditionOptions {
  open?: () => Promise<BeatHost>;
  // the zones a score needs and where they come from; loaded once a score is in the engine
  zones?: (score: Score) => readonly ZoneRef[];
  fetchZone?: (library: string, file: string) => Promise<ZoneData>;
  onError?: (message: string) => void;
}

interface Block {
  fromBar: number;
  bars: number;
}

export class Audition {
  readonly #open: () => Promise<BeatHost>;
  readonly #zones: ((score: Score) => readonly ZoneRef[]) | null;
  readonly #fetchZone: ((library: string, file: string) => Promise<ZoneData>) | null;
  readonly #onError: (message: string) => void;
  readonly #live: Latest<{ score: Score; block: Block }>;
  #host: Promise<BeatHost> | null = null;
  #ready: BeatHost | null = null;
  #loader: ZoneLoader | null = null;
  #loaded = false;
  #closed = false;
  #playing = false;
  // starts still under way: a hit waits for none of them and does not cut in
  #starting = 0;
  // a start or a stop after another makes the older one's awaits come to nothing
  #seq = 0;
  #block: Block = { fromBar: 0, bars: 1 };
  #range: Range = { start: 0, end: 0 };
  #anchor = { frame: 0, at: 0 };

  constructor(options: AuditionOptions = {}) {
    this.#open = options.open ?? (() => startBeat(AUDITION_RATE));
    this.#zones = options.zones ?? null;
    this.#fetchZone = options.fetchZone ?? null;
    this.#onError = options.onError ?? (() => {});
    this.#live = new Latest((next) => this.#send(next.score, next.block));
  }

  get playing(): boolean {
    return this.#playing;
  }

  async start(score: Score, fromBar: number, bars: number): Promise<void> {
    if (this.#closed) return;
    const seq = ++this.#seq;
    this.#starting++;
    try {
      await this.#begin(seq, score, fromBar, bars);
    } finally {
      this.#starting--;
    }
  }

  async #begin(seq: number, score: Score, fromBar: number, bars: number): Promise<void> {
    const host = await this.#engine();
    if (seq !== this.#seq) return;
    await host.ctx.resume();
    const client = host.client;
    if (!(await this.#take(client, score, "Партитура не играет"))) return;
    if (seq !== this.#seq) return;
    let range: Range;
    try {
      range = auditionRange(score.bpm, fromBar, bars, host.ctx.sampleRate, client.length);
    } catch (error) {
      this.#onError(error instanceof Error ? error.message : String(error));
      return;
    }
    this.#block = { fromBar, bars };
    this.#range = range;
    if (!this.#check(await client.loop(range), "Петля не встала")) return;
    if (!this.#check(await client.seek(range.start), "Перемотка не прошла")) return;
    if (seq !== this.#seq) return;
    this.#anchor = { frame: range.start, at: host.ctx.currentTime };
    if (!this.#check(await client.play(), "Звук не пошёл")) return;
    if (seq !== this.#seq) {
      await client.stop();
      return;
    }
    this.#playing = true;
  }

  update(score: Score): void {
    if (this.#playing) this.#live.push({ score, block: this.#block });
  }

  // another score and block in place, from where it plays: the loop moves, nothing restarts
  retarget(score: Score, fromBar: number, bars: number): void {
    if (this.#playing) this.#live.push({ score, block: { fromBar, bars } });
  }

  // The score once from its start, with no loop: one hit of a sound. Not while a block plays or
  // starts, so it never cuts into one; false when it was not played.
  async hit(score: Score): Promise<boolean> {
    if (this.#closed || this.#playing || this.#starting > 0) return false;
    const seq = this.#seq;
    const idle = (): boolean => seq === this.#seq && !this.#playing && this.#starting === 0;
    const host = await this.#engine();
    if (!idle()) return false;
    await host.ctx.resume();
    const client = host.client;
    if (!idle() || !(await this.#take(client, score, "Звук не играет")) || !idle()) return false;
    if (!this.#check(await client.loop(null), "Петля не снялась")) return false;
    if (!idle() || !this.#check(await client.seek(0), "Перемотка не прошла")) return false;
    if (!idle()) return false;
    return this.#check(await client.play(), "Звук не пошёл");
  }

  async stop(): Promise<void> {
    this.#seq++;
    this.#playing = false;
    if (this.#ready) await this.#ready.client.stop();
  }

  // the song second heard, from the context clock between the engine's reports
  position(): number {
    const host = this.#ready;
    if (!host) return 0;
    const rate = host.ctx.sampleRate;
    const loop = { start: this.#range.start / rate, end: this.#range.end / rate };
    if (!this.#playing) return this.#anchor.frame / rate;
    const elapsed = host.ctx.currentTime - this.#anchor.at - (host.ctx.outputLatency || 0);
    return positionAt(this.#anchor.frame / rate, elapsed, loop, Infinity);
  }

  async dispose(): Promise<void> {
    this.#closed = true;
    this.#seq++;
    this.#playing = false;
    const host = this.#host;
    this.#host = null;
    this.#ready = null;
    this.#loader = null;
    if (host) await host.then((h) => h.dispose(), () => {});
  }

  #engine(): Promise<BeatHost> {
    if (!this.#host) {
      this.#host = (async () => {
        const host = await this.#open();
        host.client.onFatal = (message) => {
          this.#playing = false;
          this.#onError(`Движок бита остановился: ${message}`);
        };
        host.client.onEnded = () => {
          this.#playing = false;
        };
        host.client.subscribe((p) => {
          this.#anchor = { frame: p.frame, at: host.ctx.currentTime };
        });
        this.#loader = this.#fetchZone ? new ZoneLoader(this.#fetchZone) : null;
        this.#ready = host;
        return host;
      })();
      // a failed start is tried again at the next press
      this.#host.catch(() => {
        this.#host = null;
      });
    }
    return this.#host;
  }

  // the score into the engine, then the zones it still lacks: a zone needs a loaded score
  async #take(client: BeatClient, score: Score, what: string): Promise<boolean> {
    const text = serialize(score);
    if (!this.#check(await (this.#loaded ? client.update(text) : client.load(text)), what)) return false;
    this.#loaded = true;
    await this.#loadZones(client, score);
    return true;
  }

  async #loadZones(client: BeatClient, score: Score): Promise<void> {
    const loader = this.#loader;
    if (!loader || !this.#zones) return;
    const problems = await loader.load(client, this.#zones(score));
    if (problems.length) this.#onError(`Не все сэмплы загрузились: ${problems.slice(0, 3).join("; ")}`);
  }

  async #send(score: Score, block: Block): Promise<void> {
    const host = this.#ready;
    if (!host || !this.#playing) return;
    if (!this.#check(await host.client.update(serialize(score)), "Живой звук не обновился")) return;
    await this.#loadZones(host.client, score);
    // a new tempo, a shorter song or another block moves the loop's frames
    let range: Range;
    try {
      range = auditionRange(score.bpm, block.fromBar, block.bars, host.ctx.sampleRate, host.client.length);
    } catch (error) {
      this.#onError(error instanceof Error ? error.message : String(error));
      return;
    }
    this.#block = block;
    if (range.start === this.#range.start && range.end === this.#range.end) return;
    this.#range = range;
    this.#check(await host.client.loop(range), "Петля не встала");
  }

  #check(result: BeatResult, what: string): boolean {
    if (result.ok) return true;
    this.#onError(`${what}: ${result.errors.slice(0, 3).join("; ")}`);
    return false;
  }
}
