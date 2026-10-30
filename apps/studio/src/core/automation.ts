// A lane's value at a time: the first value before the first point, the last after the
// last, linear between; at points sharing a time the later one wins.

export function valueAt(points: readonly (readonly [number, number])[], seconds: number): number {
  const first = points[0];
  if (first === undefined) throw new Error("a lane needs at least one point");
  if (seconds < first[0]) return first[1];
  let lo = 0;
  let hi = points.length - 1;
  // the last point at or before `seconds`
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if ((points[mid] as readonly [number, number])[0] <= seconds) lo = mid;
    else hi = mid - 1;
  }
  const a = points[lo] as readonly [number, number];
  const b = points[lo + 1];
  if (b === undefined || b[0] === a[0]) return a[1];
  return a[1] + ((b[1] - a[1]) * (seconds - a[0])) / (b[0] - a[0]);
}
