import type { Dsp } from "./dsp.ts";

export interface DspOptions {
  instantiateWasm?: (
    imports: WebAssembly.Imports,
    done: (instance: WebAssembly.Instance) => void,
  ) => Record<string, never>;
}

export default function createRedlineDsp(options?: DspOptions): Promise<Dsp>;
