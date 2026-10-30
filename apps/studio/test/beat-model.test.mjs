// The beat editor's model: the score as the page edits it, with every value the tests expect
// taken from the score format (apps/redline-dsp/core/src/beat/score.rs, events.rs).
import assert from "node:assert/strict";
import { test } from "node:test";

import { History } from "../src/core/history.ts";
import {
  addNote,
  faultsOf,
  glidesInBar,
  notesInBar,
  barAtFrame,
  barFrame,
  cycleStep,
  deleteNote,
  glideCurves,
  hitAt,
  laneValues,
  liveScore,
  locate,
  makeUnique,
  moveNote,
  nextRoll,
  nextScoreName,
  noteName,
  noteNumber,
  notesOf,
  parseScore,
  playingAt,
  position,
  resizeNote,
  serialize,
  setGain,
  setHit,
  setLaneValue,
  setPan,
  setRack,
  setRoll,
  setSoundParam,
  songBars,
  targetOf,
  usage,
} from "../src/beat/model.ts";

// every kind of field the format has, including racks the editor does not read
const FULL = {
  version: 1,
  bpm: 140,
  swing: 56,
  seed: 7,
  key: "F# minor",
  sounds: {
    bass: { instrument: "bass808", params: { glide_ms: 150, drive: 0.4 } },
    bell: { instrument: "fm", params: { preset: "bell", ratio: 3.5 } },
    hat: { instrument: "hat_closed", swing: 62 },
    kick: { instrument: "kick" },
    piano: { instrument: "sampler", library: "vcsl", params: { tune: -2 } },
    snare: { instrument: "snare" },
  },
  patterns: {
    drums: {
      bars: 2,
      rows: {
        kick: "x...|....|x.x.|....|x...|....|x...|..x.",
        snare: "....X.......X... ....X.......X.g.",
        hat: {
          steps: "x3x.x4x.x6xgx2x.x.x.x.x.x.x.x.x.",
          velocity: Array.from({ length: 32 }, (_, i) => (i % 2 ? 0.5 : 0.9)),
          pitch: Array.from({ length: 32 }, (_, i) => (i % 4) - 1),
          decay: Array.from({ length: 32 }, () => 1.5),
          chance: Array.from({ length: 32 }, (_, i) => (i === 7 ? 0.5 : 1)),
        },
      },
    },
    fill: { bars: 1, rows: { snare: "....X...X.X.XXXX", hat: "................" } },
    low: {
      bars: 2,
      rows: {
        bass: [
          { step: 0, len: 8, note: "F#1", vel: 0.9 },
          { step: 6, len: 4, note: "C#2", vel: 0.8, passing: true },
        ],
        bell: [{ step: 16, len: 2, note: "Bb4", vel: 0.6 }],
      },
    },
  },
  sections: [
    { name: "intro", bars: 4, play: ["drums"], mute: ["kick"] },
    { name: "hook", bars: 8, play: ["drums", "low"], fill: "fill" },
  ],
  mix: {
    sounds: {
      bass: { gain_db: -3, pan: 0, rack: [{ effect: "saturate", params: { drive: 0.3 } }], sidechain: "kick" },
      hat: { pan: -0.25, rack: [{ effect: "eq", on: false, params: { b1_gain: 2 } }, { effect: "compressor" }] },
    },
    master: [{ effect: "limiter", params: { ceiling: -1 } }],
  },
};

const text = (score) => JSON.stringify(score, null, 1);
const fresh = () => parseScore(text(FULL));

test("an untouched score comes back identical, every field included", () => {
  const score = parseScore(text(FULL));
  assert.deepEqual(JSON.parse(serialize(score)), FULL);
  assert.equal(serialize(score), JSON.stringify(FULL));
});

test("something that is not a score is refused with a reason", () => {
  assert.throws(() => parseScore("[]"), /object/);
  assert.throws(() => parseScore('{"sounds": {}, "patterns": {}}'), /sections/);
  assert.throws(() => parseScore("{nope"), /JSON/);
});

