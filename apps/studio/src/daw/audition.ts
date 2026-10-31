// One block of an own beat played live from its score, round and round, in its own 48 kHz
// context through BeatEngine: an edit sent while it plays is heard at once.

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

// the sampler zones of the fetched libraries into the engine; the problems come back as text
export async function loadZones(client: BeatClient, libraries: readonly Library[], zone: (library: string, file: string) => Promise<ZoneData>): Promise<string[]> {
  const problems: string[] = [];
  const zones = libraries.filter((l) => l.fetched).flatMap((l) => l.zones);
  if (!zones.length) return problems;
  for (const z of zones) {
    try {
      const data = await zone(z.library, z.file);
      const result = await client.loadZone({
        library: z.library,
        root: data.root,
        velLo: z.vel[0],
        velHi: z.vel[1],
        rate: data.rate,
        samples: new Int16Array(data.pcm),
        channels: data.channels === 2 ? 2 : 1,
      });
      if (!result.ok) problems.push(`${z.library} ${z.file}: ${result.errors.join("; ")}`);
    } catch (error) {
      problems.push(`${z.library} ${z.file}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  const rebuilt = await client.rebuildSamplers();
  if (!rebuilt.ok) problems.push(...rebuilt.errors);
  return problems;
}

export interface AuditionOptions {
  open?: () => Promise<BeatHost>;
  // run once on the new engine before the first score, e.g. loadZones
  prepare?: (client: BeatClient) => Promise<string[]>;
  onError?: (message: string) => void;
}

export class Audition {
  readonly #open: () => Promise<BeatHost>;
  readonly #prepare: ((client: BeatClient) => Promise<string[]>) | null;
  readonly #onError: (message: string) => void;
  readonly #live: Latest<Score>;
  #host: Promise<BeatHost> | null = null;
  #ready: BeatHost | null = null;
  #loaded = false;
  #closed = false;
  #playing = false;
  // a start or a stop after another makes the older one's awaits come to nothing
  #seq = 0;
  #block = { fromBar: 0, bars: 1 };
  #range: Range = { start: 0, end: 0 };
  #anchor = { frame: 0, at: 0 };

  constructor(options: AuditionOptions = {}) {
    this.#open = options.open ?? (() => startBeat(AUDITION_RATE));
    this.#prepare = options.prepare ?? null;
    this.#onError = options.onError ?? (() => {});
    this.#live = new Latest((score) => this.#send(score));
  }

  get playing(): boolean {
    return this.#playing;
  }

  async start(score: Score, fromBar: number, bars: number): Promise<void> {
    if (this.#closed) return;
    const seq = ++this.#seq;
    const host = await this.#engine();
    if (seq !== this.#seq) return;
    await host.ctx.resume();
    const client = host.client;
    const text = serialize(score);
    if (!this.#check(await (this.#loaded ? client.update(text) : client.load(text)), "Партитура не играет")) return;
    this.#loaded = true;
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
    if (this.#playing) this.#live.push(score);
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
        if (this.#prepare) {
          const problems = await this.#prepare(host.client);
          if (problems.length) this.#onError(`Не все сэмплы загрузились: ${problems.slice(0, 3).join("; ")}`);
        }
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

  async #send(score: Score): Promise<void> {
    const host = this.#ready;
    if (!host || !this.#playing) return;
    if (!this.#check(await host.client.update(serialize(score)), "Живой звук не обновился")) return;
    // a new tempo or a shorter song moves the block's frames
    let range: Range;
    try {
      range = auditionRange(score.bpm, this.#block.fromBar, this.#block.bars, host.ctx.sampleRate, host.client.length);
    } catch (error) {
      this.#onError(error instanceof Error ? error.message : String(error));
      return;
    }
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
