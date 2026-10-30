// Lining up the vocal and the beat racks: the one with less latency waits for the other.

export interface Alignment {
  vocalPad: number;
  beatPad: number;
  total: number;
}

export function align(vocal: number, beat: number, master: number): Alignment {
  for (const latency of [vocal, beat, master]) {
    if (!Number.isInteger(latency) || latency < 0) throw new Error(`no latency ${latency}`);
  }
  return {
    vocalPad: Math.max(0, beat - vocal),
    beatPad: Math.max(0, vocal - beat),
    total: Math.max(vocal, beat) + master,
  };
}

// a fixed delay over interleaved stereo, processed in place
export class DelayLine {
  readonly #ring: Float32Array;
  readonly #capacity: number;
  #delay = 0;
  #at = 0;

  constructor(capacity: number) {
    this.#capacity = capacity;
    this.#ring = new Float32Array(2 * Math.max(1, capacity));
  }

  get delay(): number {
    return this.#delay;
  }

  setDelay(frames: number): void {
    if (!Number.isInteger(frames) || frames < 0 || frames > this.#capacity) {
      throw new Error(`a delay of ${frames} frames does not fit in ${this.#capacity}`);
    }
    if (frames === this.#delay) return;
    this.#delay = frames;
    this.#ring.fill(0);
    this.#at = 0;
  }

  process(block: Float32Array, frames: number): void {
    const d = this.#delay;
    if (d === 0) return;
    const ring = this.#ring;
    let at = this.#at;
    for (let i = 0; i < frames; i++) {
      const l = block[2 * i] as number;
      const r = block[2 * i + 1] as number;
      block[2 * i] = ring[2 * at] as number;
      block[2 * i + 1] = ring[2 * at + 1] as number;
      ring[2 * at] = l;
      ring[2 * at + 1] = r;
      at = at + 1 === d ? 0 : at + 1;
    }
    this.#at = at;
  }
}
