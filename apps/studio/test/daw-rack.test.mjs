// The channel rack of an own beat: which pattern it shows, what a press on a step makes, the
// knobs' one undo step per drag, pattern-wide edits with no share question, and the piano roll
// picked by sound.
import assert from "node:assert/strict";
import { test } from "node:test";

import { drumSteps, notesOf } from "../src/beat/model.ts";
import { BeatDraft } from "../src/daw/draft.ts";
import { gainAt, gainPosition, lockedText, panAt, panPosition, RackPick, rackStep, rowKind } from "../src/daw/rack.ts";
import { rollRange, rollRangeFor, rollRowOf, scoreRoll } from "../src/daw/roll.ts";

const settle = async () => {
  for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
};

const spec = (instrument, kind) => ({ instrument, kind, mono: false, max_voices: 8, max_life_s: 2, params: [] });
const SPECS = [spec("kick", "drum"), spec("snare", "drum"), spec("hat_closed", "drum"), spec("bass808", "note"), spec("pluck", "note")];

// c: unplayed and first; a: two bars, in the verse with d and in the hook; b: the intro; d has the hat
const score = () => ({
  version: 1,
  bpm: 140,
  seed: 3,
  key: "C minor",
  sounds: {
    kick: { instrument: "kick" },
    snare: { instrument: "snare" },
    hat: { instrument: "hat_closed" },
    rim: { instrument: "snare" },
    bass: { instrument: "bass808" },
    keys: { instrument: "pluck" },
  },
  patterns: {
    c: { bars: 1, rows: {} },
    a: {
      bars: 2,
      rows: {
        kick: "x...x...x...x...|x...x...x...x...",
        snare: { steps: "....X.......g...|....x.......2..." },
        bass: [{ step: 0, len: 4, note: "C2", vel: 0.8 }],
      },
    },
    b: { bars: 1, rows: { kick: "x...............", keys: [{ step: 0, len: 8, note: "C4", vel: 0.8 }] } },
    d: { bars: 1, rows: { hat: "x.x.x.x.x.x.x.x." } },
  },
  sections: [
    { name: "intro", bars: 4, play: ["b"] },
    { name: "verse", bars: 8, play: ["a", "d"] },
    { name: "hook", bars: 8, play: ["a"] },
  ],
});

const hits = (s, p, sound) => drumSteps(s, p, sound).join("");

// ---------- the pattern shown ----------

test("the rack starts on the first pattern the selected block plays, else on the score's first", () => {
  const s = score();
  assert.equal(new RackPick().current(s, 3, 1), "a");
  assert.equal(new RackPick().current(s, 3, 0), "b");
  assert.equal(new RackPick().current(s, 3, null), "c");
  // a block that plays nothing, or a section that is not there
  const silent = { ...s, sections: [{ name: "gap", bars: 2, play: [] }] };
  assert.equal(new RackPick().current(silent, 3, 0), "c");
  assert.equal(new RackPick().current(s, 3, 7), "c");
  // a name in play with no pattern behind it is passed over
  const stale = { ...s, sections: [{ name: "x", bars: 2, play: ["gone", "b"] }] };
  assert.equal(new RackPick().current(stale, 3, 0), "b");
  assert.equal(new RackPick().current({ ...s, patterns: {} }, 3, 1), null);
});

test("a drum lane opens on a pattern with drum rows, an 808 lane on one with notes", () => {
  const s = {
    bpm: 140,
    sounds: { kick: { instrument: "kick" }, bass: { instrument: "bass808" } },
    patterns: {
      low: { bars: 2, rows: { bass: [{ step: 0, len: 4, note: "C1", vel: 1 }] } },
      drums: { bars: 2, rows: { kick: "x" + ".".repeat(31) } },
    },
    sections: [
      { name: "intro", bars: 8, play: ["low"] },
      { name: "hook", bars: 8, play: ["low", "drums"] },
    ],
  };
  // the intro plays no drums: the score's first pattern that has them
  assert.equal(new RackPick().current(s, 3, 0, "drum"), "drums");
  assert.equal(new RackPick().current(s, 3, 1, "drum"), "drums");
  assert.equal(new RackPick().current(s, 3, 1, "note"), "low");
  // no wish: the block's first, as before
  assert.equal(new RackPick().current(s, 3, 0, null), "low");
  // a wish nothing answers falls back to the block's first
  const noDrums = { ...s, patterns: { low: s.patterns.low }, sections: [s.sections[0]] };
  assert.equal(new RackPick().current(noDrums, 3, 0, "drum"), "low");
});

