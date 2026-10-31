// The song screen's view model: which lanes show, the song facts read from the server (with or
// without the new facts), the take lanes' cap, the ruler's click and drag, the chip's words and
// the marks of the 808 and drum lanes.
import assert from "node:assert/strict";
import { test } from "node:test";

import { beatKindOf, capTakes, chipText, editorOpenAfter, factsOf, keyedBlock, pickAfterUndo, playbackKey, rulerGesture, scoreMarks, visibleLanes } from "../src/daw/view.ts";

const stations = (over = {}) => ({
  beat: { kind: "file", file: "input/beats/b.mp3", bpm: 120 },
  text: { seed: 1, version: 1, command: "" },
  voice: { kind: "takes", folder: "input/takes/s", takes: ["take-01.wav", "take-02.wav"], chosen: null },
  mix: { ready: false, reason: "no voice", variant: null },
  result: { folder: null },
  ...over,
});
const reply = (over = {}, extra = {}) => ({ song: { name: "s", rev: 1, references: ["input/refs/a.wav", "input/refs/b.wav"] }, stations: stations(over), ...extra });

// ---------- beat kind ----------

test("a beat kind: a seed is own, a flip render is a flip, any other file is a file", () => {
  assert.equal(beatKindOf({ kind: "own", seed: 3, bpm: 140, file: "work/beats/3/beat.wav" }), "own");
  assert.equal(beatKindOf({ kind: "file", bpm: 120, file: "work/flips/0123abcd/flip-2.wav" }), "flip");
  assert.equal(beatKindOf({ kind: "file", bpm: 120, file: "input/beats/b.mp3" }), "file");
  // a flip folder name is eight hex digits; anything else is a plain file
  assert.equal(beatKindOf({ kind: "file", bpm: 120, file: "work/flips/xyz/flip-2.wav" }), "file");
});

// ---------- facts ----------

test("facts without the server's new fields come from the stations", () => {
  const f = factsOf(reply(), [], { flipAnalysed: false });
  assert.equal(f.beatKind, "file");
  assert.equal(f.hasBeatAudio, true);
  assert.equal(f.beatAnalysed, false);
  assert.equal(f.takes, 2);
  assert.equal(f.chosen, null);
  assert.equal(f.voiceKind, "own");
  assert.equal(f.studioReady, false);
  // unknown staleness counts as stale, so export runs what it needs
  assert.equal(f.mixStale, true);
  assert.equal(f.refsCount, 2);
  assert.equal(f.exporting, false);
  assert.equal(f.bpm, 120);
});

test("an own beat is analysed and has audio only once rendered", () => {
  const own = (rendered) => reply({ beat: { kind: "own", seed: 3, bpm: 140, file: "work/beats/3/beat.wav", rendered } });
  assert.equal(factsOf(own(false), [], { flipAnalysed: null }).hasBeatAudio, false);
  assert.equal(factsOf(own(true), [], { flipAnalysed: null }).hasBeatAudio, true);
  assert.equal(factsOf(own(false), [], { flipAnalysed: null }).beatAnalysed, true);
});

test("the server's facts win over the derived ones, as a sibling object or inside the stations", () => {
  const given = { beat_audio: false, beat_analysed: true, studio_ready: true, mix_stale: false, refs: 0, voice_kind: "suno", exporting: true, beat_dirty: true };
  for (const r of [reply({}, { facts: given }), reply({ ...given })]) {
    const f = factsOf(r, [], { flipAnalysed: false });
    assert.equal(f.hasBeatAudio, false);
    assert.equal(f.beatAnalysed, true);
    assert.equal(f.studioReady, true);
    assert.equal(f.mixStale, false);
    assert.equal(f.refsCount, 0);
    assert.equal(f.voiceKind, "suno");
    assert.equal(f.exporting, true);
    assert.equal(f.beatDirty, true);
  }
});

test("a running result job is an export in progress; a finished one is not", () => {
  const job = (state) => ({ kind: "result", state, reason: null, started: 1, finished: null });
  assert.equal(factsOf(reply(), [job("running")], { flipAnalysed: true }).exporting, true);
  assert.equal(factsOf(reply(), [job("done")], { flipAnalysed: true }).exporting, false);
});

test("a Suno voice has no takes to count", () => {
  const f = factsOf(reply({ voice: { kind: "suno", file: "input/suno/v.wav", separated: false } }), [], { flipAnalysed: true });
  assert.equal(f.voiceKind, "suno");
  assert.equal(f.takes, 0);
  assert.deepEqual(f.takeNames, []);
});

// ---------- lanes ----------