test("an edited step changes only its own character; | and spaces do not count as steps", () => {
  const before = fresh();
  const row = before.patterns.drums.rows.kick;
  // step 9 is the 10th step character: "x...|....|x" puts it at index 11
  const after = setHit(before, "drums", "kick", 9, "X");
  const edited = after.patterns.drums.rows.kick;
  assert.equal(edited.length, row.length);
  const diff = [...edited].map((c, i) => (c === row[i] ? -1 : i)).filter((i) => i >= 0);
  assert.deepEqual(diff, [11]);
  assert.equal(edited[11], "X");
  // nothing else moved, and the input is untouched
  const back = structuredClone(after);
  back.patterns.drums.rows.kick = row;
  assert.deepEqual(back, before);
  assert.equal(before.patterns.drums.rows.kick, row);
  // the first and the last step of the row, past a space
  const first = setHit(before, "drums", "snare", 0, "x").patterns.drums.rows.snare;
  assert.equal(first[0], "x");
  const last = setHit(before, "drums", "snare", 31, "x").patterns.drums.rows.snare;
  assert.equal(last.at(-1), "x");
  assert.equal(last.slice(0, -1), FULL.patterns.drums.rows.snare.slice(0, -1));
});

test("a step outside the row is refused, not written", () => {
  assert.throws(() => setHit(fresh(), "drums", "kick", 32, "x"), /step 32/);
  assert.throws(() => setHit(fresh(), "drums", "kick", -1, "x"), /step -1/);
  assert.throws(() => setHit(fresh(), "drums", "nobody", 0, "x"), /no row nobody/);
});

test("a click cycles rest, hit, accent, ghost and back to rest; a roll goes to rest", () => {
  let s = fresh();
  const at = () => hitAt(s, "fill", "hat", 3);
  assert.equal(at(), ".");
  for (const want of ["x", "X", "g", "."]) {
    s = cycleStep(s, "fill", "hat", 3);
    assert.equal(at(), want);
  }
  s = setRoll(s, "fill", "hat", 3, 3);
  assert.equal(at(), "3");
  s = cycleStep(s, "fill", "hat", 3);
  assert.equal(at(), ".");
});

test("a roll takes 2, 3, 4 or 6 hits; the roll key walks them and ends on a hit", () => {
  let s = fresh();
  for (const n of [2, 3, 4, 6]) {
    s = setRoll(s, "fill", "snare", 0, n);
    assert.equal(hitAt(s, "fill", "snare", 0), String(n));
  }
  assert.throws(() => setRoll(s, "fill", "snare", 0, 5), /2, 3, 4 or 6/);
  assert.deepEqual([".", "x", "X", "g", "2", "3", "4", "6"].map(nextRoll), ["2", "2", "2", "2", "3", "4", "6", "x"]);
});

test("a lane is made from the defaults when first edited and dropped when back at them", () => {
  const before = fresh();
  const s = setLaneValue(before, "fill", "snare", "velocity", 4, 0.3);
  const row = s.patterns.fill.rows.snare;
  assert.equal(row.steps, "....X...X.X.XXXX");
  // a hit's own velocity: accent 1, hit 0.8, ghost 0.4; a rest plays nothing, 0
  const want = [0, 0, 0, 0, 0.3, 0, 0, 0, 1, 0, 1, 0, 1, 1, 1, 1];
  assert.deepEqual(row.velocity, want);
  assert.deepEqual(Object.keys(row), ["steps", "velocity"]);
  const back = setLaneValue(s, "fill", "snare", "velocity", 4, 1);
  assert.deepEqual(back, before);
  assert.equal(back.patterns.fill.rows.snare, "....X...X.X.XXXX");

  for (const [lane, value, rest] of [
    ["pitch", 3, 0],
    ["decay", 2, 1],
    ["chance", 0.25, 1],
  ]) {
    const made = setLaneValue(before, "fill", "snare", lane, 8, value).patterns.fill.rows.snare[lane];
    assert.equal(made.length, 16);
    assert.equal(made[8], value);
    assert.ok(made.every((v, i) => i === 8 || v === rest), lane);
  }
});

