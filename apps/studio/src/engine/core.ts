// The engine the AudioWorklet runs: the vocal, beat and master racks of redline_dsp over the
// decoded stems, lined up, summed and mastered, with the A/B sources beside them.
//
// It has no AudioWorklet in it, so node runs it on the same wasm in the tests. Everything
// is driven from two calls: `handle` for a message from the page, `render` for a block.

import { valueAt } from "../core/automation.ts";
import { catalogueOf, paramNumber, type Catalogue } from "../core/diff.ts";
import { DelayLine, align } from "../core/latency.ts";
import { LoudnessMeter } from "../core/loudness.ts";
import type { EffectSpec, Lane, ParamValue, Track } from "../types.ts";
import { lastError, utf8, withText, type Dsp } from "./dsp.ts";
import type { FromEngine, Source, ToEngine } from "./protocol.ts";

const BLOCK = 128;
// a new rack hears this much of what came before its first block, past its latency
const WARM_EXTRA = 4096;
const WARM_MAX = 65536;
// frames of warm-up a new rack goes through per block, beside the live ones
const WARM_CHUNK = 1024;
const CROSSFADE = 1024;
// the fade to and from silence around a seek, a pause or a change of latency
const DIP = 384;
const AB_RAMP_S = 0.03;
const DELAY_CAPACITY = 1 << 17;
const LEVELS_PER_S = 30;

// a lane with its param's name kept in the heap, so the audio thread does not encode it
interface LiveLane {
  lane: Lane;
  name: number;
  len: number;
  value: number;
}

interface Slot {
  effect: string;
  on: boolean;
  params: Record<string, ParamValue>;
}

interface Inst {
  handle: number;
  version: number;
  latency: number;
  lanes: LiveLane[];
  // the slots' own values, for a param a lane lets go of
  slots: Slot[];
  // the master effect's gain and trim, for the gain reduction it shows
  master: { gain: number; trim: number } | null;
}

interface Pending {
  inst: Inst;
  // the next track frame the new rack is fed while it warms up
  cursor: number;
  ready: boolean;
}

class Rack {
  current: Inst | null = null;
  pending: Pending | null = null;
  fading: { old: Inst; at: number } | null = null;
  swapQueued = false;

  readonly track: Track;

  constructor(track: Track) {
    this.track = track;
  }

  version(v: number): Inst | null {
    if (this.pending?.inst.version === v) return this.pending.inst;
    if (this.current?.version === v) return this.current;
    return null;
  }
}

// what a rack heard live at each track frame, the last HISTORY frames of it, so a new rack
// warms up on the same signal
const HISTORY = 2 * WARM_MAX;

class History {
  readonly #data = new Float32Array(2 * HISTORY);
  readonly #at = new Int32Array(HISTORY).fill(-1);

  write(heap: Float32Array, ptr: number, pos: Int32Array, n: number): void {
    const base = ptr >> 2;
    for (let i = 0; i < n; i++) {
      const p = pos[i] as number;
      const k = p & (HISTORY - 1);
      this.#at[k] = p;
      this.#data[2 * k] = heap[base + 2 * i] as number;
      this.#data[2 * k + 1] = heap[base + 2 * i + 1] as number;
    }
  }