test("a pattern picked in the rack stays over the block's until it is gone", () => {
  const pick = new RackPick();
  pick.choose("b");
  assert.equal(pick.current(score(), 3, 1), "b");
  assert.equal(pick.current(score(), 3, 2), "b");
  // undone or deleted: the score's first pattern, which then stays
  const without = score();
  delete without.patterns.b;
  assert.equal(pick.current(without, 3, 1), "c");
  assert.equal(pick.current(score(), 3, 1), "c");
});

test("another seed is another beat: the pick goes and the block decides again", () => {
  const pick = new RackPick();
  assert.equal(pick.current(score(), 3, 1), "a");
  pick.choose("d");
  assert.equal(pick.current(score(), 3, 1), "d");
  assert.equal(pick.current(score(), 4, 1), "a");
  // following a block picked on the timeline drops the pick too
  pick.choose("d");
  pick.follow();
  assert.equal(pick.current(score(), 4, 0), "b");
});

// ---------- a press on a step ----------

test("a click on a rest puts a hit, or the roll picked; a click on any hit takes it away", () => {
  const s = score();
  const put = rackStep(s, SPECS, "a", "kick", 1, "click", 0);
  assert.equal(put.ok, true);
  assert.equal(hits(put.score, "a", "kick"), "xx..x...x...x...x...x...x...x...");
  assert.equal(hits(rackStep(s, SPECS, "a", "kick", 1, "click", 3).score, "a", "kick"), "x3..x...x...x...x...x...x...x...");
  assert.equal(hits(rackStep(s, SPECS, "a", "kick", 0, "click", 4).score, "a", "kick"), "....x...x...x...x...x...x...x...");
  // an accent, a ghost and a roll all go on a click, whatever roll is picked
  assert.equal(drumSteps(rackStep(s, SPECS, "a", "snare", 4, "click", 2).score, "a", "snare")[4], ".");
  assert.equal(drumSteps(rackStep(s, SPECS, "a", "snare", 12, "click", 0).score, "a", "snare")[12], ".");
  assert.equal(drumSteps(rackStep(s, SPECS, "a", "snare", 28, "click", 6).score, "a", "snare")[28], ".");
  // the given score is left as it was
  assert.deepEqual(s, score());
});

test("a click lands on the pattern step of the bar shown: the second bar's first step is 16", () => {
  const made = rackStep(score(), SPECS, "a", "snare", 16 + 2, "click", 0);
  assert.equal(made.ok, true);
  assert.equal(hits(made.score, "a", "snare"), "....X.......g.....x.x.......2...");
  // the bar mark of the row is kept
  assert.equal(made.score.patterns.a.rows.snare.steps, "....X.......g...|..x.x.......2...");
});

test("shift-click cycles a hit through accent, ghost and plain: x → X → g → x", () => {
  const at = (s) => drumSteps(s, "a", "kick")[0];
  let s = score();
  s = rackStep(s, SPECS, "a", "kick", 0, "accent", 0).score;
  assert.equal(at(s), "X");
  s = rackStep(s, SPECS, "a", "kick", 0, "accent", 0).score;
  assert.equal(at(s), "g");
  s = rackStep(s, SPECS, "a", "kick", 0, "accent", 0).score;
  assert.equal(at(s), "x");
  // a rest starts the cycle at the accent; a roll counts as a plain hit
  assert.equal(drumSteps(rackStep(score(), SPECS, "a", "kick", 1, "accent", 0).score, "a", "kick")[1], "X");
  assert.equal(drumSteps(rackStep(score(), SPECS, "a", "snare", 28, "accent", 0).score, "a", "snare")[28], "X");
});