test("dropping one lane keeps the others, and the row stays an object while any is left", () => {
  const s = setLaneValue(fresh(), "drums", "hat", "chance", 7, 1);
  const row = s.patterns.drums.rows.hat;
  assert.equal(row.chance, undefined);
  assert.deepEqual(row.velocity, FULL.patterns.drums.rows.hat.velocity);
  assert.deepEqual(row.pitch, FULL.patterns.drums.rows.hat.pitch);
  const read = laneValues(s, "drums", "hat", "chance");
  assert.equal(read.explicit, false);
  assert.ok(read.values.every((v) => v === 1));
  assert.equal(laneValues(s, "drums", "hat", "pitch").explicit, true);
});

test("lane values are clamped to the format's ranges, the limits themselves kept", () => {
  const cases = [
    ["velocity", 1.5, 1],
    ["velocity", -0.2, 0],
    ["velocity", 0, 0],
    ["pitch", 30, 24],
    ["pitch", -30, -24],
    ["pitch", -24, -24],
    ["decay", 0, 0.1],
    ["decay", 9, 4],
    ["decay", 0.1, 0.1],
    ["chance", 2, 1],
    ["chance", -1, 0],
  ];
  for (const [lane, given, want] of cases) {
    // step 1 is a roll, so even a velocity of 0 differs from its default
    const s = setLaneValue(fresh(), "drums", "hat", lane, 1, given);
    assert.equal(laneValues(s, "drums", "hat", lane).values[1], want, `${lane} ${given}`);
  }
  assert.throws(() => setLaneValue(fresh(), "drums", "hat", "pan", 1, 0), /lane/);
  assert.throws(() => setLaneValue(fresh(), "drums", "hat", "pitch", 1, Number.NaN), /number/);
});

test("with a velocity lane, a new hit takes its own default so accent and ghost are heard", () => {
  let s = setLaneValue(fresh(), "fill", "snare", "velocity", 4, 0.3);
  s = setHit(s, "fill", "snare", 0, "g");
  assert.equal(s.patterns.fill.rows.snare.velocity[0], 0.4);
  assert.equal(s.patterns.fill.rows.snare.steps[0], "g");
  assert.equal(s.patterns.fill.rows.snare.velocity[4], 0.3);
});

test("notes are names in the score and MIDI numbers in the editor", () => {
  assert.equal(noteNumber("C-1"), 0);
  assert.equal(noteNumber("F#1"), 30);
  assert.equal(noteNumber("Bb2"), 46);
  assert.equal(noteNumber("G9"), 127);
  assert.equal(noteNumber("H2"), null);
  assert.equal(noteName(30, false), "F#1");
  assert.equal(noteName(46, true), "Bb2");
  assert.equal(noteName(0, false), "C-1");
  const notes = notesOf(fresh(), "low", "bass");
  assert.deepEqual(
    notes.map((n) => [n.index, n.step, n.len, n.midi, n.vel, n.passing]),
    [
      [0, 0, 8, 30, 0.9, false],
      [1, 6, 4, 37, 0.8, true],
    ],
  );
});

test("no note runs past the pattern end: adding, moving and resizing clamp to it", () => {
  // the low pattern is 2 bars: 32 steps
  let s = fresh();
  let made = addNote(s, "low", "bell", { step: 30, len: 4, midi: 70, vel: 0.5 });
  let n = notesOf(made.score, "low", "bell")[made.index];
  assert.deepEqual([n.step, n.len], [30, 2]);
  made = addNote(s, "low", "bell", { step: 40, len: 1, midi: 70, vel: 0.5 });
  n = notesOf(made.score, "low", "bell")[made.index];
  assert.deepEqual([n.step, n.len], [31, 1]);
  // the last step exactly fits a note of 1
  s = moveNote(fresh(), "low", "bass", 0, { step: 29 });
  assert.deepEqual([notesOf(s, "low", "bass")[0].step, notesOf(s, "low", "bass")[0].len], [24, 8]);
  s = moveNote(fresh(), "low", "bass", 0, { step: -5, midi: 200 });
  assert.deepEqual([notesOf(s, "low", "bass")[0].step, notesOf(s, "low", "bass")[0].midi], [0, 127]);
  s = resizeNote(fresh(), "low", "bass", 1, 99);
  assert.equal(notesOf(s, "low", "bass")[1].len, 26);
  s = resizeNote(fresh(), "low", "bass", 1, 0);
  assert.equal(notesOf(s, "low", "bass")[1].len, 1);
});