  // frames [from, from + n) over the heap at ptr where it has them; the rest stays as it is
  read(heap: Float32Array, ptr: number, from: number, n: number): void {
    const base = ptr >> 2;
    for (let i = 0; i < n; i++) {
      const p = from + i;
      if (p < 0) continue;
      const k = p & (HISTORY - 1);
      if (this.#at[k] !== p) continue;
      heap[base + 2 * i] = this.#data[2 * k] as number;
      heap[base + 2 * i + 1] = this.#data[2 * k + 1] as number;
    }
  }
}

interface Accum {
  peak: number;
  sum: number;
}

const accum = (): Accum => ({ peak: 0, sum: 0 });

export class Engine {
  readonly #dsp: Dsp;
  readonly #rate: number;
  readonly #post: (m: FromEngine) => void;
  readonly #catalogue: Catalogue;
  readonly #racks: Record<Track, Rack> = { vocal: new Rack("vocal"), beat: new Rack("beat"), master: new Rack("master") };
  // heap buffers of WARM_CHUNK stereo frames
  readonly #buf: Record<"vocal" | "beat" | "side" | "mix" | "alt" | "altSide" | "warm" | "warmSide", number>;
  #stems: { frames: number; vocal: Float32Array; beat: Float32Array; original: Float32Array } | null = null;
  #render: Float32Array | null = null;
  #reference: { data: Float32Array; start: number; length: number } | null = null;
  #read = 0;
  #loop = { on: false, start: 0, end: 0 };
  #want = false;
  #running = false;
  #jobs: (() => void)[] = [];
  #env = 0;
  // frames the output stays silent after the delay lines were emptied, until they refill
  #hold = 0;
  #ending = false;
  readonly #gain: Record<Source, number> = { result: 1, original: 0, reference: 0, render: 0 };
  #target: Record<Source, number> = { result: 1, original: 0, reference: 0, render: 0 };
  readonly #pad = { vocal: new DelayLine(DELAY_CAPACITY), beat: new DelayLine(DELAY_CAPACITY) };
  readonly #late = { original: new DelayLine(DELAY_CAPACITY), render: new DelayLine(DELAY_CAPACITY) };
  #total = 0;
  readonly #original = new Float32Array(2 * BLOCK);
  readonly #renderBlock = new Float32Array(2 * BLOCK);
  // the track frame of each frame of the block
  readonly #pos = new Int32Array(BLOCK);
  readonly #heardSide = new History();
  readonly #heardMix = new History();
  readonly #meter: LoudnessMeter;
  #window = 0;
  #levels = { vocal: accum(), beat: accum(), pre: accum(), master: accum() };

  constructor(dsp: Dsp, rate: number, post: (m: FromEngine) => void) {
    this.#dsp = dsp;
    this.#rate = rate;
    this.#post = post;
    this.#catalogue = catalogueOf(JSON.parse(dsp.UTF8ToString(dsp._effects_json())) as EffectSpec[]);
    const names = ["vocal", "beat", "side", "mix", "alt", "altSide", "warm", "warmSide"] as const;
    const buf = {} as Record<(typeof names)[number], number>;
    for (const name of names) {
      const ptr = dsp._malloc(2 * WARM_CHUNK * 4);
      if (!ptr) throw new Error("the wasm heap is full");
      buf[name] = ptr;
    }
    this.#buf = buf;
    this.#meter = new LoudnessMeter(rate, 30);
  }

  handle(m: ToEngine): void {
    switch (m.type) {
      case "stems":
        this.#stems = { frames: m.frames, vocal: m.vocal, beat: m.beat, original: m.original };
        this.#read = Math.min(this.#read, m.frames);
        return;
      case "source":
        if (m.name === "render") this.#render = m.data;
        else this.#reference = m.data && m.length > 0 ? { data: m.data, start: m.start, length: m.length } : null;
        return;
      case "rack":
        return this.#rack(m.track, m.version, m.json, m.lanes);
      case "set":
        return this.#set(m.track, m.version, m.slot, m.param, m.value);
      case "lanes": {
        const inst = this.#racks.vocal.version(m.version);
        if (inst) this.#setLanes(inst, m.lanes);
        return;
      }
      case "play":
        if (m.playing && !this.#stems) return;
        this.#want = m.playing;
        if (m.playing) this.#running = true;
        return;
      case "seek":
        return this.#atSilence(() => this.#jump(m.frame));
      case "loop": {
        const on = m.on && m.end > m.start;
        this.#loop = { on, start: on ? m.start : 0, end: on ? m.end : 0 };
        if (on && (this.#read < m.start || this.#read >= m.end)) this.#atSilence(() => this.#jump(m.start));
        return;
      }
      case "listen":
        this.#target = { ...m.gains };
        if (!this.#running) Object.assign(this.#gain, this.#target);
        return;
    }
  }

  // fills both channels; a block of any length is taken in parts of 128 frames
  render(left: Float32Array, right: Float32Array): void {
    for (let at = 0; at < left.length; at += BLOCK) {
      this.#block(left, right, at, Math.min(BLOCK, left.length - at));
    }
  }

  #error(message: string, track?: Track, version?: number): void {
    const m: FromEngine = { type: "error", message };
    if (track !== undefined) m.track = track;
    if (version !== undefined) m.version = version;
    this.#post(m);
  }

  #rack(track: Track, version: number, json: string, lanes: Lane[]): void {
    const dsp = this.#dsp;
    const handle = withText(dsp, json, (p, n) => dsp._rack_new(this.#rate, p, n));
    if (!handle) return this.#error(lastError(dsp), track, version);
    const latency = dsp._rack_latency(handle);
    const slots = rackSlots(json);
    const inst: Inst = {
      handle,
      version,
      latency,
      lanes: track === "vocal" ? this.#encode(lanes) : [],
      slots,
      master: track === "master" ? masterParams(slots) : null,
    };
    const rack = this.#racks[track];
    if (rack.pending) this.#free(rack.pending.inst);
    rack.pending = null;
    rack.swapQueued = false;
    if (!rack.current || !this.#running) {
      this.#install(rack, inst);
      return;
    }
    rack.pending = { inst, cursor: this.#read - Math.min(WARM_MAX, latency + WARM_EXTRA), ready: false };
  }

  #install(rack: Rack, inst: Inst): void {
    if (rack.current) this.#free(rack.current);
    if (rack.fading) this.#free(rack.fading.old);
    rack.fading = null;
    rack.current = inst;
    this.#realign();
    this.#post({ type: "rack", track: rack.track, version: inst.version, latency: inst.latency });
  }

  #free(inst: Inst): void {
    this.#dsp._rack_free(inst.handle);
    this.#release(inst.lanes);
    inst.lanes = [];
  }

  #encode(lanes: Lane[]): LiveLane[] {
    const dsp = this.#dsp;
    return lanes.map((lane) => {
      const bytes = utf8(lane.param);
      const name = dsp._malloc(Math.max(1, bytes.length));
      if (!name) throw new Error("the wasm heap is full");
      dsp.HEAPU8.set(bytes, name);
      return { lane, name, len: bytes.length, value: Number.NaN };
    });
  }

  #release(lanes: LiveLane[]): void {
    for (const l of lanes) this.#dsp._free(l.name);
  }

  // a param no lane drives any more goes back to its slot's own value
  #setLanes(inst: Inst, lanes: Lane[]): void {
    const old = inst.lanes;
    inst.lanes = this.#encode(lanes);
    for (const l of old) {
      const { slot, param } = l.lane;
      if (lanes.some((n) => n.slot === slot && n.param === param)) continue;
      const value = this.#own(inst, slot, param);
      if (!Number.isNaN(value)) this.#dsp._rack_set(inst.handle, slot, l.name, l.len, value);
    }
    this.#release(old);
  }

