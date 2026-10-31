// The text of the song as parts and lines, and what the text editing needs.

import type { TextProblem, TextReply } from "./api.ts";

export interface LyricPart {
  part: string | null;
  lines: string[];
}

// "[Куплет 1]" opens a part; a blank line also starts a new block
export function parseLyric(text: string): LyricPart[] {
  const parts: LyricPart[] = [];
  let current: LyricPart | null = null;
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    const heading = /^\[(.+)\]$/.exec(line);
    if (heading) {
      current = { part: heading[1] ?? null, lines: [] };
      parts.push(current);
    } else if (line === "") {
      current = null;
    } else {
      if (!current) {
        current = { part: null, lines: [] };
        parts.push(current);
      }
      current.lines.push(line);
    }
  }
  return parts.filter((p) => p.lines.length > 0);
}

export function lineCount(parts: readonly LyricPart[]): number {
  return parts.reduce((n, p) => n + p.lines.length, 0);
}

export function placeOfProblem(p: TextProblem): string {
  return p.place ? `${p.place}: ${p.message}` : p.message;
}

export function textDirty(editing: boolean, draft: string, loaded: Pick<TextReply, "text"> | null): boolean {
  return editing && loaded !== null && draft !== loaded.text;
}