test("a move keeps the note's spelling when only its time moves, and its other fields", () => {
  const s = moveNote(fresh(), "low", "bass", 1, { step: 10 });
  assert.deepEqual(s.patterns.low.rows.bass[1], { step: 10, len: 4, note: "C#2", vel: 0.8, passing: true });
  const up = moveNote(fresh(), "low", "bass", 1, { midi: 39 });
  assert.equal(up.patterns.low.rows.bass[1].note, "D#2");
});

test("overlapping notes on the 808 are kept: an overlap is how it glides", () => {
  const made = addNote(fresh(), "low", "bass", { step: 4, len: 4, midi: 32, vel: 0.7 });
  const notes = notesOf(made.score, "low", "bass");
  assert.equal(notes.length, 3);
  // placed by its step among the others, which keep their order
  assert.deepEqual(notes.map((n) => n.step), [0, 4, 6]);
  assert.equal(made.index, 1);
  const gone = deleteNote(made.score, "low", "bass", 1);
  assert.deepEqual(gone, fresh());
  assert.throws(() => deleteNote(fresh(), "low", "bass", 5), /note 5/);
});

test("sound params, gain, pan and racks are written where the format keeps them", () => {
  let s = setSoundParam(fresh(), "kick", "tune", 60);
  assert.deepEqual(s.sounds.kick, { instrument: "kick", params: { tune: 60 } });
  s = setSoundParam(s, "bell", "preset", "keys");
  assert.equal(s.sounds.bell.params.preset, "keys");
  s = setGain(s, "kick", 20);
  assert.equal(s.mix.sounds.kick.gain_db, 12);
  s = setGain(s, "kick", -80);
  assert.equal(s.mix.sounds.kick.gain_db, -60);
  s = setPan(s, "snare", -3);
  assert.equal(s.mix.sounds.snare.pan, -1);
  // a score without a mix gets one
  const bare = structuredClone(fresh());
  delete bare.mix;
  assert.deepEqual(setPan(bare, "hat", 0.5).mix, { sounds: { hat: { pan: 0.5 } } });
  s = setRack(fresh(), "bass", [{ effect: "eq", on: true, params: {} }]);
  assert.deepEqual(s.mix.sounds.bass, { gain_db: -3, pan: 0, rack: [{ effect: "eq", on: true, params: {} }], sidechain: "kick" });
  assert.throws(() => setRack(fresh(), "bass", new Array(5).fill({ effect: "eq" })), /4/);
});

test("make unique gives one section its own copy; the other sections keep the shared one", () => {
  const before = fresh();
  assert.deepEqual(usage(before, "drums"), [0, 1]);
  const { score, name } = makeUnique(before, 1, "drums");
  assert.equal(name, "drums_2");
  assert.deepEqual(score.sections[0].play, ["drums"]);
  assert.deepEqual(score.sections[1].play, ["drums_2", "low"]);
  assert.deepEqual(score.patterns.drums_2, before.patterns.drums);
  assert.deepEqual(score.patterns.drums, before.patterns.drums);
  assert.deepEqual(usage(score, "drums"), [0]);
  // a taken name moves on to the next
  const again = makeUnique(score, 0, "drums");
  assert.equal(again.name, "drums_3");
  // a fill is made unique where the section uses it as a fill
  const filled = makeUnique(before, 1, "fill");
  assert.equal(filled.score.sections[1].fill, "fill_2");
  assert.throws(() => makeUnique(before, 0, "low"), /does not play low/);
});

