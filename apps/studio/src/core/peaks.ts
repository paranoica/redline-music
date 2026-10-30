// Min and max of each of `bins` equal spans of interleaved stereo, the channels' mean.

export function peaks(x: Float32Array, bins: number): { min: Float32Array; max: Float32Array } {
  const frames = x.length / 2;
  const min = new Float32Array(bins);
  const max = new Float32Array(bins);
  for (let b = 0; b < bins; b++) {
    const from = Math.floor((b * frames) / bins);
    const to = Math.max(from + 1, Math.floor(((b + 1) * frames) / bins));
    let lo = 0;
    let hi = 0;
    for (let n = from; n < to && n < frames; n++) {
      const v = ((x[2 * n] as number) + (x[2 * n + 1] as number)) / 2;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    min[b] = lo;
    max[b] = hi;
  }
  return { min, max };
}
