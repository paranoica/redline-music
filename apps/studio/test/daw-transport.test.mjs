// The screen's transport machine, the "next step" chip and what disables record and export.
import assert from "node:assert/strict";
import { test } from "node:test";

import { IDLE, exportBlockers, nextStep, recordBlockers, step } from "../src/daw/transport.ts";

const idle = IDLE;
const failed = { mode: "idle", error: "boom" };
const playing = { mode: "playing" };
const rec = (s) => ({ mode: "recording", step: s });
const rendering = (then) => ({ mode: "rendering", then });

const EVENTS = {
  play: { type: "play", render: false },
  playR: { type: "play", render: true },
  pause: { type: "pause" },
  stop: { type: "stop" },
  record: { type: "record", render: false },
  recordR: { type: "record", render: true },
  export: { type: "export", render: false },
  exportR: { type: "export", render: true },
  openedCal: { type: "opened", calibrate: true },
  opened: { type: "opened", calibrate: false },
  calibrated: { type: "calibrated" },
  recordStarted: { type: "recordStarted" },
  recordStopped: { type: "recordStopped" },
  renderStarted: { type: "renderStarted" },
  renderDone: { type: "renderDone" },
  error: { type: "error", message: "boom" },
};

// [state name, state, event name, next state, effects]; every pair left out is ignored
const MOVES = [
  ["idle", idle, "play", playing, ["play"]],
  ["idle", idle, "playR", rendering("play"), ["commit"]],
  ["idle", idle, "stop", idle, ["stop"]],
  ["idle", idle, "record", rec("opening"), ["open-engine"]],
  ["idle", idle, "recordR", rendering("record"), ["commit"]],
  ["idle", idle, "export", idle, ["export"]],
  ["idle", idle, "exportR", rendering("export"), ["commit"]],
  ["idle", idle, "renderStarted", rendering(null), []],
  ["idle", idle, "error", failed, []],

  ["failed", failed, "play", playing, ["play"]],
  ["failed", failed, "playR", rendering("play"), ["commit"]],
  ["failed", failed, "stop", idle, ["stop"]],
  ["failed", failed, "record", rec("opening"), ["open-engine"]],
  ["failed", failed, "recordR", rendering("record"), ["commit"]],
  ["failed", failed, "export", idle, ["export"]],
  ["failed", failed, "exportR", rendering("export"), ["commit"]],
  ["failed", failed, "renderStarted", rendering(null), []],
  ["failed", failed, "error", failed, []],

  ["playing", playing, "pause", idle, ["pause"]],
  ["playing", playing, "stop", idle, ["stop"]],
  ["playing", playing, "record", rec("opening"), ["stop", "open-engine"]],
  ["playing", playing, "recordR", rendering("record"), ["stop", "commit"]],
  ["playing", playing, "export", playing, ["export"]],
  ["playing", playing, "exportR", rendering("export"), ["stop", "commit"]],
  ["playing", playing, "error", failed, ["stop"]],

  ...[null, "play", "record", "export"].flatMap((then) => {
    const s = rendering(then);
    const name = `rendering(${then})`;
    return [
      [name, s, "play", rendering("play"), []],
      [name, s, "playR", rendering("play"), []],
      [name, s, "record", rendering("record"), []],
      [name, s, "recordR", rendering("record"), []],
      [name, s, "export", rendering("export"), []],
      [name, s, "exportR", rendering("export"), []],
      [name, s, "stop", idle, ["stop"]],
      ...(then === "play" ? [[name, s, "pause", rendering(null), []]] : []),
      [name, s, "renderDone", ...{ null: [idle, []], play: [playing, ["play"]], record: [rec("opening"), ["open-engine"]], export: [idle, ["export"]] }[then]],
      [name, s, "error", failed, []],
    ];
  }),

  ["opening", rec("opening"), "openedCal", rec("calibrate"), []],
  ["opening", rec("opening"), "opened", rec("counting"), ["start-record"]],
  ["opening", rec("opening"), "stop", idle, []],
  ["opening", rec("opening"), "error", failed, []],

  ["calibrate", rec("calibrate"), "calibrated", rec("counting"), ["start-record"]],
  ["calibrate", rec("calibrate"), "stop", idle, []],
  ["calibrate", rec("calibrate"), "error", failed, []],

  ["counting", rec("counting"), "recordStarted", rec("recording"), []],
  ["counting", rec("counting"), "stop", rec("stopping"), ["stop-record"]],
  ["counting", rec("counting"), "recordStopped", idle, []],
  ["counting", rec("counting"), "error", failed, []],

  ["recording", rec("recording"), "stop", rec("stopping"), ["stop-record"]],
  ["recording", rec("recording"), "recordStopped", idle, []],
  ["recording", rec("recording"), "error", failed, []],

  ["stopping", rec("stopping"), "recordStopped", idle, []],
  ["stopping", rec("stopping"), "error", failed, []],
];