test("a song bar resolves to its section, its pattern bars, the fill on the last bar and the mutes", () => {
  const s = fresh();
  assert.equal(songBars(s), 12);
  assert.deepEqual(locate(s, 0), { section: 0, start: 0, barInSection: 0, last: false });
  assert.deepEqual(locate(s, 4), { section: 1, start: 4, barInSection: 0, last: false });
  assert.deepEqual(locate(s, 11), { section: 1, start: 4, barInSection: 7, last: true });
  assert.equal(locate(s, 12), null);
  assert.equal(locate(s, -1), null);

  const rows = (bar) =>
    playingAt(s, bar).map((r) => [r.pattern, r.sound, r.patternBar, r.fill, r.replaced, r.muted]);
  // the intro mutes the kick; drums is 2 bars
  assert.deepEqual(rows(0), [
    ["drums", "hat", 0, false, false, false],
    ["drums", "kick", 0, false, false, true],
    ["drums", "snare", 0, false, false, false],
  ]);
  assert.deepEqual(rows(3).map((r) => r[2]), [1, 1, 1]);
  // the intro has no fill, so its last bar plays the pattern
  assert.ok(rows(3).every((r) => !r[3] && !r[4]));
  // hook bar 6 of 8: drums bar 1, low bar 1
  assert.deepEqual(rows(9), [
    ["drums", "hat", 1, false, false, false],
    ["drums", "kick", 1, false, false, false],
    ["drums", "snare", 1, false, false, false],
    ["low", "bass", 1, false, false, false],
    ["low", "bell", 1, false, false, false],
  ]);
  // the last bar: the fill replaces hat and snare, the kick and the low pattern go on
  assert.deepEqual(rows(11), [
    ["drums", "hat", 1, false, true, false],
    ["drums", "kick", 1, false, false, false],
    ["drums", "snare", 1, false, true, false],
    ["low", "bass", 1, false, false, false],
    ["low", "bell", 1, false, false, false],
    ["fill", "hat", 0, true, false, false],
    ["fill", "snare", 0, true, false, false],
  ]);
});

test("mutes apply after the fill: a muted sound stays silent in the fill too", () => {
  const s = structuredClone(fresh());
  s.sections[1].mute = ["snare"];
  const last = playingAt(s, 11).filter((r) => r.sound === "snare");
  assert.deepEqual(last.map((r) => [r.pattern, r.replaced, r.muted]), [
    ["drums", true, true],
    ["fill", false, true],
  ]);
});

test("bars and frames: 16 steps a bar at the score's tempo, swing left out", () => {
  // 120 BPM at 48 kHz: a 16th is 6000 frames, a bar 96000
  assert.equal(barFrame(0, 120, 48000), 0);
  assert.equal(barFrame(3, 120, 48000), 288000);
  assert.equal(barAtFrame(287999, 120, 48000), 2);
  assert.equal(barAtFrame(288000, 120, 48000), 3);
  // 140 BPM: a bar is 82285.714... frames, rounded as the core rounds a frame
  assert.equal(barFrame(1, 140, 48000), 82286);
  assert.equal(barFrame(7, 140, 48000), 576000);
  assert.deepEqual(position(0, 120, 48000), { bar: 1, beat: 1, step: 1 });
  assert.deepEqual(position(96000 + 3 * 24000 + 6000, 120, 48000), { bar: 2, beat: 4, step: 2 });
});