test("a right click clears the step and nothing else", () => {
  const s = score();
  const cleared = rackStep(s, SPECS, "a", "snare", 4, "clear", 3);
  assert.equal(hits(cleared.score, "a", "snare"), "............g.......x.......2...");
  const rest = rackStep(s, SPECS, "a", "snare", 5, "clear", 3);
  assert.equal(hits(rest.score, "a", "snare"), hits(s, "a", "snare"));
  // the other rows and patterns are untouched
  assert.deepEqual(cleared.score.patterns.b, s.patterns.b);
  assert.equal(hits(cleared.score, "a", "kick"), hits(s, "a", "kick"));
});

test("a step past the pattern's end is refused, not written", () => {
  const made = rackStep(score(), SPECS, "a", "kick", 32, "click", 0);
  assert.equal(made.ok, false);
  assert.match(made.reason, /шаг/i);
});

test("a click on a sound the pattern has no row for makes the row, then sets the step", () => {
  const s = score();
  const made = rackStep(s, SPECS, "c", "kick", 15, "click", 0);
  assert.equal(made.ok, true);
  assert.equal(made.score.patterns.c.rows.kick, "...............x");
  // a two-bar pattern gets a two-bar row
  const twice = rackStep(s, SPECS, "a", "rim", 17, "click", 0);
  assert.equal(twice.score.patterns.a.rows.rim, `${".".repeat(17)}x${".".repeat(14)}`);
  const b = rackStep(s, SPECS, "b", "snare", 0, "accent", 0);
  assert.equal(b.score.patterns.b.rows.snare, "X...............");
  // a right click on a missing row writes nothing
  const none = rackStep(s, SPECS, "c", "kick", 3, "clear", 0);
  assert.equal(none.ok, true);
  assert.deepEqual(none.score.patterns.c.rows, {});
});

test("a row another pattern of the same section holds is refused with the reason", () => {
  const s = score();
  const made = rackStep(s, SPECS, "a", "hat", 0, "click", 0);
  assert.deepEqual(made, { ok: false, reason: "В секции verse звук hat уже играет паттерн d." });
  assert.equal(lockedText(s, "d"), "в паттерне 4 · verse");
  assert.equal(lockedText(s, "c"), "в паттерне 1");
  // with no instrument list the sound's kind is unknown and no row is made
  assert.deepEqual(rackStep(s, [], "c", "kick", 0, "click", 0), { ok: false, reason: "Инструмента kick нет." });
});

test("a sound's kind comes from its instrument, else from the row it has", () => {
  const s = score();
  assert.equal(rowKind(s, SPECS, "a", "kick"), "drum");
  assert.equal(rowKind(s, SPECS, "c", "keys"), "note");
  assert.equal(rowKind(s, [], "a", "bass"), "note");
  assert.equal(rowKind(s, [], "a", "snare"), "drum");
  assert.equal(rowKind(s, [], "c", "kick"), null);
});

// ---------- the knobs ----------

test("the volume knob spans -60..12 dB in half-dB steps, the pan knob -1..1", () => {
  assert.equal(gainAt(0), -60);
  assert.equal(gainAt(1), 12);
  assert.equal(gainAt(gainPosition(0)), 0);
  assert.equal(gainAt(gainPosition(-6)), -6);
  assert.equal(gainAt(0.5), -24);
  assert.equal(gainPosition(-100), 0);
  assert.equal(gainPosition(40), 1);
  assert.equal(panAt(0), -1);
  assert.equal(panAt(1), 1);
  assert.equal(panAt(0.5), 0);
  assert.equal(panPosition(-0.5), 0.25);
  assert.equal(panAt(panPosition(0.3)), 0.3);
});

// ---------- the editor ----------

function fakeDom() {
  const el = () => {
    const e = {
      children: [],
      style: { setProperty() {} },
      dataset: {},
      classList: { add() {}, remove() {}, toggle() {} },
      append(...n) { this.children.push(...n); },
      replaceChildren(...n) { this.children = n; },
      setAttribute() {},
      addEventListener(type, fn) { (this.handlers ??= {})[type] = fn; },
      removeEventListener() {},
      querySelectorAll: () => [],
      focus() {},
    };
    return e;
  };
  globalThis.document = { createElement: el, createElementNS: el };
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
}