  #own(inst: Inst, slot: number, param: string): number {
    const s = inst.slots[slot];
    const spec = s && this.#catalogue.get(s.effect)?.params.find((p) => p.name === param);
    return s && spec ? paramNumber(spec, s.params[param]) : Number.NaN;
  }

  #realign(): void {
    const latency = (t: Track): number => this.#racks[t].current?.latency ?? 0;
    let a = align(latency("vocal"), latency("beat"), latency("master"));
    if (a.total > DELAY_CAPACITY) {
      this.#error(`the racks' latency of ${a.total} frames is more than the page lines up`);
      a = { vocalPad: 0, beatPad: 0, total: 0 };
    }
    this.#pad.vocal.setDelay(a.vocalPad);
    this.#pad.beat.setDelay(a.beatPad);
    this.#late.original.setDelay(a.total);
    this.#late.render.setDelay(a.total);
    this.#total = a.total;
    this.#hold = a.total;
  }

  #set(track: Track, version: number, slot: number, param: string, value: number): void {
    const inst = this.#racks[track].version(version);
    if (!inst) return;
    const s = inst.slots[slot];
    if (s) s.params[param] = value;
    // a lane rules its param; the value set here is the one it goes back to
    if (inst.lanes.some((l) => l.lane.slot === slot && l.lane.param === param)) return;
    const dsp = this.#dsp;
    const done = withText(dsp, param, (p, n) => dsp._rack_set(inst.handle, slot, p, n, value));
    if (done !== 0) return this.#error(lastError(dsp), track, version);
    if (inst.master && (param === "gain" || param === "trim")) inst.master[param] = value;
  }

  // `job` runs once the output is silent: at once when nothing plays
  #atSilence(job: () => void): void {
    if (!this.#running) {
      job();
      return;
    }
    this.#jobs.push(job);
  }

  #jump(frame: number): void {
    const frames = this.#stems?.frames ?? 0;
    this.#read = Math.max(0, Math.min(Math.round(frame), Math.max(0, frames - 1)));
    for (const d of [this.#pad.vocal, this.#pad.beat, this.#late.original, this.#late.render]) {
      const keep = d.delay;
      d.setDelay(0);
      d.setDelay(keep);
    }
    this.#hold = this.#total;
  }

  // the track has played out and faded: back to its start, with nothing of its end left in the racks
  #finish(): void {
    this.#ending = false;
    this.#want = false;
    this.#jump(0);
    for (const rack of Object.values(this.#racks)) {
      for (const inst of [rack.current, rack.pending?.inst, rack.fading?.old]) {
        if (inst && this.#dsp._rack_reset(inst.handle, this.#rate) !== 0) {
          this.#error(lastError(this.#dsp), rack.track, inst.version);
        }
      }
    }
    this.#post({ type: "ended" });
  }

  #heap(): Float32Array {
    return this.#dsp.HEAPF32;
  }

  // a stem's frames [from, from + n) into the heap at ptr, silent outside the track
  #fill(stem: Float32Array, ptr: number, from: number, n: number, add = false): void {
    const heap = this.#heap();
    const base = ptr >> 2;
    const frames = stem.length / 2;
    for (let i = 0; i < n; i++) {
      const p = from + i;
      const inside = p >= 0 && p < frames;
      const l = inside ? (stem[2 * p] as number) : 0;
      const r = inside ? (stem[2 * p + 1] as number) : 0;
      if (add) {
        heap[base + 2 * i] = (heap[base + 2 * i] as number) + l;
        heap[base + 2 * i + 1] = (heap[base + 2 * i + 1] as number) + r;
      } else {
        heap[base + 2 * i] = l;
        heap[base + 2 * i + 1] = r;
      }
    }
  }

  #process(rack: Rack, inst: Inst, io: number, side: number, n: number, seconds: number | null): void {
    const dsp = this.#dsp;
    if (seconds !== null && inst.lanes.length) this.#lanes(inst, seconds);
    if (dsp._rack_process(inst.handle, io, side, io, n) !== 0) {
      const message = lastError(dsp);
      // a rack that panicked is freed by the core; the track goes on without it
      if (rack.current === inst) rack.current = null;
      this.#release(inst.lanes);
      inst.lanes = [];
      this.#error(message, rack.track, inst.version);
      this.#heap().fill(0, io >> 2, (io >> 2) + 2 * n);
    }
  }

  #lanes(inst: Inst, seconds: number): void {
    const dsp = this.#dsp;
    for (const l of inst.lanes) {
      const v = valueAt(l.lane.points, seconds);
      if (v === l.value) continue;
      l.value = v;
      dsp._rack_set(inst.handle, l.lane.slot, l.name, l.len, v);
    }
  }

  // a new rack catches up with the live position before it takes over
  #warm(rack: Rack, start: number): void {
    const pending = rack.pending;
    const stems = this.#stems;
    if (!pending || pending.ready || !stems) return;
    if (pending.cursor > start) pending.cursor = start - Math.min(WARM_MAX, pending.inst.latency + WARM_EXTRA);
    const n = Math.min(WARM_CHUNK, start - pending.cursor);
    if (n > 0) {
      const { warm, warmSide } = this.#buf;
      const from = pending.cursor;
      let side = 0;
      // what the live rack heard, where it is still kept; the raw stems before that
      if (rack.track === "vocal") {
        this.#fill(stems.vocal, warm, from, n);
        this.#fill(stems.beat, warmSide, from, n);
        this.#heardSide.read(this.#heap(), warmSide, from, n);
        side = warmSide;
      } else if (rack.track === "beat") {
        this.#fill(stems.beat, warm, from, n);
      } else {
        this.#fill(stems.vocal, warm, from, n);
        this.#fill(stems.beat, warm, from, n, true);
        this.#heardMix.read(this.#heap(), warm, from, n);
      }
      this.#process(rack, pending.inst, warm, side, n, rack.track === "vocal" ? from / this.#rate : null);
      pending.cursor += n;
    }
    pending.ready = pending.cursor === start;
  }

  // one track's rack over the block in io, in place
  #track(rack: Rack, io: number, side: number, n: number, start: number): void {
    const seconds = rack.track === "vocal" ? start / this.#rate : null;
    this.#warm(rack, start);
    const pending = rack.pending;
    const { alt, altSide } = this.#buf;
    const heap = this.#heap();
    if (pending?.ready && !rack.fading) {
      if (!rack.current) {
        rack.pending = null;
        this.#install(rack, pending.inst);
      } else if (pending.inst.latency === rack.current.latency) {
        rack.pending = null;
        rack.fading = { old: rack.current, at: 0 };
        rack.current = pending.inst;
        this.#post({ type: "rack", track: rack.track, version: pending.inst.version, latency: pending.inst.latency });
      } else if (!rack.swapQueued) {
        rack.swapQueued = true;
        const inst = pending.inst;
        this.#jobs.push(() => {
          if (rack.pending?.inst !== inst) return;
          rack.pending = null;
          rack.swapQueued = false;
          this.#install(rack, inst);
        });
      }
    }
    if (rack.pending?.ready) {
      // the new rack waits for silence to take over and goes on hearing the live input
      heap.copyWithin(alt >> 2, io >> 2, (io >> 2) + 2 * n);
      if (side) heap.copyWithin(altSide >> 2, side >> 2, (side >> 2) + 2 * n);
      this.#process(rack, rack.pending.inst, alt, side ? altSide : 0, n, seconds);
    }
    const current = rack.current;
    if (rack.fading && current) {
      heap.copyWithin(alt >> 2, io >> 2, (io >> 2) + 2 * n);
      if (side) heap.copyWithin(altSide >> 2, side >> 2, (side >> 2) + 2 * n);
      this.#process(rack, rack.fading.old, io, side, n, seconds);
      this.#process(rack, current, alt, side ? altSide : 0, n, seconds);
      const h = this.#heap();
      const a = io >> 2;
      const b = alt >> 2;
      for (let i = 0; i < n; i++) {
        const g = Math.min(1, (rack.fading.at + i + 1) / CROSSFADE);
        h[a + 2 * i] = (h[a + 2 * i] as number) * (1 - g) + (h[b + 2 * i] as number) * g;
        h[a + 2 * i + 1] = (h[a + 2 * i + 1] as number) * (1 - g) + (h[b + 2 * i + 1] as number) * g;
      }
      rack.fading.at += n;
      if (rack.fading.at >= CROSSFADE) {
        this.#free(rack.fading.old);
        rack.fading = null;
      }
    } else if (current) {
      this.#process(rack, current, io, side, n, seconds);
    }
  }

  #block(left: Float32Array, right: Float32Array, off: number, n: number): void {
    const stems = this.#stems;
    if (!this.#running || !stems) {
      left.fill(0, off, off + n);
      right.fill(0, off, off + n);
      return;
    }
    const { vocal, beat, side, mix } = this.#buf;
    const start = this.#read;
    // the stems' frames of this block, with the loop's wrap; past the end the racks are fed
    // silence until what they hold has come out
    const last = stems.frames + this.#total;
    const v = vocal >> 2;
    const b = beat >> 2;
    let heap = this.#heap();
    for (let i = 0; i < n; i++) {
      const p = this.#read;
      const inside = p < stems.frames;
      this.#pos[i] = p;
      for (let c = 0; c < 2; c++) {
        heap[v + 2 * i + c] = inside ? (stems.vocal[2 * p + c] as number) : 0;
        heap[b + 2 * i + c] = inside ? (stems.beat[2 * p + c] as number) : 0;
        this.#original[2 * i + c] = inside ? (stems.original[2 * p + c] as number) : 0;
        this.#renderBlock[2 * i + c] = inside && this.#render ? (this.#render[2 * p + c] ?? 0) : 0;
      }
      if (this.#loop.on) {
        if (++this.#read >= this.#loop.end) this.#read = this.#loop.start;
      } else if (this.#read < last) {
        this.#read++;
      }
    }

    this.#track(this.#racks.beat, beat, 0, n, start);
    heap = this.#heap();
    heap.copyWithin(side >> 2, b, b + 2 * n);
    this.#heardSide.write(heap, side, this.#pos, n);
    this.#track(this.#racks.vocal, vocal, side, n, start);
    heap = this.#heap();
    this.#pad.vocal.process(heap.subarray(v, v + 2 * n), n);
    this.#pad.beat.process(heap.subarray(b, b + 2 * n), n);
    const x = mix >> 2;
    const L = this.#levels;
    for (let i = 0; i < 2 * n; i++) {
      const a = heap[v + i] as number;
      const c = heap[b + i] as number;
      const s = a + c;
      heap[x + i] = s;
      L.vocal.peak = Math.max(L.vocal.peak, Math.abs(a));
      L.vocal.sum += a * a;
      L.beat.peak = Math.max(L.beat.peak, Math.abs(c));
      L.beat.sum += c * c;
      L.pre.peak = Math.max(L.pre.peak, Math.abs(s));
    }
    this.#heardMix.write(heap, mix, this.#pos, n);
    this.#track(this.#racks.master, mix, 0, n, start);
    heap = this.#heap();
    const result = heap.subarray(x, x + 2 * n);
    this.#meter.push(result, n);
    this.#late.original.process(this.#original, n);
    this.#late.render.process(this.#renderBlock, n);

    const ref = this.#reference;
    let refAt = ref ? ref.start + mod(this.#heard(start) - (this.#loop.on ? this.#loop.start : 0), ref.length) : 0;
    const envStep = 1 / DIP;
    const gainStep = 1 / (AB_RAMP_S * this.#rate);
    const target = this.#want && this.#jobs.length === 0 && this.#hold <= 0 ? 1 : 0;
    this.#hold = Math.max(0, this.#hold - n);
    for (let i = 0; i < n; i++) {
      this.#env = approach(this.#env, target, envStep);
      for (const s of ["result", "original", "reference", "render"] as const) {
        this.#gain[s] = approach(this.#gain[s], this.#target[s], gainStep);
      }
      const g = this.#gain;
      for (let c = 0; c < 2; c++) {
        const r = result[2 * i + c] as number;
        L.master.peak = Math.max(L.master.peak, Math.abs(r));
        L.master.sum += r * r;
        const refSample = ref ? (ref.data[2 * refAt + c] ?? 0) : 0;
        const out =
          this.#env *
          (g.result * r +
            g.original * (this.#original[2 * i + c] as number) +
            g.render * (this.#renderBlock[2 * i + c] as number) +
            g.reference * refSample);
        (c === 0 ? left : right)[off + i] = out;
      }
      if (ref && ++refAt >= ref.start + ref.length) refAt = ref.start;
    }

    this.#window += n;
    if (this.#window >= this.#rate / LEVELS_PER_S) this.#report();
    // the fade out starts on the next block, which is silent: this one still has the end in it
    if (!this.#loop.on && this.#read >= last && !this.#ending) {
      this.#ending = true;
      this.#want = false;
      this.#jobs.push(() => this.#finish());
    }
    if (this.#env === 0) {
      const jobs = this.#jobs;
      this.#jobs = [];
      for (const job of jobs) job();
      if (!this.#want && this.#jobs.length === 0) {
        this.#running = false;
        this.#report();
      }
    }
  }

  #heard(read: number): number {
    let heard = read - this.#total;
    if (this.#loop.on && heard < this.#loop.start) heard += this.#loop.end - this.#loop.start;
    return Math.max(0, heard);
  }

  #report(): void {
    const n = Math.max(1, this.#window);
    const L = this.#levels;
    const level = (a: Accum) => ({ peak: a.peak, rms: Math.sqrt(a.sum / (2 * n)) });
    const master = this.#racks.master.current?.master ?? null;
    let gr: number | null = null;
    if (master !== null) {
      const gain = master.gain + master.trim;
      gr = L.pre.peak > 1e-6 && L.master.peak > 1e-9 ? Math.max(0, db(L.pre.peak) + gain - db(L.master.peak)) : 0;
    }
    const seconds = this.#loop.on ? (this.#loop.end - this.#loop.start) / this.#rate : 10;
    this.#post({
      type: "levels",
      frame: this.#heard(this.#read),
      playing: this.#running,
      vocal: level(L.vocal),
      beat: level(L.beat),
      master: { ...level(L.master), gr },
      lufs: this.#meter.lufs(Math.min(30, Math.max(3, seconds))),
      latency: this.#total,
    });
    this.#window = 0;
    this.#levels = { vocal: accum(), beat: accum(), pre: accum(), master: accum() };
  }
}

function approach(v: number, target: number, step: number): number {
  if (v < target) return Math.min(target, v + step);
  if (v > target) return Math.max(target, v - step);
  return v;
}

function mod(a: number, m: number): number {
  return ((a % m) + m) % m;
}

function db(x: number): number {
  return 20 * Math.log10(x);
}

// the core has taken this JSON already, so it parses
function rackSlots(json: string): Slot[] {
  const rack = JSON.parse(json) as { effects?: { effect: string; on?: boolean; params?: Record<string, ParamValue> }[] };
  return (rack.effects ?? []).map((e) => ({ effect: e.effect, on: e.on !== false, params: { ...e.params } }));
}

// gain and trim of the master effect: what it raises the mix by before its clip
function masterParams(slots: Slot[]): { gain: number; trim: number } | null {
  const master = slots.find((e) => e.effect === "master" && e.on);
  if (!master) return null;
  const gain = typeof master.params.gain === "number" ? master.params.gain : 0;
  const trim = typeof master.params.trim === "number" ? master.params.trim : 0;
  return { gain, trim };
}