test("glides come from the event table: the note it left, the note it reached, when, how long", () => {
  const table = {
    length: 192000,
    rate: 48000,
    key: { tonic: 6, mode: "minor" },
    sounds: [
      { name: "bass", instrument: "bass808" },
      { name: "kick", instrument: "kick" },
      { name: "sub", instrument: "bass808" },
    ],
    events: [
      { id: 0, sound: 0, start: 0, off: 48000, end: 36000, note: 30, vel: 0.9, decay: 1, roll: 0, legato_from: null, section: 0, bar: 0, step: 0 },
      { id: 1, sound: 1, start: 0, off: null, end: null, note: 60, vel: 0.8, decay: 1, roll: 0, legato_from: null, section: 0, bar: 0, step: 0 },
      { id: 2, sound: 0, start: 36000, off: 60000, end: null, note: 37, vel: 0.8, decay: 1, roll: 0, legato_from: 30, section: 0, bar: 0, step: 6 },
      { id: 3, sound: 2, start: 96000, off: 120000, end: null, note: 25, vel: 0.8, decay: 1, roll: 0, legato_from: 32, section: 0, bar: 1, step: 0 },
    ],
  };
  const score = { ...fresh(), sounds: { ...fresh().sounds, sub: { instrument: "bass808" } } };
  const instruments = [{ instrument: "bass808", kind: "note", mono: true, max_voices: 1, max_life_s: 1, params: [{ name: "glide_ms", low: 10, high: 500, default: 80, unit: "ms" }] }];
  assert.deepEqual(glideCurves(table, score, instruments), [
    { sound: "bass", from: 30, to: 37, start: 36000, bar: 0, step: 6, ms: 150 },
    // no glide_ms in the score: the instrument's default
    { sound: "sub", from: 32, to: 25, start: 96000, bar: 1, step: 0, ms: 80 },
  ]);
  assert.deepEqual(glideCurves({ ...table, events: [] }, score, instruments), []);
});

test("solo and mute reach only the live copy, as section mutes; the score is untouched", () => {
  const s = fresh();
  const muted = liveScore(s, new Set(["hat"]), new Set());
  assert.deepEqual(muted.sections.map((x) => x.mute), [["kick", "hat"], ["hat"]]);
  const solo = liveScore(s, new Set(), new Set(["bass"]));
  assert.deepEqual(solo.sections[1].mute, ["bell", "hat", "kick", "piano", "snare"]);
  assert.deepEqual(s, fresh());
  assert.equal(liveScore(s, new Set(), new Set()), s);
});

test("a check's path leads to what it is about", () => {
  const s = fresh();
  assert.deepEqual(targetOf("patterns.drums.rows.hat.velocity[3]", s), { kind: "row", pattern: "drums", sound: "hat", step: 3, note: null, bar: 0 });
  // a note's error leads to the note, at the first song bar its pattern bar plays
  assert.deepEqual(targetOf("patterns.low.rows.bass[1].note", s), { kind: "row", pattern: "low", sound: "bass", step: null, note: 1, bar: 4 });
  assert.deepEqual(targetOf("patterns.low.rows.bell[0]", s), { kind: "row", pattern: "low", sound: "bell", step: null, note: 0, bar: 5 });
  assert.deepEqual(targetOf("sections[1].bars", s), { kind: "bar", bar: 4 });
  assert.deepEqual(targetOf("sections[1] (hook)", s), { kind: "bar", bar: 4 });
  assert.deepEqual(targetOf("bars 6-8", s), { kind: "bar", bar: 5 });
  assert.deepEqual(targetOf("sounds.bell.params.ratio", s), { kind: "sound", sound: "bell" });
  assert.deepEqual(targetOf("mix.sounds.hat.rack[0]", s), { kind: "sound", sound: "hat" });
  assert.equal(targetOf("bpm", s), null);
  assert.equal(targetOf("patterns.ghost.rows.x", s), null);
});

test("the next saved name counts on from the base", () => {
  assert.equal(nextScoreName("score-1.json"), "score-2.json");
  assert.equal(nextScoreName("score-19.json"), "score-20.json");
  assert.equal(nextScoreName("other.json"), null);
});

test("every operation is one step of the history, undone back to the score before it", () => {
  const start = fresh();
  const h = new History(start);
  let s = start;
  for (const op of [
    (x) => cycleStep(x, "drums", "kick", 1),
    (x) => setLaneValue(x, "drums", "kick", "pitch", 0, 5),
    (x) => addNote(x, "low", "bell", { step: 0, len: 1, midi: 60, vel: 1 }).score,
    (x) => makeUnique(x, 1, "low").score,
  ]) {
    s = op(s);
    assert.equal(h.push(s), true);
  }
  for (let i = 0; i < 4; i++) h.undo();
  assert.deepEqual(h.current, start);
  assert.equal(h.canUndo, false);
});

