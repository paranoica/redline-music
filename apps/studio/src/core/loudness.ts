// Loudness after ITU-R BS.1770-4: K-weighting, 400 ms blocks every 100 ms, the absolute gate
// at -70 LUFS and the relative one 10 LU under the mean of what passed it.

export interface Biquad {
  b: [number, number, number];
  a: [number, number, number];
}

const ABSOLUTE_GATE = -70;
const RELATIVE_GATE = 10;

// the filters' analog prototypes, as libebur128 states them, for any rate
export function kWeighting(rate: number): { shelf: Biquad; highpass: Biquad } {
  let f0 = 1681.974450955533;
  const g = 3.999843853973347;
  let q = 0.7071752369554196;
  let k = Math.tan((Math.PI * f0) / rate);
  const vh = 10 ** (g / 20);
  const vb = vh ** 0.4996667741545416;
  let a0 = 1 + k / q + k * k;
  const shelf: Biquad = {
    b: [(vh + (vb * k) / q + k * k) / a0, (2 * (k * k - vh)) / a0, (vh - (vb * k) / q + k * k) / a0],
    a: [1, (2 * (k * k - 1)) / a0, (1 - k / q + k * k) / a0],
  };
  f0 = 38.13547087602444;
  q = 0.5003270373238773;
  k = Math.tan((Math.PI * f0) / rate);
  a0 = 1 + k / q + k * k;
  const highpass: Biquad = {
    b: [1, -2, 1],
    a: [1, (2 * (k * k - 1)) / a0, (1 - k / q + k * k) / a0],
  };
  return { shelf, highpass };
}

// the K filter on one channel, as a running state
class KFilter {
  readonly #s: Biquad;
  readonly #h: Biquad;
  #x1 = 0;
  #x2 = 0;
  #y1 = 0;
  #y2 = 0;
  #z1 = 0;
  #z2 = 0;

  constructor(rate: number) {
    const { shelf, highpass } = kWeighting(rate);
    this.#s = shelf;
    this.#h = highpass;
  }

  step(x: number): number {
    const [b0, b1, b2] = this.#s.b;
    const [, a1, a2] = this.#s.a;
    const y = b0 * x + b1 * this.#x1 + b2 * this.#x2 - a1 * this.#y1 - a2 * this.#y2;
    this.#x2 = this.#x1;
    this.#x1 = x;
    const [, c1, c2] = this.#h.a;
    const z = y - 2 * this.#y1 + this.#y2 - c1 * this.#z1 - c2 * this.#z2;
    this.#y2 = this.#y1;
    this.#y1 = y;
    this.#z2 = this.#z1;
    this.#z1 = z;
    return z;
  }

  reset(): void {
    this.#x1 = this.#x2 = this.#y1 = this.#y2 = this.#z1 = this.#z2 = 0;
  }
}

function toLufs(power: number): number {
  return power > 0 ? -0.691 + 10 * Math.log10(power) : -Infinity;
}

// gated loudness of 400 ms block powers
function gated(blocks: readonly number[]): number {
  const absolute = 10 ** ((ABSOLUTE_GATE + 0.691) / 10);
  let sum = 0;
  let n = 0;
  for (const p of blocks) {
    if (p > absolute) {
      sum += p;
      n++;
    }
  }
  if (n === 0) return -Infinity;
  const relative = (sum / n) * 10 ** (-RELATIVE_GATE / 10);
  sum = 0;
  n = 0;
  for (const p of blocks) {
    if (p > absolute && p > relative) {
      sum += p;
      n++;
    }
  }
  return n === 0 ? -Infinity : toLufs(sum / n);
}

// powers (left + right mean squares, K-weighted) of 100 ms steps
function quarterPowers(x: Float32Array, rate: number, start: number, end: number): number[] {
  const step = Math.round(rate / 10);
  const left = new KFilter(rate);
  const right = new KFilter(rate);
  const out: number[] = [];
  let acc = 0;
  let count = 0;
  for (let n = start; n < end; n++) {
    const l = left.step(x[2 * n] as number);
    const r = right.step(x[2 * n + 1] as number);
    acc += l * l + r * r;
    if (++count === step) {
      out.push(acc / step);
      acc = 0;
      count = 0;
    }
  }
  return out;
}

