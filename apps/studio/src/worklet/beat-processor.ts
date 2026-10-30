// The AudioWorklet of the beat page around a BeatEngine. As the studio's worklet, it gets the
// wasm bytes in the page's first message; commands that come before the engine is up wait.

import { BeatEngine, type BeatNotice } from "../beat/engine.ts";
import type { BeatCommand, BeatResult, FromBeat, ToBeat } from "../beat/protocol.ts";
import createRedlineDsp from "../engine/redline_dsp.js";

const BLOCK = 128;
const REPORTS_PER_S = 15;

type Command = BeatCommand & { id: number };

function run(engine: BeatEngine, m: Command): BeatResult {
  switch (m.type) {
    case "load":
      return engine.load(m.score);
    case "update":
      return engine.update(m.score);
    case "rack":
      return engine.setRack(m.sound, m.rack);
    case "master":
      return engine.setMasterRack(m.rack);
    case "zone":
      return engine.loadZone(m.zone);
    case "rebuild":
      return engine.rebuildSamplers();
    case "seek":
      return engine.seek(m.frame);
    case "play":
      return engine.play();
    case "stop":
      return engine.stop();
    case "loop":
      if (m.range) return engine.loop(m.range.start, m.range.end);
      engine.clearLoop();
      return { ok: true, rebuilt: false, warnings: [] };
    case "repeat":
      engine.repeat = m.on;
      return { ok: true, rebuilt: false, warnings: [] };
  }
}

class BeatProcessor extends AudioWorkletProcessor {
  #engine: BeatEngine | null = null;
  #waiting: Command[] = [];
  #dead = false;
  readonly #mix = new Float32Array(2 * BLOCK);
  readonly #spare = new Float32Array(BLOCK);
  // the last command handled, for the reports after it
  #last = 0;
  #since = 0;
  #peakLeft = 0;
  #peakRight = 0;
  #wasPlaying = false;

  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent<ToBeat>) => {
      const m = event.data;
      if (this.#dead) return;
      if (m.type === "wasm") void this.#start(m.bytes);
      else if (this.#engine) this.#handle(this.#engine, m);
      else this.#waiting.push(m);
    };
  }

  #post(m: FromBeat): void {
    this.port.postMessage(m);
  }

  async #start(bytes: ArrayBuffer): Promise<void> {
    try {
      const dsp = await createRedlineDsp({
        instantiateWasm: (imports, done) => {
          WebAssembly.instantiate(bytes, imports).then(
            (made) => done(made.instance),
            (error: unknown) => this.#post({ type: "error", message: `the wasm did not load: ${String(error)}` }),
          );
          return {};
        },
      });
      const engine = new BeatEngine(dsp, sampleRate, (n: BeatNotice) => this.#notice(n));
      this.#engine = engine;
      this.#post({ type: "ready" });
      for (const m of this.#waiting) this.#handle(engine, m);
      this.#waiting = [];
    } catch (error) {
      this.#post({ type: "error", message: `the beat engine did not start: ${String(error)}` });
    }
  }

  #handle(engine: BeatEngine, m: Command): void {
    const result = run(engine, m);
    this.#last = m.id;
    this.#post({ type: "result", id: m.id, result, length: engine.length, latency: engine.latency });
    // a seek or a stop shows on the page at once, not at the next report
    if (!engine.playing && !engine.dead) this.#report(engine);
  }

  #notice(n: BeatNotice): void {
    if (n.type === "fatal") {
      this.#dead = true;
      this.#post({ type: "fatal", message: n.message });
    } else {
      this.#post({ type: "ended" });
    }
  }

  #report(engine: BeatEngine): void {
    this.#post({
      type: "position",
      frame: engine.position,
      playing: engine.playing,
      peakLeft: this.#peakLeft,
      peakRight: this.#peakRight,
      after: this.#last,
    });
    this.#since = 0;
    this.#peakLeft = 0;
    this.#peakRight = 0;
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0];
    const left = out?.[0];
    if (!left) return true;
    const right = out[1] ?? this.#spare;
    const engine = this.#engine;
    if (!engine || this.#dead) {
      left.fill(0);
      right.fill(0);
      return true;
    }
    const mix = this.#mix;
    try {
      for (let at = 0; at < left.length; at += BLOCK) {
        const n = Math.min(BLOCK, left.length - at);
        engine.process(mix, n);
        for (let i = 0; i < n; i++) {
          left[at + i] = mix[2 * i] as number;
          right[at + i] = mix[2 * i + 1] as number;
        }
        if (engine.peakLeft > this.#peakLeft) this.#peakLeft = engine.peakLeft;
        if (engine.peakRight > this.#peakRight) this.#peakRight = engine.peakRight;
        this.#since += n;
      }
      const playing = engine.playing;
      if ((playing && this.#since >= sampleRate / REPORTS_PER_S) || (this.#wasPlaying && !playing)) this.#report(engine);
      this.#wasPlaying = playing;
    } catch (error) {
      this.#dead = true;
      this.#engine = null;
      left.fill(0);
      right.fill(0);
      this.#post({ type: "fatal", message: error instanceof Error ? error.message : String(error) });
    }
    if (this.#dead) {
      left.fill(0);
      right.fill(0);
    }
    return true;
  }
}

registerProcessor("redline-beat", BeatProcessor);
