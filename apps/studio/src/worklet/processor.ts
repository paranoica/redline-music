// The AudioWorklet around the engine. A worklet cannot fetch, so the page sends the wasm
// bytes in its first message; messages that come before the engine is up wait for it.

import { Engine } from "../engine/core.ts";
import type { ToEngine } from "../engine/protocol.ts";
import createRedlineDsp from "../engine/redline_dsp.js";

type Incoming = ToEngine | { type: "wasm"; bytes: ArrayBuffer };

class RedlineProcessor extends AudioWorkletProcessor {
  #engine: Engine | null = null;
  #waiting: ToEngine[] = [];
  // after a throw the engine's state, or the whole wasm module after an abort, is not to be trusted
  #dead = false;
  readonly #spare = new Float32Array(128);

  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent<Incoming>) => {
      const m = event.data;
      if (this.#dead) return;
      if (m.type === "wasm") {
        void this.#start(m.bytes);
      } else if (this.#engine) {
        try {
          this.#engine.handle(m);
        } catch (error) {
          this.#die(error);
        }
      } else {
        this.#waiting.push(m);
      }
    };
  }

  async #start(bytes: ArrayBuffer): Promise<void> {
    try {
      const dsp = await createRedlineDsp({
        instantiateWasm: (imports, done) => {
          WebAssembly.instantiate(bytes, imports).then(
            (made) => done(made.instance),
            (error: unknown) => this.port.postMessage({ type: "error", message: `the wasm did not load: ${String(error)}` }),
          );
          return {};
        },
      });
      const engine = new Engine(dsp, sampleRate, (m) => this.port.postMessage(m));
      for (const m of this.#waiting) engine.handle(m);
      this.#waiting = [];
      this.#engine = engine;
      this.port.postMessage({ type: "ready" });
    } catch (error) {
      this.port.postMessage({ type: "error", message: `the engine did not start: ${String(error)}` });
    }
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0];
    const left = out?.[0];
    if (!left) return true;
    const right = out[1] ?? this.#spare;
    const engine = this.#engine;
    if (engine && !this.#dead) {
      try {
        engine.render(left, right);
        return true;
      } catch (error) {
        this.#die(error);
      }
    }
    left.fill(0);
    right.fill(0);
    return true;
  }

  #die(error: unknown): void {
    this.#dead = true;
    this.#engine = null;
    const message = error instanceof Error ? error.message : String(error);
    this.port.postMessage({ type: "error", fatal: true, message });
  }
}

registerProcessor("redline-engine", RedlineProcessor);
