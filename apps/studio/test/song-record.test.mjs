// What a toggle sent to start a take answered, and how often the recorder is asked.
import assert from "node:assert/strict";
import { test } from "node:test";

import { recPollMs, startRefusal } from "../src/song/record.ts";

test("only a started take goes on; every other answer is a note and no take", () => {
  assert.equal(startRefusal({ code: "recording", values: { take: "take-04" } }), null);
  for (const code of ["quantum_moved", "kept_late", "finished_late_discarded", "kept", "discarded", "still_finishing", "something_new"]) {
    const note = startRefusal({ code, values: {} });
    assert.equal(typeof note, "string", code);
    assert.match(note, /не начался/, code);
  }
});

test("the note says what the engine did instead", () => {
  const late = startRefusal({ code: "kept_late", values: { take: "take-03", seconds: 12, dropouts: 0 } });
  assert.match(late, /дубль 03/);
  assert.match(late, /сохран/);
  assert.match(late, /ещё раз/);
  assert.match(startRefusal({ code: "finished_late_discarded", values: { take: "take-03" } }), /сброшен/);
  const moved = startRefusal({ code: "quantum_moved", values: { was: 256, now: 512 } });
  assert.match(moved, /256/);
  assert.match(moved, /512/);
  assert.match(startRefusal({ code: "something_new" }), /something_new/);
  // no take name given: still a sentence
  assert.match(startRefusal({ code: "kept_late" }), /прошлый дубль/);
});

test("the recorder is asked about 20 times a second while the engine is open or a take is wanted, once a second otherwise", () => {
  for (const phase of ["opening", "calibrating", "ready", "live", "stopping", "out_of_sync"]) assert.equal(recPollMs(phase, false), 50, phase);
  assert.equal(recPollMs("closed", false), 1000);
  assert.equal(recPollMs("failed", false), 1000);
  // the screen waits for a take (recording, a waiter, a take being watched)
  assert.equal(recPollMs("closed", true), 50);
  assert.equal(recPollMs("failed", true), 50);
});
