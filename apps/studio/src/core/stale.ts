// Which setting changes leave the server's analysis stale, so it has to run again.

import type { Settings } from "../types.ts";

export function staleKeys(stale: Readonly<Record<string, readonly string[]>>): Set<string> {
  return new Set(Object.values(stale).flat());
}

export function changedKeys(a: Settings, b: Settings): string[] {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
}

export function needsAnalysis(changed: readonly string[], stale: ReadonlySet<string>): boolean {
  return changed.some((k) => stale.has(k));
}