test("keys mean what the focus is on: G marks a bar in the strip and sets a ghost in the grid", async () => {
  const { beatKey } = await import("../src/beat/keys.ts");
  const key = (code, key = "", extra = {}) => ({ code, key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...extra });
  assert.deepEqual(beatKey(key("KeyG", "g"), "strip", false), { type: "mark", verdict: "good" });
  assert.deepEqual(beatKey(key("KeyB", "b"), "strip", false), { type: "mark", verdict: "bad" });
  assert.deepEqual(beatKey(key("KeyG", "g"), "grid", false), { type: "hit", hit: "g" });
  assert.deepEqual(beatKey(key("KeyX", "x"), "grid", false), { type: "hit", hit: "x" });
  assert.deepEqual(beatKey(key("KeyA", "a"), "grid", false), { type: "hit", hit: "X" });
  assert.deepEqual(beatKey(key("Digit6", "6"), "grid", false), { type: "roll", n: 6 });
  assert.equal(beatKey(key("Digit5", "5"), "grid", false), null);
  assert.deepEqual(beatKey(key("Delete", "Delete"), "grid", false), { type: "hit", hit: "." });
  assert.deepEqual(beatKey(key("Space", " "), "grid", false), { type: "play" });
  assert.deepEqual(beatKey(key("BracketRight", "]"), "other", false), { type: "bar", by: 1 });
  assert.deepEqual(beatKey(key("KeyZ", "z", { ctrlKey: true, shiftKey: true }), "grid", false), { type: "redo" });
  assert.deepEqual(beatKey(key("KeyZ", "z", { metaKey: true }), "other", false), { type: "undo" });
  // a letter outside the grid and the strip does nothing, and a text field keeps every key
  assert.equal(beatKey(key("KeyG", "g"), "other", false), null);
  assert.equal(beatKey(key("Space", " "), "strip", true), null);
});

test("a note row shows the selected bar: notes cut at its edges say they go on", () => {
  // low is 2 bars; the bass notes: 0..8 and 6..10, the bell: 16..18
  const notes = notesOf(fresh(), "low", "bass");
  assert.deepEqual(notesInBar(notes, 0), [
    { index: 0, start: 0, len: 8, midi: 30, cutStart: false, cutEnd: false },
    { index: 1, start: 6, len: 4, midi: 37, cutStart: false, cutEnd: false },
  ]);
  assert.deepEqual(notesInBar(notes, 1), []);
  // a note across the bar line shows in both bars, cut where the bar ends
  const across = [{ index: 0, step: 12, len: 8, midi: 40, vel: 1, passing: false }, { index: 1, step: 31, len: 1, midi: 41, vel: 1, passing: false }];
  assert.deepEqual(notesInBar(across, 0), [{ index: 0, start: 12, len: 4, midi: 40, cutStart: false, cutEnd: true }]);
  assert.deepEqual(notesInBar(across, 1), [
    { index: 0, start: 0, len: 4, midi: 40, cutStart: true, cutEnd: false },
    { index: 1, start: 15, len: 1, midi: 41, cutStart: false, cutEnd: false },
  ]);
});

test("glides are placed in steps from the start of the bar shown; others are left out", () => {
  // 120 BPM at 48 kHz: a step is 6000 frames, a bar 96000; 150 ms is 1.2 steps
  const glides = [
    { sound: "bass", from: 30, to: 37, start: 36000, bar: 0, step: 6, ms: 150 },
    { sound: "bass", from: 37, to: 30, start: 96000 + 90000, bar: 1, step: 15, ms: 150 },
    { sound: "sub", from: 30, to: 32, start: 96000, bar: 1, step: 0, ms: 60 },
  ];
  assert.deepEqual(glidesInBar(glides, "bass", 0, 120, 48000), [{ from: 30, to: 37, x: 6, w: 1.2 }]);
  assert.deepEqual(glidesInBar(glides, "bass", 1, 120, 48000), [{ from: 37, to: 30, x: 15, w: 1.2 }]);
  assert.deepEqual(glidesInBar(glides, "bass", 2, 120, 48000), []);
});