const STATES = [
  ["idle", idle],
  ["failed", failed],
  ["playing", playing],
  ...[null, "play", "record", "export"].map((t) => [`rendering(${t})`, rendering(t)]),
  ...["opening", "calibrate", "counting", "recording", "stopping"].map((s) => [s, rec(s)]),
];

for (const [name, state, event, next, effects] of MOVES) {
  test(`${name} + ${event} -> ${JSON.stringify(next)} ${effects.join(",")}`, () => {
    const before = structuredClone(state);
    assert.deepEqual(step(state, EVENTS[event]), { state: next, effects });
    assert.deepEqual(state, before, "the state given is left alone");
  });
}

test("every other event leaves the state as it is and does nothing", () => {
  const moved = new Set(MOVES.map(([name, , event]) => `${name}/${event}`));
  let checked = 0;
  for (const [name, state] of STATES) {
    for (const [event, e] of Object.entries(EVENTS)) {
      if (moved.has(`${name}/${event}`)) continue;
      assert.deepEqual(step(state, e), { state, effects: [] }, `${name} + ${event}`);
      checked += 1;
    }
  }
  assert.ok(checked > 50);
});

test("a whole take: record over a stale beat, wait for the render, calibrate, record, stop", () => {
  let s = IDLE;
  const effects = [];
  for (const e of ["recordR", "renderDone", "openedCal", "calibrated", "recordStarted", "stop", "recordStopped"]) {
    const out = step(s, EVENTS[e]);
    s = out.state;
    effects.push(...out.effects);
  }
  assert.deepEqual(s, IDLE);
  assert.deepEqual(effects, ["commit", "open-engine", "start-record", "stop-record"]);
});

test("record pressed while a background render runs waits for it, then opens the engine", () => {
  let s = step(IDLE, EVENTS.renderStarted).state;
  const pressed = step(s, EVENTS.record);
  assert.deepEqual(pressed, { state: rendering("record"), effects: [] });
  s = pressed.state;
  assert.deepEqual(step(s, EVENTS.renderDone), { state: rec("opening"), effects: ["open-engine"] });
});

const DONE = {
  hasBeatAudio: true,
  beatAnalysed: true,
  takes: 2,
  chosen: "take-1.wav",
  voiceKind: "own",
  studioReady: true,
  mixStale: false,
  refsCount: 3,
  exporting: false,
};

test("next step walks the song from the beat to the export", () => {
  assert.equal(nextStep(DONE), "done");
  assert.equal(nextStep({ ...DONE, beatAnalysed: false }), "analyse");
  assert.equal(nextStep({ ...DONE, beatAnalysed: false, hasBeatAudio: false }), "analyse");
  assert.equal(nextStep({ ...DONE, hasBeatAudio: false }), "listen");
  assert.equal(nextStep({ ...DONE, takes: 0, chosen: null, studioReady: false }), "record");
  assert.equal(nextStep({ ...DONE, chosen: null, studioReady: false }), "pick");
  assert.equal(nextStep({ ...DONE, studioReady: false }), "prepare-mix");
  assert.equal(nextStep({ ...DONE, studioReady: false, mixStale: true }), "prepare-mix");
  assert.equal(nextStep({ ...DONE, mixStale: true }), "export");
  assert.equal(nextStep({ ...DONE, exporting: true }), "export");
});

test("a Suno voice skips record and pick", () => {
  const suno = { ...DONE, voiceKind: "suno", takes: 0, chosen: null };
  assert.equal(nextStep({ ...suno, studioReady: false }), "prepare-mix");
  assert.equal(nextStep({ ...suno, mixStale: true }), "export");
  assert.equal(nextStep(suno), "done");
  assert.equal(nextStep({ ...suno, hasBeatAudio: false }), "listen");
});

test("record is disabled by a Suno voice, a busy engine and a render, each named", () => {
  const own = { voiceKind: "own", engineBusy: false };
  assert.deepEqual(recordBlockers(own, IDLE), []);
  assert.deepEqual(recordBlockers(own, playing), []);
  assert.deepEqual(recordBlockers({ ...own, voiceKind: "suno" }, IDLE), ["suno"]);
  assert.deepEqual(recordBlockers({ ...own, engineBusy: true }, IDLE), ["busy"]);
  assert.deepEqual(recordBlockers(own, rendering(null)), ["rendering"]);
  assert.deepEqual(recordBlockers({ voiceKind: "suno", engineBusy: true }, rendering("play")), ["suno", "busy", "rendering"]);
});

test("export is disabled with no refs and with no chosen take of an own voice", () => {
  assert.deepEqual(exportBlockers(DONE), []);
  assert.deepEqual(exportBlockers({ ...DONE, refsCount: 0 }), ["no-refs"]);
  assert.deepEqual(exportBlockers({ ...DONE, chosen: null }), ["no-take"]);
  assert.deepEqual(exportBlockers({ ...DONE, refsCount: 0, chosen: null }), ["no-refs", "no-take"]);
  assert.deepEqual(exportBlockers({ ...DONE, voiceKind: "suno", chosen: null }), []);
});