const facts = (over = {}) => ({ beatKind: "file", beatAnalysed: true, voiceKind: "own", ...over });

test("an own beat shows text, voice, takes, beat, 808 and drums in that order", () => {
  const v = visibleLanes(facts({ beatKind: "own" }));
  assert.deepEqual(v.lanes, ["text", "voice", "takes", "beat", "bass", "drums"]);
  assert.equal(v.beat, "blocks");
});

test("a flip shows its blocks on the beat lane and no 808 or drum lanes", () => {
  const v = visibleLanes(facts({ beatKind: "flip" }));
  assert.deepEqual(v.lanes, ["text", "voice", "takes", "beat"]);
  assert.equal(v.beat, "blocks");
});

test("a type beat not analysed shows one waveform lane with the analyse offer", () => {
  const v = visibleLanes(facts({ beatKind: "file", beatAnalysed: false }));
  assert.deepEqual(v.lanes, ["text", "voice", "takes", "beat"]);
  assert.equal(v.beat, "analyse");
  assert.equal(visibleLanes(facts({ beatKind: "file", beatAnalysed: true })).beat, "file");
});

test("a Suno voice hides the take lanes and keeps the voice lane", () => {
  const v = visibleLanes(facts({ voiceKind: "suno", beatKind: "own" }));
  assert.deepEqual(v.lanes, ["text", "voice", "beat", "bass", "drums"]);
});

// ---------- the take lanes ----------

const names = (n) => Array.from({ length: n }, (_, i) => `take-0${i + 1}.wav`);

test("three takes or fewer all show, nothing behind «ещё»", () => {
  assert.deepEqual(capTakes({ names: names(3), ranks: {}, chosen: null, live: null }), { shown: ["take-03.wav", "take-02.wav", "take-01.wav"], hidden: 0 });
  assert.deepEqual(capTakes({ names: [], ranks: {}, chosen: null, live: null }), { shown: [], hidden: 0 });
});

test("more than three: the best three by rank, the rest counted", () => {
  const ranks = { "take-01.wav": 4, "take-02.wav": 1, "take-03.wav": 5, "take-04.wav": 2, "take-05.wav": 3 };
  assert.deepEqual(capTakes({ names: names(5), ranks, chosen: null, live: null }), { shown: ["take-02.wav", "take-04.wav", "take-05.wav"], hidden: 2 });
});

test("unranked takes come after ranked ones, newest first", () => {
  const ranks = { "take-01.wav": 1 };
  assert.deepEqual(capTakes({ names: names(5), ranks, chosen: null, live: null }), { shown: ["take-01.wav", "take-05.wav", "take-04.wav"], hidden: 2 });
});

test("the chosen take and the take being recorded always show, the best fill the rest", () => {
  const ranks = { "take-01.wav": 1, "take-02.wav": 2, "take-03.wav": 3, "take-04.wav": 4 };
  const got = capTakes({ names: names(4), ranks, chosen: "take-04.wav", live: "take-05.wav" });
  assert.deepEqual(got, { shown: ["take-05.wav", "take-04.wav", "take-01.wav"], hidden: 2 });
});

test("opened, every take shows", () => {
  const got = capTakes({ names: names(5), ranks: {}, chosen: null, live: null, all: true });
  assert.equal(got.shown.length, 5);
  assert.equal(got.hidden, 0);
});

// ---------- the ruler ----------

test("a press without a drag is a seek to that point", () => {
  assert.deepEqual(rulerGesture(150, 152, 60, 57), { kind: "seek", bar: 152 / 60 });
  // before the first bar line and past the last, the seek stays on the timeline
  assert.deepEqual(rulerGesture(-5, -5, 60, 57), { kind: "seek", bar: 0 });
  assert.deepEqual(rulerGesture(9000, 9000, 60, 57), { kind: "seek", bar: 57 });
});

test("a drag sets a loop on whole bars covering it, either way round", () => {
  assert.deepEqual(rulerGesture(130, 250, 60, 57), { kind: "loop", fromBar: 2, toBar: 5 });
  assert.deepEqual(rulerGesture(250, 130, 60, 57), { kind: "loop", fromBar: 2, toBar: 5 });
  // on the bar lines exactly
  assert.deepEqual(rulerGesture(120, 240, 60, 57), { kind: "loop", fromBar: 2, toBar: 4 });
});

test("a short drag inside one bar loops that bar; a drag past the end stops at the last bar", () => {
  assert.deepEqual(rulerGesture(125, 140, 60, 57), { kind: "loop", fromBar: 2, toBar: 3 });
  assert.deepEqual(rulerGesture(3300, 9000, 60, 57), { kind: "loop", fromBar: 55, toBar: 57 });
});