async function editorSetup({ playback = null } = {}) {
  fakeDom();
  const { Editor } = await import("../src/daw/editor.ts");
  const base = "score-1.json";
  const changes = [];
  const editor = new Editor({
    base: undefined,
    token: "t",
    api: {},
    beatApi: {
      draft: async () => ({ score: score(), base }),
      session: async () => ({ score_file: base, score: score(), instruments: SPECS }),
      saveDraft: async () => {},
      save: async () => ({ file: "score-2.json" }),
      render: async () => ({ state: "running", score_file: null, error: null }),
      renderStatus: async () => ({ state: "idle", score_file: null, error: null }),
    },
    flipApi: {},
    flipDraft: { doc: null, flush: async () => {} },
    confirm: () => true,
    reply: () => ({ stations: { beat: { kind: "own", seed: 3 }, text: { seed: null } } }),
    facts: () => ({ beatKind: "own", beatDirty: false, hasBeatAudio: true }),
    jobs: () => [],
    flip: () => null,
    clips: () => [
      { index: 0, fromBar: 0, bars: 4, label: "intro" },
      { index: 1, fromBar: 4, bars: 8, label: "verse" },
      { index: 2, fromBar: 12, bars: 8, label: "hook" },
    ],
    playheadBar: () => 0,
    playback: () => playback,
    studio: () => null,
    stopSong() {},
    startJob: async () => {},
    change: async () => true,
    reload: async () => {},
    beatRendered: async () => {},
    layout: () => ({ startBar: 0, barsPerLine: 1 }),
    nudge() {},
    scoreChanged: (s) => changes.push(s),
    openTab() {},
    beatState() {},
    repaint() {},
    say() {},
    overlay: {},
  });
  editor.prepare();
  await settle();
  return { editor, changes };
}

test("a knob drag is heard live and makes one undo step: undo goes back to before the drag", async () => {
  const heard = [];
  const { editor, changes } = await editorSetup({ playback: { updateAudition: (s) => heard.push(s.mix?.sounds?.kick?.gain_db) } });
  const before = editor.score;
  const at = (db) => ({ ...before, mix: { sounds: { kick: { gain_db: db } } } });
  for (const db of [-1, -2, -3, -4]) assert.equal(editor.editPattern(at(db), { live: true }), true);
  assert.deepEqual(heard, [-1, -2, -3, -4]);
  // the drag is not an edit yet
  assert.equal(editor.score, before);
  assert.equal(changes.length, 0);
  assert.equal(editor.editPattern(at(-4)), true);
  assert.equal(editor.score.mix.sounds.kick.gain_db, -4);
  assert.equal(changes.length, 1);
  assert.equal(editor.undo(false), true);
  assert.equal(editor.score.mix, undefined);
  // one step only: nothing older to go back to
  assert.equal(editor.undo(false), false);
  assert.equal(editor.undo(true), true);
  assert.equal(editor.score.mix.sounds.kick.gain_db, -4);
});

test("a rack edit lands at once wherever the pattern plays: no share question", async () => {
  const { editor } = await editorSetup();
  // pattern a plays in the verse and the hook
  const made = rackStep(editor.score, SPECS, "a", "kick", 1, "click", 0);
  assert.equal(editor.editPattern(made.score), true);
  assert.equal(hits(editor.score, "a", "kick"), "xx..x...x...x...x...x...x...x...");
  assert.deepEqual(Object.keys(editor.score.patterns), ["c", "a", "b", "d"]);
  assert.equal(editor.undo(false), true);
  assert.equal(hits(editor.score, "a", "kick"), "x...x...x...x...x...x...x...x...");
});

