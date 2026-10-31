// Lyric lines over the beat's bars. The writer fits one line to a bar at half time, which is two
// bars of the beat, and leaves four half-time bars to the beat alone: four bars of intro before
// the first line by default. The page only knows how many lines each section has.

export interface LyricSection {
  kind: string;
  lines: number;
}

// the owner's nudge, kept in layout.json
export interface LyricLayout {
  startBar: number;
  barsPerLine: 1 | 2;
}

export interface LinePlace {
  section: number;
  line: number;
  fromBar: number;
  bars: number;
  // placed by the default rule, not by the owner
  approximate: boolean;
  // ran past the last bar and was put on it
  clamped: boolean;
}

export const DEFAULT_LAYOUT: LyricLayout = { startBar: 4, barsPerLine: 2 };

export function placeLines(sections: readonly LyricSection[], totalBars: number, layout: LyricLayout | null): LinePlace[] {
  const { startBar, barsPerLine } = layout ?? DEFAULT_LAYOUT;
  if (!Number.isInteger(startBar) || startBar < 0) throw new RangeError(`lines cannot start at bar ${startBar}`);
  if (barsPerLine !== 1 && barsPerLine !== 2) throw new RangeError(`a line cannot take ${String(barsPerLine)} bars`);
  // a part of a bar at the end of a file holds no line
  const total = Math.max(0, Math.floor(totalBars));
  const last = Math.max(0, total - 1);
  const out: LinePlace[] = [];
  let at = startBar;
  sections.forEach((s, section) => {
    for (let line = 0; line < s.lines; line += 1) {
      const fits = at + barsPerLine <= total;
      const fromBar = fits ? at : Math.min(at, last);
      const bars = fits ? barsPerLine : Math.max(0, Math.min(barsPerLine, total - fromBar));
      out.push({ section, line, fromBar, bars, approximate: layout === null, clamped: !fits });
      at += barsPerLine;
    }
  });
  return out;
}

// the line to light at a playhead bar; on a bar several clamped lines share, the first of them
export function lineAt(places: readonly LinePlace[], bar: number): LinePlace | null {
  return places.find((p) => bar >= p.fromBar && bar < p.fromBar + p.bars) ?? null;
}
