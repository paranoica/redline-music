// Lyric lines placed over the beat's bars.
import assert from "node:assert/strict";
import { test } from "node:test";

import { lineAt, placeLines } from "../src/daw/lyrics.ts";

const song = [
  { kind: "intro", lines: 0 },
  { kind: "verse", lines: 3 },
  { kind: "hook", lines: 2 },
];

const line = (section, index, fromBar, bars, approximate = true, clamped = false) => ({
  section,
  line: index,
  fromBar,
  bars,
  approximate,
  clamped,
});

test("by default the lines start after four intro bars, two bars each, roughly", () => {
  assert.deepEqual(placeLines(song, 32, null), [
    line(1, 0, 4, 2),
    line(1, 1, 6, 2),
    line(1, 2, 8, 2),
    line(2, 0, 10, 2),
    line(2, 1, 12, 2),
  ]);
});

test("a layout moves every line and is no longer approximate", () => {
  assert.deepEqual(placeLines(song, 32, { startBar: 0, barsPerLine: 1 }), [
    line(1, 0, 0, 1, false),
    line(1, 1, 1, 1, false),
    line(1, 2, 2, 1, false),
    line(2, 0, 3, 1, false),
    line(2, 1, 4, 1, false),
  ]);
  assert.deepEqual(
    placeLines([{ kind: "verse", lines: 2 }], 32, { startBar: 5, barsPerLine: 2 }).map((p) => [p.fromBar, p.approximate]),
    [
      [5, false],
      [7, false],
    ],
  );
});

test("lines past the last bar are clamped onto it and flagged", () => {
  const verse = [{ kind: "verse", lines: 5 }];
  assert.deepEqual(placeLines(verse, 10, null), [
    line(0, 0, 4, 2),
    line(0, 1, 6, 2),
    line(0, 2, 8, 2),
    line(0, 3, 9, 1, true, true),
    line(0, 4, 9, 1, true, true),
  ]);
  assert.deepEqual(placeLines([{ kind: "verse", lines: 3 }], 9, null).at(-1), line(0, 2, 8, 1, true, true));
});

test("a part of a bar at the end of a file holds no line", () => {
  assert.deepEqual(placeLines([{ kind: "verse", lines: 3 }], 9.9, null).at(-1), line(0, 2, 8, 1, true, true));
});

test("a start past the end puts every line on the last bar", () => {
  const out = placeLines([{ kind: "verse", lines: 2 }], 32, { startBar: 40, barsPerLine: 2 });
  assert.deepEqual(out, [line(0, 0, 31, 1, false, true), line(0, 1, 31, 1, false, true)]);
});

test("a beat with no bars clamps every line to nothing", () => {
  assert.deepEqual(placeLines([{ kind: "hook", lines: 1 }], 0, null), [line(0, 0, 0, 0, true, true)]);
});

test("no lines, no places", () => {
  assert.deepEqual(placeLines([], 32, null), []);
  assert.deepEqual(placeLines([{ kind: "intro", lines: 0 }, { kind: "outro", lines: 0 }], 32, null), []);
});

test("a layout must start on a whole bar at or after the first and use one or two bars a line", () => {
  assert.throws(() => placeLines(song, 32, { startBar: -1, barsPerLine: 2 }), RangeError);
  assert.throws(() => placeLines(song, 32, { startBar: 1.5, barsPerLine: 2 }), RangeError);
  assert.throws(() => placeLines(song, 32, { startBar: 0, barsPerLine: 3 }), RangeError);
});

test("the lit line is the one whose bars hold the playhead", () => {
  const places = placeLines(song, 32, null);
  assert.equal(lineAt(places, 3.99), null);
  assert.deepEqual(lineAt(places, 4), line(1, 0, 4, 2));
  assert.deepEqual(lineAt(places, 5.99), line(1, 0, 4, 2));
  assert.deepEqual(lineAt(places, 6), line(1, 1, 6, 2));
  assert.deepEqual(lineAt(places, 13.5), line(2, 1, 12, 2));
  assert.equal(lineAt(places, 14), null);
  assert.equal(lineAt([], 0), null);
});

test("on a bar several clamped lines share, the first of them is lit", () => {
  const places = placeLines([{ kind: "verse", lines: 5 }], 9, null);
  assert.deepEqual(places.slice(2), [line(0, 2, 8, 1, true, true), line(0, 3, 8, 1, true, true), line(0, 4, 8, 1, true, true)]);
  assert.deepEqual(lineAt(places, 8.5), line(0, 2, 8, 1, true, true));
  assert.equal(lineAt(places, 9), null);
});
