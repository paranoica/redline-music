// The takes table: a row is addressed by its file name, which the server's routes take.
import assert from "node:assert/strict";
import { test } from "node:test";

import { takeRows } from "../src/song/takes.ts";

const metrics = { timing_ms: 8.7, grid_ms: 1, clip_runs: 0, floor_below_voice_db: -42.4, dropouts: 0, dropout_ms: 0, bleed: 0.02 };
const take = (name, file, rank, total) => ({ name, file, rank, total, points: {}, headphones: false, metrics });

test("a row's name is the take's file name, whatever the ranking calls it", () => {
  const ranking = {
    ranked: true,
    takes: [take("take-02", "/x/input/takes/s/take-02.wav", 1, 1.5), take("take-01", "input/takes/s/take-01.wav", 2, 2)],
    skipped: [],
  };
  const rows = takeRows(ranking);
  assert.deepEqual(rows.map((r) => r.name), ["take-02.wav", "take-01.wav"]);
  assert.deepEqual(rows.map((r) => r.label), rows.map((r) => r.label.replace(/\.wav$/, "")));
});
