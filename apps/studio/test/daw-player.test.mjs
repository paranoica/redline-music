// The song player's pure parts and the owner that picks a backend and keeps audition and song
// playback apart. The audio graphs themselves only run in a browser.
import assert from "node:assert/strict";
import { test } from "node:test";

import { auditionRange } from "../src/daw/audition.ts";
import { Playback, TAKE_LEAD, fitLoop, laneDbSlots, laneGains, laneStart, positionAt, segmentEnd, startPoint, withLaneGains } from "../src/daw/player.ts";

const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg ?? ""} ${a} != ${b}`);

test("the recorder drops the round trip itself, so a take starts with the beat", () => {
  assert.equal(TAKE_LEAD, 0);
});

test("a lane playing from a position: where its buffer starts and after what delay", () => {
  assert.deepEqual(laneStart(0, 0, 10), { delay: 0, offset: 0 });
  assert.deepEqual(laneStart(4, 0, 10), { delay: 0, offset: 4 });
  // a lane that comes in later waits for its first frame
  assert.deepEqual(laneStart(1, 3, 10), { delay: 2, offset: 0 });
  assert.deepEqual(laneStart(3, 3, 10), { delay: 0, offset: 0 });
  assert.deepEqual(laneStart(5, 3, 10), { delay: 0, offset: 2 });
  // its last frame is the end: nothing left to play there or after it
  assert.equal(laneStart(13, 3, 10), null);
  assert.equal(laneStart(12.999, 3, 10) === null, false);
  assert.equal(laneStart(10, 0, 10), null);
  assert.equal(laneStart(0, 0, 0), null);
  // a position before the song plays from its start
  assert.deepEqual(laneStart(-2, 0, 10), { delay: 0, offset: 0 });
});

test("the clock moves on from its anchor and wraps inside a loop", () => {
  assert.equal(positionAt(2, 1.5, null, 100), 3.5);
  // before the sound is heard the clock waits at the anchor
  assert.equal(positionAt(2, -0.2, null, 100), 2);
  // it stops at the end of the song
  assert.equal(positionAt(98, 5, null, 100), 100);
  const loop = { start: 4, end: 8 };
  assert.equal(positionAt(5, 2, loop, 100), 7);
  near(positionAt(5, 3, loop, 100), 4);
  near(positionAt(5, 4.5, loop, 100), 5.5);
  // several times round
  near(positionAt(4, 4 * 3 + 1, loop, 100), 5);
  // exactly on the end is the start again
  assert.equal(positionAt(6, 2, loop, 100), 4);
});

test("a loop is clamped to the song; one too short or outside it is no loop", () => {
  assert.deepEqual(fitLoop({ start: 2, end: 6 }, 10), { start: 2, end: 6 });
  assert.deepEqual(fitLoop({ start: 8, end: 14 }, 10), { start: 8, end: 10 });
  assert.deepEqual(fitLoop({ start: -1, end: 3 }, 10), { start: 0, end: 3 });
  assert.equal(fitLoop({ start: 6, end: 2 }, 10), null);
  assert.equal(fitLoop({ start: 9.8, end: 12 }, 10), null);
  assert.equal(fitLoop({ start: 12, end: 14 }, 10), null);
  assert.equal(fitLoop({ start: 2, end: 2.4 }, 10), null);
  assert.deepEqual(fitLoop({ start: 2, end: 2.5 }, 10), { start: 2, end: 2.5 });
  assert.equal(fitLoop({ start: Number.NaN, end: 3 }, 10), null);
});

test("where play starts: into the loop when outside it, the start again at the end", () => {
  assert.equal(startPoint(3, null, 10), 3);
  assert.equal(startPoint(-1, null, 10), 0);
  assert.equal(startPoint(10, null, 10), 0);
  assert.equal(startPoint(12, null, 10), 0);
  assert.equal(startPoint(9.99, null, 10), 9.99);
  const loop = { start: 4, end: 8 };
  assert.equal(startPoint(5, loop, 10), 5);
  assert.equal(startPoint(4, loop, 10), 4);
  assert.equal(startPoint(8, loop, 10), 4);
  assert.equal(startPoint(1, loop, 10), 4);
  assert.equal(startPoint(10, loop, 10), 4);
});

test("a pass of the sources ends at the loop's end or the song's", () => {
  assert.equal(segmentEnd(2, null, 10), 10);
  assert.equal(segmentEnd(5, { start: 4, end: 8 }, 10), 8);
  assert.equal(segmentEnd(4, { start: 4, end: 8 }, 10), 8);
});

test("mute and solo decide which lanes are heard, at their own gains", () => {
  const lane = (gain, mute = false, solo = false) => ({ gain, mute, solo });
  assert.deepEqual(laneGains({ beat: lane(1), voice: lane(0.5) }), { beat: 1, voice: 0.5 });
  assert.deepEqual(laneGains({ beat: lane(1, true), voice: lane(0.5) }), { beat: 0, voice: 0.5 });
  assert.deepEqual(laneGains({ beat: lane(0.8), voice: lane(0.5, false, true) }), { beat: 0, voice: 0.5 });
  assert.deepEqual(laneGains({ beat: lane(0.8, false, true), voice: lane(0.5, false, true) }), { beat: 0.8, voice: 0.5 });
  // a muted solo lane stays silent and still silences the others
  assert.deepEqual(laneGains({ beat: lane(0.8), voice: lane(0.5, true, true) }), { beat: 0, voice: 0 });
  assert.deepEqual(laneGains({ beat: lane(-1), voice: lane(Number.NaN) }), { beat: 0, voice: 0 });
});

test("a lane's gain in the studio is two gain slots that add up to its dB", () => {
  assert.deepEqual(laneDbSlots(1), [0, 0]);
  near(laneDbSlots(0.5)[0] + laneDbSlots(0.5)[1], 20 * Math.log10(0.5));
  near(laneDbSlots(0.5)[0], 20 * Math.log10(0.5));
  assert.equal(laneDbSlots(0.5)[1], 0);
  // past one slot's floor the second takes the rest; silence is both at their floor
  assert.deepEqual(laneDbSlots(0.0001), [-60, -20]);
  assert.deepEqual(laneDbSlots(0), [-60, -60]);
  assert.deepEqual(laneDbSlots(-1), [-60, -60]);
  // louder than one slot's ceiling is the ceiling: lanes are not pushed past +24 dB
  assert.deepEqual(laneDbSlots(10 ** (30 / 20)), [24, 0]);
});

test("the studio's lane gains go last on the vocal and beat racks; the master and the mix's own racks stay", () => {
  const racks = {
    vocal: [{ effect: "compressor", on: true, params: { threshold: -20 } }],
    beat: [],
    master: [{ effect: "master", on: true, params: { gain: 1 } }],
  };
  const before = structuredClone(racks);
  const gain = (db) => ({ effect: "gain", on: true, params: { db } });
  const out = withLaneGains(racks, { voice: 0, beat: 1 }, 32);
  assert.deepEqual(out.vocal, [racks.vocal[0], gain(-60), gain(-60)]);
  assert.deepEqual(out.beat, [gain(0), gain(0)]);
  assert.deepEqual(out.master, racks.master);
  assert.deepEqual(racks, before);
  // a full vocal rack has no room: it plays as it is rather than not at all
  const full = { ...racks, vocal: Array.from({ length: 31 }, () => gain(0)) };
  assert.equal(withLaneGains(full, { voice: 0, beat: 1 }, 32).vocal.length, 31);
  assert.equal(withLaneGains(full, { voice: 0, beat: 1 }, 33).vocal.length, 33);
});

test("an audition's loop is the block's bars in frames, cut at the song's end", () => {
  // 120 BPM: a bar is 2 s, 96000 frames at 48 kHz
  assert.deepEqual(auditionRange(120, 2, 4, 48000, 10 ** 9), { start: 192000, end: 576000 });
  assert.deepEqual(auditionRange(120, 0, 1, 48000, 10 ** 9), { start: 0, end: 96000 });
  assert.deepEqual(auditionRange(120, 2, 4, 48000, 400000), { start: 192000, end: 400000 });
  // 90 BPM at 44.1 kHz: a bar is 8/3 s, 117600 frames
  assert.deepEqual(auditionRange(90, 1, 2, 44100, 10 ** 9), { start: 117600, end: 352800 });
  assert.throws(() => auditionRange(120, -1, 4, 48000, 10 ** 9), RangeError);
  assert.throws(() => auditionRange(120, 2, 0, 48000, 10 ** 9), RangeError);
  assert.throws(() => auditionRange(120, 2.5, 4, 48000, 10 ** 9), RangeError);
  // a block past the song's end has nothing to play
  assert.throws(() => auditionRange(120, 5, 4, 48000, 400000), RangeError);
});

// ---------- the owner, with stand-in backends ----------

class FakePlayer {
  playing = false;
  loaded = false;
  disposed = false;
  pos = 0;
  loop = null;
  gains = {};
  calls = [];
  constructor(kind, duration = 100) {
    this.kind = kind;
    this.duration = duration;
  }
  async load() {
    this.loaded = true;
    this.calls.push("load");
  }
  async play(from) {
    if (from !== undefined) this.pos = from;
    this.playing = true;
    this.calls.push(`play ${this.pos}`);
  }
  pause() {
    this.playing = false;
    this.calls.push("pause");
  }
  stop() {
    this.playing = false;
    this.pos = 0;
    this.calls.push("stop");
  }
  seek(s) {
    this.pos = s;
    this.calls.push(`seek ${s}`);
  }
  setLoop(loop) {
    this.loop = loop;
  }
  position() {
    return this.pos;
  }
  setLaneGain(lane, gain) {
    this.gains[lane] = gain;
  }
  async dispose() {
    this.disposed = true;
  }
}

class FakeAudition {
  playing = false;
  started = [];
  updates = [];
  stops = 0;
  async start(score, fromBar, bars) {
    this.playing = true;
    this.started.push([score, fromBar, bars]);
  }
  update(score) {
    this.updates.push(score);
  }
  async stop() {
    this.playing = false;
    this.stops++;
  }
  async dispose() {}
}

function owner(ready, durations = { simple: 100, studio: 100 }) {
  const made = [];
  const state = { ready };
  const audition = new FakeAudition();
  const playback = new Playback({
    simple: () => {
      const p = new FakePlayer("simple", durations.simple);
      made.push(p);
      return p;
    },
    studio: () => {
      const p = new FakePlayer("studio", durations.studio);
      made.push(p);
      return p;
    },
    ready: async () => state.ready,
    audition,
  });
  return { playback, made, state, audition };
}

test("no studio yet: the simple backend plays; once ready and stopped, the studio takes over where it was", async () => {
  const { playback, made, state } = owner(false);
  await playback.sync();
  assert.equal(playback.kind, "simple");
  const simple = made[0];
  assert.equal(simple.loaded, true);
  playback.setLoop({ start: 10, end: 20 });
  playback.setLane("beat", { gain: 0.5 });
  playback.setLane("voice", { mute: true });
  simple.pos = 12.5;
  state.ready = true;
  await playback.sync();
  assert.equal(playback.kind, "studio");
  const studio = made[1];
  assert.equal(simple.disposed, true);
  assert.equal(studio.loaded, true);
  assert.equal(studio.pos, 12.5);
  assert.deepEqual(studio.loop, { start: 10, end: 20 });
  assert.deepEqual(studio.gains, { beat: 0.5, voice: 0 });
  assert.equal(studio.playing, false);
});

test("no swap while the song plays; the pause lets it happen", async () => {
  const { playback, made, state } = owner(false);
  await playback.sync();
  await playback.play(3);
  state.ready = true;
  await playback.sync();
  assert.equal(playback.kind, "simple");
  assert.equal(made.length, 1);
  made[0].pos = 7;
  playback.pause();
  await playback.sync();
  assert.equal(playback.kind, "studio");
  assert.equal(made[1].pos, 7);
});

test("the studio going away (another take chosen) falls back to the simple backend", async () => {
  const { playback, made, state } = owner(true);
  await playback.sync();
  assert.equal(playback.kind, "studio");
  made[0].pos = 4;
  state.ready = false;
  await playback.sync();
  assert.equal(playback.kind, "simple");
  assert.equal(made[0].disposed, true);
  assert.equal(made[1].pos, 4);
});

test("the same backend again is kept, not rebuilt", async () => {
  const { playback, made } = owner(true);
  await playback.sync();
  await playback.sync();
  assert.equal(made.length, 1);
  assert.equal(made[0].disposed, false);
});

test("a position past the new backend's end starts it over", async () => {
  const { playback, made, state } = owner(false, { simple: 100, studio: 50 });
  await playback.sync();
  made[0].pos = 60;
  state.ready = true;
  await playback.sync();
  assert.equal(made[1].pos, 0);
});

test("starting an audition stops the song where it is, and playing the song stops the audition", async () => {
  const { playback, made, audition } = owner(false);
  await playback.sync();
  await playback.play(5);
  const score = { bpm: 120 };
  await playback.audition(score, 2, 4);
  assert.equal(made[0].playing, false);
  assert.equal(made[0].pos, 5);
  assert.deepEqual(audition.started, [[score, 2, 4]]);
  playback.updateAudition({ bpm: 121 });
  assert.deepEqual(audition.updates, [{ bpm: 121 }]);
  const stops = audition.stops;
  await playback.play();
  assert.equal(audition.playing, false);
  assert.equal(audition.stops, stops + 1);
  assert.equal(made[0].playing, true);
});

test("an edit while no audition plays is not sent; stopping one that is not playing does nothing", async () => {
  const { playback, audition } = owner(false);
  await playback.sync();
  playback.updateAudition({ bpm: 120 });
  assert.deepEqual(audition.updates, []);
  await playback.stopAudition();
  assert.equal(audition.stops, 0);
});

test("dispose lets go of the backend and refuses later calls", async () => {
  const { playback, made } = owner(false);
  await playback.sync();
  await playback.dispose();
  assert.equal(made[0].disposed, true);
  assert.equal(playback.kind, null);
  await playback.sync();
  assert.equal(made.length, 1);
});