// ---------- the chip ----------

test("the chip names the next step", () => {
  assert.equal(chipText("analyse"), "дальше: разобрать бит");
  assert.equal(chipText("listen"), "дальше: послушать");
  assert.equal(chipText("record"), "дальше: запись (R)");
  assert.equal(chipText("pick"), "дальше: выбрать дубль ★");
  assert.equal(chipText("prepare-mix"), "дальше: свести");
  assert.equal(chipText("export"), "дальше: экспорт");
  assert.equal(chipText("done"), "готово");
});

// ---------- score marks ----------

const score = () => ({
  version: 1,
  bpm: 120,
  seed: 1,
  key: "C",
  sounds: { kick: { instrument: "kick" }, snare: { instrument: "snare" }, hat: { instrument: "hat_closed" }, bass: { instrument: "bass808" }, keys: { instrument: "fm" } },
  patterns: {
    a: {
      bars: 2,
      rows: {
        kick: "x.......x.......|x...............",
        snare: "....x.......x...|....x.......x...",
        hat: "x.x.x.x.x.x.x.x.|................",
        bass: [
          { step: 0, len: 4, note: "C2", vel: 0.8 },
          { step: 20, len: 2, note: "G1", vel: 0.8 },
        ],
        keys: [{ step: 0, len: 16, note: "C4", vel: 0.5 }],
      },
    },
    f: { bars: 1, rows: { snare: "xxxxxxxxxxxxxxxx" } },
  },
  sections: [
    { name: "one", bars: 3, play: ["a"], fill: "f" },
    { name: "two", bars: 1, play: ["a"], mute: ["kick", "bass"] },
  ],
});

test("808 notes land on the bars the pattern plays, repeated and on play-order bars", () => {
  const { bass } = scoreMarks(score());
  // section one: bars 0,1 play a's bars 0,1, bar 2 plays a's bar 0 again; section two mutes the 808
  assert.deepEqual(bass, [
    { bar: 0, step: 0, len: 4, midi: 36 },
    { bar: 1, step: 4, len: 2, midi: 31 },
    { bar: 2, step: 0, len: 4, midi: 36 },
  ]);
});

test("drum hits sit on rows kick 0, snare 1, hats 2; a fill replaces its rows on the last bar; mute drops a row", () => {
  const { drums } = scoreMarks(score());
  const at = (bar, row) => drums.filter((d) => d.bar === bar && d.row === row).map((d) => d.step);
  assert.deepEqual(at(0, 0), [0, 8]);
  assert.deepEqual(at(0, 1), [4, 12]);
  assert.deepEqual(at(0, 2), [0, 2, 4, 6, 8, 10, 12, 14]);
  assert.deepEqual(at(1, 2), []);
  // bar 2 is the fill bar of section one: the snare row is the fill's
  assert.deepEqual(at(2, 1), Array.from({ length: 16 }, (_, i) => i));
  assert.deepEqual(at(2, 0), [0, 8]);
  // section two mutes the kick
  assert.deepEqual(at(3, 0), []);
  assert.deepEqual(at(3, 1), [4, 12]);
  // a keys part is neither 808 nor drums
  assert.ok(drums.every((d) => d.row >= 0 && d.row <= 2));
});

// ---------- keys and editor tabs ----------

import { dawKey, editorTabs } from "../src/daw/view.ts";

const key = (code, mods = {}) => ({ code, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });

test("the screen's keys: Space, R, L, Home, Ctrl+Z and Ctrl+Y / Ctrl+Shift+Z", () => {
  assert.equal(dawKey(key("Space"), false), "play");
  assert.equal(dawKey(key("KeyR"), false), "record");
  assert.equal(dawKey(key("KeyL"), false), "loop");
  assert.equal(dawKey(key("Home"), false), "home");
  assert.equal(dawKey(key("KeyZ", { ctrlKey: true }), false), "undo");
  assert.equal(dawKey(key("KeyZ", { metaKey: true }), false), "undo");
  assert.equal(dawKey(key("KeyY", { ctrlKey: true }), false), "redo");
  assert.equal(dawKey(key("KeyZ", { ctrlKey: true, shiftKey: true }), false), "redo");
});

