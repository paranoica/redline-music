// What the page does with an analysis that did not come back.

// a 409: the server gave the work to an analysis asked for after this one
export class Superseded extends Error {}

const FIRST_MS = 300;
const MOST_MS = 10000;
// an analysis longer than the waits would otherwise be cancelled by the other page for good
const TRIES = 6;

// "quiet" when the page aborted it; "again" when the server was busy with another page's
export function analysisFailure(error: unknown, aborted: boolean): "quiet" | "again" | "failed" {
  if (aborted || (error instanceof DOMException && error.name === "AbortError")) return "quiet";
  if (error instanceof Superseded) return "again";
  return "failed";
}

// null once the page should stop asking
export function retryDelay(attempt: number): number | null {
  if (attempt >= TRIES) return null;
  return Math.min(MOST_MS, FIRST_MS * 2 ** attempt);
}
