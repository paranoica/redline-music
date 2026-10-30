// The event table of the score being edited, made by the core on the page's own copy of the
// wasm, so the 808 glides are drawn as the player will play them after every edit.

import { withText, lastError } from "../engine/dsp.ts";
import createRedlineDsp from "../engine/redline_dsp.js";
import type { EventTable } from "./model.ts";

export type TableResult = { ok: true; table: EventTable } | { ok: false; errors: string[] };

export class Tables {
  readonly #dsp: Awaited<ReturnType<typeof createRedlineDsp>>;
  readonly #rate: number;

  private constructor(dsp: Awaited<ReturnType<typeof createRedlineDsp>>, rate: number) {
    this.#dsp = dsp;
    this.#rate = rate;
  }

  static async start(rate: number): Promise<Tables> {
    const response = await fetch(new URL("redline_dsp.wasm", document.baseURI));
    if (!response.ok) throw new Error(`the wasm did not load (${response.status})`);
    const bytes = await response.arrayBuffer();
    // a failed instantiation has to reject, or the glue's promise never settles
    const dsp = await new Promise<Awaited<ReturnType<typeof createRedlineDsp>>>((resolve, reject) => {
      createRedlineDsp({
        instantiateWasm: (imports, done) => {
          WebAssembly.instantiate(bytes, imports).then((made) => done(made.instance), reject);
          return {};
        },
      }).then(resolve, reject);
    });
    return new Tables(dsp, rate);
  }

  events(text: string): TableResult {
    const dsp = this.#dsp;
    const ptr = withText(dsp, text, (p, n) => dsp._beat_events_json(p, n, this.#rate));
    if (!ptr) return { ok: false, errors: lastError(dsp).split("\n").filter(Boolean) };
    return { ok: true, table: JSON.parse(dsp.UTF8ToString(ptr)) as EventTable };
  }
}