test("errors mark the rows, notes and steps they point into", () => {
  const f = faultsOf(
    [
      "patterns.low.rows.bass[1].note: C#2 is not in F# minor",
      "patterns.drums.rows.hat.velocity[3]: 2 is outside 0..1",
      "patterns.drums.rows.kick: 15 steps, a 2-bar pattern needs 32",
      "bpm: 300 is outside 40..220",
      "not a path at all",
    ],
    fresh(),
  );
  assert.deepEqual([...f.rows].sort(), ["drums/hat", "drums/kick", "low/bass"]);
  assert.deepEqual([...(f.notes.get("low/bass") ?? [])], [1]);
  assert.deepEqual([...(f.steps.get("drums/hat") ?? [])], [3]);
  assert.equal(f.notes.get("drums/kick"), undefined);
  assert.equal(faultsOf([], fresh()).rows.size, 0);
});

test("the check's warnings read in Russian; what the page does not know stays as sent", async () => {
  const { warningText, sourceText, whereText } = await import("../src/beat/warnings.ts");
  assert.equal(
    warningText({ kind: "density", where: "sections[1] (hook)", message: "6.5 hat hits a bar; trap keeps 8 to 16", source: "own choice" }),
    "хэт: 6.5 удара за такт, для trap — от 8 до 16",
  );
  assert.equal(
    warningText({ kind: "density", where: "sections[0] (intro)", message: "1.0 kick hits a bar; drill keeps 2 to 6", source: "own choice" }),
    "бочка: 1.0 удара за такт, для drill — от 2 до 6",
  );
  assert.equal(
    warningText({ kind: "repeat", where: "bars 1-9", message: "the same bar plays 9 times in a row; change one thing every 4 to 8 bars", source: "blog, unverified" }),
    "один и тот же такт 9 раз подряд — меняй что-нибудь раз в 4–8 тактов",
  );
  assert.equal(
    warningText({ kind: "unchanged", where: "bars 17-31", message: "the same sounds (bass, hat) play for 15 bars; bring one in or out every 8 bars at most", source: "blog, unverified" }),
    "одни и те же звуки (bass, hat) 15 тактов подряд — вводи или убирай звук хотя бы раз в 8 тактов",
  );
  assert.equal(
    warningText({ kind: "clash", where: "bars 3, 5", message: "a kick starts within 30 ms of an 808 note 4 times; move the kick or the note", source: "blog, unverified" }),
    "бочка ближе 30 мс к началу ноты 808 — 4 раза; сдвинь бочку или ноту",
  );
  // a known kind with a message of another shape, and an unknown kind, come through as sent
  assert.equal(warningText({ kind: "repeat", where: "", message: "something new", source: "" }), "something new");
  assert.equal(warningText({ kind: "novel", where: "", message: "a new check", source: "" }), "a new check");
  assert.equal(sourceText("blog, unverified"), "источник: блог, не проверено");
  assert.equal(sourceText("own choice"), "источник: своё решение");
  assert.equal(sourceText("a paper"), "источник: a paper");
  assert.equal(whereText("bars 17-31"), "такты 17–31");
  assert.equal(whereText("bars 3"), "такт 3");
  assert.equal(whereText("sections[1] (hook)"), "секция «hook»");
  assert.equal(whereText("patterns.x.rows.y"), "patterns.x.rows.y");
});

test("an error's place reads as pattern, sound and note or step, not as a JSON path", async () => {
  const { pathText } = await import("../src/beat/warnings.ts");
  assert.equal(pathText("patterns.keys_prog.rows.keys[4].note"), "keys_prog · keys · нота 5");
  assert.equal(pathText("patterns.drums.rows.hat.velocity[12]"), "drums · hat · шаг 13, громкость");
  assert.equal(pathText("patterns.drums.rows.hat"), "drums · hat");
  assert.equal(pathText("sections[0].bars"), "секция 1 · bars");
  assert.equal(pathText("bpm"), "bpm");
  assert.equal(pathText(""), "");
});