test("Ctrl+D duplicates and Delete deletes; neither is taken while typing or with other modifiers", () => {
  assert.equal(dawKey(key("KeyD", { ctrlKey: true }), false), "duplicate");
  assert.equal(dawKey(key("KeyD", { metaKey: true }), false), "duplicate");
  assert.equal(dawKey(key("Delete"), false), "delete");
  assert.equal(dawKey(key("KeyD", { ctrlKey: true }), true), null);
  assert.equal(dawKey(key("Delete"), true), null);
  assert.equal(dawKey(key("KeyD", { ctrlKey: true, shiftKey: true }), false), null);
  assert.equal(dawKey(key("KeyD"), false), null);
  assert.equal(dawKey(key("Delete", { altKey: true }), false), null);
});

test("no key is taken while typing, with Alt, or with Ctrl on a plain key", () => {
  assert.equal(dawKey(key("Space"), true), null);
  assert.equal(dawKey(key("KeyR"), true), null);
  assert.equal(dawKey(key("KeyZ", { ctrlKey: true }), true), null);
  assert.equal(dawKey(key("KeyR", { altKey: true }), false), null);
  assert.equal(dawKey(key("KeyR", { ctrlKey: true }), false), null);
  assert.equal(dawKey(key("KeyB"), false), null);
});

test("the editor shows only the tabs of the selected lane", () => {
  assert.deepEqual(editorTabs("bass", "own"), ["roll"]);
  assert.deepEqual(editorTabs("drums", "own"), ["drums"]);
  assert.deepEqual(editorTabs("beat", "own"), ["roll", "drums", "fx"]);
  // a flip or a plain file has no roll or grid in this version
  assert.deepEqual(editorTabs("beat", "flip"), ["roll", "drums", "fx"]);
  assert.deepEqual(editorTabs("beat", "file"), ["fx"]);
  assert.deepEqual(editorTabs("voice", "file"), ["fx"]);
  assert.deepEqual(editorTabs("takes", "own"), ["fx"]);
  assert.deepEqual(editorTabs("text", "own"), ["text"]);
});

test("an own beat renders to the same file: a new render alone makes the player load the beat again", () => {
  const f = { chosen: "take-01.wav", studioReady: false, hasBeatAudio: true };
  const first = playbackKey(f, null, 1);
  assert.equal(playbackKey(f, null, 1), first);
  assert.notEqual(playbackKey(f, null, 2), first);
  // the other reasons stay reasons
  assert.notEqual(playbackKey({ ...f, chosen: "take-02.wav" }, null, 1), first);
  assert.notEqual(playbackKey({ ...f, studioReady: true }, null, 1), first);
  assert.notEqual(playbackKey({ ...f, hasBeatAudio: false }, null, 1), first);
  assert.notEqual(playbackKey(f, "input/beats/b.mp3", 1), first);
});

test("selecting a lane opens the editor; picking the open lane again keeps what the user chose", () => {
  // the first selection opens the collapsed editor
  assert.equal(editorOpenAfter(false, null, "beat"), true);
  // another lane opens it again after the user collapsed it
  assert.equal(editorOpenAfter(false, "beat", "voice"), true);
  assert.equal(editorOpenAfter(true, "beat", "voice"), true);
  // the same lane: collapsed stays collapsed, open stays open
  assert.equal(editorOpenAfter(false, "beat", "beat"), false);
  assert.equal(editorOpenAfter(true, "beat", "beat"), true);
});

test("a beat file read into a flip is edited as a flip before its first render", () => {
  assert.equal(factsOf(reply(), [], { flipAnalysed: true }).beatKind, "flip");
  assert.equal(factsOf(reply(), [], { flipAnalysed: false }).beatKind, "file");
  assert.equal(factsOf(reply(), [], { flipAnalysed: null }).beatKind, "file");
});

test("Delete and Ctrl+D act on the picked block only while a beat lane is selected", () => {
  for (const lane of ["beat", "bass", "drums"]) assert.equal(keyedBlock(lane, 1, 3), 1);
  for (const lane of ["text", "voice", "takes", null]) assert.equal(keyedBlock(lane, 1, 3), null);
  // first and last block; nothing picked; a pick past the blocks left
  assert.equal(keyedBlock("beat", 0, 3), 0);
  assert.equal(keyedBlock("beat", 2, 3), 2);
  assert.equal(keyedBlock("beat", null, 3), null);
  assert.equal(keyedBlock("beat", 3, 3), null);
  assert.equal(keyedBlock("beat", 0, 0), null);
});

test("after an undo the picked block is the one the editor shows; nothing picked stays so", () => {
  assert.equal(pickAfterUndo(2, 1), 1);
  assert.equal(pickAfterUndo(0, 0), 0);
  assert.equal(pickAfterUndo(2, null), null);
  assert.equal(pickAfterUndo(null, 1), null);
});