test("the editor keeps the instrument list the session came with", async () => {
  const { editor } = await editorSetup();
  assert.deepEqual(editor.instruments.map((i) => i.instrument), SPECS.map((i) => i.instrument));
  const draft = new BeatDraft({
    api: {
      draft: async () => ({ score: score(), base: "score-1.json" }),
      session: async () => ({ score_file: "score-1.json", score: score() }),
    },
    facts: () => ({ beatKind: "own", beatDirty: false, hasBeatAudio: true }),
    loaded() {},
    loadEnded() {},
    reload: async () => {},
    changed() {},
    say() {},
    timer: { set: () => 1, clear() {} },
  });
  assert.deepEqual(draft.instruments, []);
  draft.reset(3);
  await draft.load();
  // a session without the list leaves it empty rather than undefined
  assert.deepEqual(draft.instruments, []);
});

// ---------- the piano roll by sound ----------

test("the roll edits the note row chosen in the rack, else the block's 808 as before", () => {
  const s = score();
  assert.deepEqual(rollRowOf(s, 0, { pattern: "b", sound: "keys" }), { pattern: "b", sound: "keys", chosen: true });
  // chosen wins even over a block with an 808
  assert.deepEqual(rollRowOf(s, 1, { pattern: "b", sound: "keys" }), { pattern: "b", sound: "keys", chosen: true });
  assert.deepEqual(rollRowOf(s, 1, null), { pattern: "a", sound: "bass", chosen: false });
  // a choice that is no note row any more falls back
  assert.deepEqual(rollRowOf(s, 1, { pattern: "gone", sound: "keys" }), { pattern: "a", sound: "bass", chosen: false });
  assert.deepEqual(rollRowOf(s, 1, { pattern: "c", sound: "keys" }), { pattern: "a", sound: "bass", chosen: false });
  assert.deepEqual(rollRowOf(s, 1, { pattern: "a", sound: "kick" }), { pattern: "a", sound: "bass", chosen: false });
  assert.equal(rollRowOf(s, 0, null), null);
  assert.equal(rollRowOf(s, null, null), null);
});

test("bass instruments keep the 808 range; other note sounds get C3..C6 around their notes", () => {
  for (const bass of ["bass808", "reese", "sample808"]) {
    assert.deepEqual(rollRangeFor(bass, []), rollRange([]));
    assert.deepEqual(rollRangeFor(bass, [36, 40]), rollRange([36, 40]));
  }
  assert.deepEqual(rollRangeFor("pluck", []), { lo: 48, hi: 84 });
  assert.deepEqual(rollRangeFor("pluck", [60, 64]), { lo: 48, hi: 84 });
  assert.deepEqual(rollRangeFor("pluck", [40, 90]), { lo: 36, hi: 94 });
  assert.deepEqual(rollRangeFor("fm", [2]), { lo: 0, hi: 84 });
  assert.deepEqual(rollRangeFor("fm", [126]), { lo: 48, hi: 127 });
});

test("a chosen row's roll edits the whole pattern; the block's 808 still asks through the share gate", () => {
  let s = score();
  const calls = [];
  let chosen = { pattern: "b", sound: "keys" };
  const roll = scoreRoll({
    score: () => s,
    block: () => ({ section: 1, fromBar: 4, bars: 8, label: "verse" }),
    edit: (section, pattern, change) => {
      calls.push(["edit", section, pattern]);
      s = change(s, pattern);
    },
    notes: () => chosen,
    editPattern: (change) => {
      calls.push(["pattern"]);
      s = change(s);
    },
  });
  const data = roll.data();
  assert.equal(typeof data, "object");
  assert.deepEqual(data.notes.map((n) => n.midi), [60]);
  assert.equal(data.patternSteps, 16);
  assert.equal(data.viewBars, 1);
  assert.deepEqual(data.range, { lo: 48, hi: 84 });
  roll.add(8, 67, () => {});
  assert.deepEqual(calls, [["pattern"]]);
  assert.deepEqual(notesOf(s, "b", "keys").map((n) => [n.step, n.midi]), [[0, 60], [8, 67]]);
  chosen = null;
  const old = roll.data();
  assert.deepEqual(old.notes.map((n) => n.midi), [36]);
  roll.remove(0);
  assert.deepEqual(calls, [["pattern"], ["edit", 1, "a"]]);
  assert.deepEqual(notesOf(s, "a", "bass"), []);
});