function blocksOf(quarters: readonly number[]): number[] {
  const blocks: number[] = [];
  for (let i = 0; i + 4 <= quarters.length; i++) {
    blocks.push(
      ((quarters[i] as number) + (quarters[i + 1] as number) + (quarters[i + 2] as number) + (quarters[i + 3] as number)) / 4,
    );
  }
  return blocks;
}

// integrated loudness of interleaved stereo over [start, end) frames; -Infinity when silent
export function integratedLufs(x: Float32Array, rate: number, start = 0, end = x.length / 2): number {
  const from = Math.max(0, Math.floor(start));
  const to = Math.min(x.length / 2, Math.floor(end));
  return gated(blocksOf(quarterPowers(x, rate, from, to)));
}

// each side's gain in dB that brings the known ones down to the quietest of them
export function abGains<K extends string>(lufs: Partial<Record<K, number | null>>): Record<K, number> {
  const known = (Object.values(lufs) as (number | null)[]).filter(
    (v): v is number => v !== null && Number.isFinite(v),
  );
  const quiet = Math.min(...known);
  const out = {} as Record<K, number>;
  for (const [key, value] of Object.entries(lufs) as [K, number | null][]) {
    out[key] = value !== null && Number.isFinite(value) ? quiet - value : 0;
  }
  return out;
}

export function dbToGain(db: number): number {
  return 10 ** (db / 20);
}

// the loudest window of `seconds` by plain mean square, on half-second steps
export function loudestSpan(x: Float32Array, rate: number, seconds: number): { start: number; end: number } {
  const frames = x.length / 2;
  const total = frames / rate;
  if (seconds >= total) return { start: 0, end: total };
  const hop = Math.round(rate / 2);
  const hops = Math.floor(frames / hop);
  const energy = new Float64Array(hops);
  for (let h = 0; h < hops; h++) {
    let acc = 0;
    for (let n = h * hop; n < (h + 1) * hop; n++) {
      const l = x[2 * n] as number;
      const r = x[2 * n + 1] as number;
      acc += l * l + r * r;
    }
    energy[h] = acc;
  }
  const width = Math.max(1, Math.round(seconds * 2));
  let sum = 0;
  for (let h = 0; h < width; h++) sum += energy[h] as number;
  let best = sum;
  let bestAt = 0;
  for (let h = width; h < hops; h++) {
    sum += (energy[h] as number) - (energy[h - width] as number);
    if (sum > best) {
      best = sum;
      bestAt = h - width + 1;
    }
  }
  const start = Math.min((bestAt * hop) / rate, total - seconds);
  return { start, end: start + seconds };
}

// loudness of what played last, for the A/B: K-weighted 100 ms powers kept for `maxSeconds`
export class LoudnessMeter {
  readonly #left: KFilter;
  readonly #right: KFilter;
  readonly #step: number;
  readonly #ring: Float64Array;
  #count = 0;
  #at = 0;
  #acc = 0;
  #filled = 0;

  constructor(rate: number, maxSeconds: number) {
    this.#left = new KFilter(rate);
    this.#right = new KFilter(rate);
    this.#step = Math.round(rate / 10);
    this.#ring = new Float64Array(Math.max(4, Math.round(maxSeconds * 10)));
  }

  push(block: Float32Array, frames: number): void {
    for (let i = 0; i < frames; i++) {
      const l = this.#left.step(block[2 * i] as number);
      const r = this.#right.step(block[2 * i + 1] as number);
      this.#acc += l * l + r * r;
      if (++this.#count === this.#step) {
        this.#ring[this.#at] = this.#acc / this.#step;
        this.#at = (this.#at + 1) % this.#ring.length;
        this.#filled = Math.min(this.#filled + 1, this.#ring.length);
        this.#acc = 0;
        this.#count = 0;
      }
    }
  }

  lufs(seconds: number): number {
    const n = Math.min(this.#filled, Math.round(seconds * 10));
    const quarters: number[] = [];
    for (let i = n; i >= 1; i--) {
      quarters.push(this.#ring[(this.#at - i + this.#ring.length) % this.#ring.length] as number);
    }
    return gated(blocksOf(quarters));
  }

  reset(): void {
    this.#left.reset();
    this.#right.reset();
    this.#filled = 0;
    this.#acc = 0;
    this.#count = 0;
  }
}
