// The bottom editor for a flip block (notes, drum rows, block effects, «Собрать по тексту») and
// for an own beat's section ops through the editor's score history.
import assert from "node:assert/strict";
import { test } from "node:test";

import { moveSection, duplicateSection } from "../src/beat/model.ts";
import { flipDrums, setFlipDrum } from "../src/daw/drums.ts";
import { FlipDraft } from "../src/daw/flipdraft.ts";
import { addBlockEffect, blockEffects, proposeInto, removeEffect, REPLACE_ASK, replaceRisk } from "../src/daw/fx.ts";
import { FlipRoll, layerProblem, SourceNotes, flipRollNotes, addFlipNote, moveFlipNote } from "../src/daw/roll.ts";
import { shareGate } from "../src/daw/share.ts";
import { editorTabs } from "../src/daw/view.ts";

const settle = async () => {
  for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
};

const doc = (over = {}) => ({
  format: 1,
  source: "input/x.wav",
  grid: { bpm: 120, downbeat_s: 0, swing: 50 },
  bass808: { mode: "original", params: {} },
  hats: { mode: "original", remove: [], patterns: ["8ths", "8ths"] },
  gains: { drums: 0, other: 0, bass: 0 },
  arrangement: [
    { label: "a", from_bar: 2, to_bar: 4 },
    { label: "b", from_bar: 0, to_bar: 2 },
  ],
  effects: [],
  ...over,
});

function setup(d = doc(), { confirm = true } = {}) {
  const says = [];
  const confirms = [];
  const draft = new FlipDraft({
    api: { state: async () => ({}), saveDraft: async () => 4, render: async () => 1, use: async () => {}, rev: async () => 1, jobs: async () => [], beacon: () => {} },
    takes: () => 0,
    confirm: (t) => (confirms.push(t), confirm),
    recording: () => false,
    reload: async () => {},
    changed: () => {},
    say: (t) => says.push(t),
    timer: { set: () => 1, clear: () => {} },
    sleep: async () => {},
  });
  draft.adopt({ draft: d, draft_rev: 3 }, false);
  return { draft, says, confirms };
}

// source bars 0..3: the note of bar n is at step n, so the bar it came from can be told
const source = [0, 1, 2, 3].map((n) => [{ step: n, len: 4, note: 30 + n, vel: 100 }]);

function roll(draft, section = 0, src = source) {
  const r = new FlipRoll({ doc: () => draft.doc, apply: (op) => draft.apply(op), block: () => ({ section, fromBar: 0, bars: 2, label: "a" }), source: () => src });
  return r;
}

test("editorTabs: a flip's beat lane has notes, drums and effects", () => {
  assert.deepEqual(editorTabs("beat", "flip"), ["roll", "drums", "fx"]);
  assert.deepEqual(editorTabs("beat", "file"), ["fx"]);
});

test("a block without notes shows the source notes of its bars, steps from the block start", () => {
  const { draft } = setup();
  const data = roll(draft).data();
  // block a is source bars 2-3: bar 2's note at step 2, bar 3's at 16 + 3
  assert.deepEqual(data.notes.map((n) => [n.step, n.midi]), [[2, 32], [19, 33]]);
  assert.match(data.caption, /ноты исходника/);
  assert.equal(data.viewBars, 2);
  assert.equal(data.chip, undefined);
  // the doc is not touched by looking
  assert.equal(draft.doc.arrangement[0].notes, undefined);
});

test("the second block's notes come from its own source bars", () => {
  const { draft } = setup();
  assert.deepEqual(roll(draft, 1).data().notes.map((n) => [n.step, n.midi]), [[0, 30], [17, 31]]);
});

test("the first edit copies the source notes into the block and switches 808 to ours, with a chip", () => {
  const { draft } = setup();
  const r = roll(draft);
  let made = -1;
  r.add(5, 40, (i) => (made = i));
  const block = draft.doc.arrangement[0];
  assert.equal(draft.doc.bass808.mode, "ours");
  assert.deepEqual(block.notes.map((n) => [n.step, n.note]), [[2, 32], [19, 33], [5, 40]]);
  assert.equal(made, 2);
  // the other block still plays its source notes
  assert.equal(draft.doc.arrangement[1].notes, undefined);
  const data = r.data();
  assert.deepEqual(data.chip && [data.chip.text, data.chip.action], ["808: наш", "вернуть"]);
  assert.match(data.caption, /свои ноты/);
});

test("the chip takes the 808 mode back and leaves the notes", () => {
  const { draft } = setup(doc({ bass808: { mode: "off", params: {} } }));
  const r = roll(draft);
  r.add(5, 40, () => {});
  r.data().chip.run();
  assert.equal(draft.doc.bass808.mode, "off");
  assert.equal(draft.doc.arrangement[0].notes.length, 3);
  assert.equal(r.data().chip, undefined);
});

test("undo of the first edit returns the source notes and the old 808 mode together", () => {
  const { draft } = setup();
  const r = roll(draft);
  r.add(5, 40, () => {});
  assert.equal(draft.undo(false), true);
  assert.equal(draft.doc.bass808.mode, "original");
  assert.equal(draft.doc.arrangement[0].notes, undefined);
  assert.equal(r.data().chip, undefined);
});

test("no chip when 808 was ours already; later edits keep the notes", () => {
  const { draft } = setup(doc({ bass808: { mode: "ours", params: {} } }));
  const r = roll(draft);
  r.remove(0);
  assert.equal(r.data().chip, undefined);
  assert.deepEqual(draft.doc.arrangement[0].notes.map((n) => n.step), [19]);
  r.move(0, { step: 3, midi: 50 });
  r.resize(0, 99);
  const n = draft.doc.arrangement[0].notes[0];
  // a tail may ring into the next bar: bars*16 + 16 - step
  assert.deepEqual([n.step, n.note, n.len], [3, 50, 45]);
});

test("an empty list of notes is silence, not the source's", () => {
  const { draft } = setup(doc({ bass808: { mode: "ours", params: {} } }));
  const r = roll(draft);
  r.remove(0);
  r.remove(0);
  assert.deepEqual(draft.doc.arrangement[0].notes, []);
  assert.deepEqual(r.data().notes, []);
});

test("a bar takes no more than 64 notes; a note outside the block is refused", () => {
  const full = Array.from({ length: 64 }, () => ({ step: 1, len: 1, note: 30, vel: 100 }));
  assert.equal(typeof addFlipNote(full, 32, { step: 2, midi: 30 }), "string");
  assert.equal(typeof addFlipNote(full, 32, { step: 20, midi: 30 }), "object");
  assert.equal(typeof addFlipNote([], 32, { step: 32, midi: 30 }), "string");
  assert.equal(typeof moveFlipNote(full.concat([{ step: 17, len: 1, note: 30, vel: 1 }]), 32, 64, { step: 2, midi: 30 }), "string");
});

test("the source notes of the last step keep their tail inside the block's room", () => {
  const notes = flipRollNotes({ from_bar: 0, to_bar: 1 }, [[{ step: 15, len: 40, note: 30, vel: 90 }]]);
  assert.equal(notes[0].len, 17);
});

test("source notes wait for the server and are read once per grid", async () => {
  let reads = 0;
  let changed = 0;
  const s = new SourceNotes({ sourceNotes: async () => (reads++, source) }, async () => {}, () => changed++);
  const d = doc();
  assert.equal(s.get(d), "loading");
  assert.equal(s.get(d), "loading");
  await settle();
  assert.equal(s.get(d), source);
  assert.equal(reads, 1);
  assert.equal(changed, 1);
  assert.equal(s.get({ ...d, grid: { ...d.grid, bpm: 90 } }), "loading");
  await settle();
  assert.equal(reads, 2);
});

test("a source that could not be read says so and is read again on retry", async () => {
  let fail = true;
  const s = new SourceNotes({ sourceNotes: async () => { if (fail) throw new Error("нет"); return source; } }, async () => {}, () => {});
  s.get(doc());
  await settle();
  assert.deepEqual(s.get(doc()), { error: "нет" });
  fail = false;
  s.retry();
  s.get(doc());
  await settle();
  assert.equal(s.get(doc()), source);
});

test("a roll over a block with no notes waits while the source loads", () => {
  const { draft } = setup();
  assert.equal(roll(draft, 0, "loading").data(), "Загружаю ноты исходника…");
  roll(draft, 0, "loading").add(1, 30, () => {});
  assert.equal(draft.doc.arrangement[0].notes, undefined);
});

// ---------- drums ----------

test("a drum step writes x into the row string of the block, bar by bar", () => {
  const { draft } = setup();
  const g = flipDrums({ doc: () => draft.doc, apply: (op) => draft.apply(op), block: () => ({ section: 0, fromBar: 0, bars: 2, label: "a" }) });
  assert.equal(typeof g.data(), "object");
  const rows = g.rows(1);
  assert.deepEqual(rows.map((r) => r.label).length, 6);
  // empty by default
  assert.ok(rows.every((r) => r.hits.every((x) => x === ".")));
  rows[0].press(4, 0);
  assert.equal(draft.doc.arrangement[0].drums.kick, `${".".repeat(16)}....x...........`);
  assert.equal(g.rows(1)[0].hits[4], "x");
  // the same step again takes it off, and the block ends with no drums at all
  g.rows(1)[0].press(4, 0);
  assert.equal(draft.doc.arrangement[0].drums, undefined);
});

test("the six rows keep their own strings; a row left empty goes", () => {
  let d = doc();
  d = setFlipDrum(d, 1, "hat", 0, 0, true);
  d = setFlipDrum(d, 1, "perc", 1, 15, true);
  assert.deepEqual(d.arrangement[1].drums, { hat: `x${".".repeat(31)}`, perc: `${".".repeat(31)}x` });
  d = setFlipDrum(d, 1, "hat", 0, 0, false);
  assert.deepEqual(Object.keys(d.arrangement[1].drums), ["perc"]);
  assert.equal(setFlipDrum(d, 1, "cowbell", 0, 0, true).ok, false);
  assert.equal(setFlipDrum(d, 1, "kick", 2, 0, true).ok, false);
  assert.equal(setFlipDrum(d, 1, "kick", 0, 16, true).ok, false);
  assert.equal(setFlipDrum(d, 9, "kick", 0, 0, true).ok, false);
});

// ---------- the tempo ----------

test("at a tempo the synth refuses, the notes and drums say why and write nothing", () => {
  const { draft } = setup(doc({ grid: { bpm: 250, downbeat_s: 0, swing: 50 } }));
  assert.match(layerProblem(draft.doc), /40–220 BPM, а в сетке 250/);
  const r = roll(draft);
  assert.match(r.data(), /250/);
  const g = flipDrums({ doc: () => draft.doc, apply: (op) => draft.apply(op), block: () => ({ section: 0, fromBar: 0, bars: 2, label: "a" }) });
  assert.match(g.data(), /250/);
  assert.equal(layerProblem(doc()), null);
  // the borders
  assert.equal(layerProblem(doc({ grid: { bpm: 220, downbeat_s: 0, swing: 50 } })), null);
  assert.notEqual(layerProblem(doc({ grid: { bpm: 39, downbeat_s: 0, swing: 50 } })), null);
});

// ---------- block effects ----------

test("an effect on a block covers the block's play-order bars; one chip each; × removes", () => {
  let d = doc();
  d = addBlockEffect(d, 1, "reverse");
  // block b plays after block a's two bars
  assert.deepEqual(d.effects, [{ effect: "reverse", from_bar: 2, to_bar: 4, params: {} }]);
  d = addBlockEffect(d, 1, "stutter");
  assert.equal(blockEffects(d, 1).length, 2);
  assert.equal(blockEffects(d, 0).length, 0);
  d = removeEffect(d, blockEffects(d, 1)[0].index);
  assert.deepEqual(d.effects.map((e) => e.effect), ["stutter"]);
  assert.equal(removeEffect(d, 5).ok, false);
  assert.equal(addBlockEffect(d, 7, "reverse").ok, false);
});

test("a tape effect on a block too short for the default duration is capped", () => {
  const d = doc({ grid: { bpm: 600, downbeat_s: 0, swing: 50 }, arrangement: [{ label: "a", from_bar: 0, to_bar: 1 }], hats: { mode: "original", remove: [], patterns: [] } });
  const out = addBlockEffect(d, 0, "tape_stop");
  assert.equal(out.effects[0].params.duration, 0.4);
  assert.equal(addBlockEffect(doc(), 0, "tape_stop").effects[0].params.duration, undefined);
});

test("block effects go through the draft's one history", () => {
  const { draft } = setup();
  draft.apply((d) => addBlockEffect(d, 0, "granular"));
  assert.equal(draft.doc.effects.length, 1);
  draft.undo(false);
  assert.equal(draft.doc.effects.length, 0);
});

// ---------- «Собрать по тексту» ----------

const proposal = [{ label: "intro", from_bar: 0, to_bar: 4 }, { label: "hook", from_bar: 4, to_bar: 8 }];

test("a proposal replaces the arrangement; a plain doc is not asked about", async () => {
  const { draft, confirms } = setup();
  const done = await proposeInto(draft, { propose: async () => proposal }, () => (confirms.push("asked"), true), () => {});
  assert.equal(done, true);
  assert.deepEqual(confirms, []);
  assert.deepEqual(draft.doc.arrangement, proposal);
  assert.deepEqual(draft.doc.hats.patterns, ["8ths", "8ths"]);
  assert.equal(draft.undo(false), true);
  assert.equal(draft.doc.arrangement[0].label, "a");
});

test("blocks with notes, drums or effects are asked about; no keeps them", async () => {
  const worked = doc();
  worked.arrangement[1].drums = { kick: "x".padEnd(32, ".") };
  assert.equal(replaceRisk(worked), true);
  assert.equal(replaceRisk(doc({ effects: [{ effect: "reverse", from_bar: 0, to_bar: 1, params: {} }] })), true);
  assert.equal(replaceRisk(doc()), false);
  const asked = [];
  const { draft } = setup(worked);
  assert.equal(await proposeInto(draft, { propose: async () => proposal }, (t) => (asked.push(t), false), () => {}), false);
  assert.deepEqual(asked, [REPLACE_ASK]);
  assert.equal(draft.doc.arrangement.length, 2);
  assert.equal(await proposeInto(draft, { propose: async () => proposal }, () => true, () => {}), true);
  assert.equal(draft.doc.arrangement.length, 2);
  assert.equal(draft.doc.arrangement[0].label, "intro");
  assert.equal(draft.doc.arrangement[0].drums, undefined);
  draft.undo(false);
  assert.deepEqual(draft.doc.arrangement[1].drums, worked.arrangement[1].drums);
});

test("an empty proposal says so and changes nothing; a failure says why", async () => {
  const { draft, says } = setup();
  assert.equal(await proposeInto(draft, { propose: async () => [] }, () => true, (t) => says.push(t)), false);
  assert.match(says.at(-1), /ни одного блока/);
  assert.equal(draft.canUndo, false);
  assert.equal(await proposeInto(draft, { propose: async () => { throw new Error("сломалось"); } }, () => true, (t) => says.push(t)), false);
  assert.match(says.at(-1), /сломалось/);
});

// ---------- an own beat's section ops through the editor ----------

const score = () => ({
  version: 1,
  bpm: 140,
  seed: 3,
  key: "C minor",
  sounds: { kick: { instrument: "kick" } },
  patterns: { a: { bars: 1, rows: { kick: "x..............." } }, b: { bars: 1, rows: { kick: "....x..........." } } },
  sections: [
    { name: "intro", bars: 4, play: ["b"] },
    { name: "verse", bars: 8, play: ["a"] },
    { name: "hook", bars: 8, play: ["a"] },
  ],
});

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

async function editorSetup({ load = true, over = {}, flipDoc } = {}) {
  fakeDom();
  const { Editor } = await import("../src/daw/editor.ts");
  const base = "score-1.json";
  const changes = [];
  const beatApi = {
    draft: async () => ({ score: score(), base }),
    session: async () => ({ score_file: base, score: score() }),
    saveDraft: async () => {},
    save: async () => ({ file: "score-2.json" }),
    render: async () => ({ state: "running", score_file: null, error: null }),
    renderStatus: async () => ({ state: "idle", score_file: null, error: null }),
  };
  const { draft } = setup(flipDoc);
  const editor = new Editor({
    base: undefined,
    token: "t",
    api: {},
    beatApi,
    flipApi: {},
    flipDraft: draft,
    confirm: () => true,
    reply: () => ({ stations: { beat: { kind: "own", seed: 3 }, text: { seed: null } } }),
    facts: () => ({ beatKind: "own", beatDirty: false, hasBeatAudio: true }),
    jobs: () => [],
    flip: () => null,
    clips: () => [],
    playheadBar: () => 0,
    playback: () => null,
    studio: () => null,
    stopSong() {},
    startJob: async () => {},
    change: async () => true,
    reload: async () => {},
    beatRendered: async () => {},
    layout: () => ({ startBar: 0, barsPerLine: 1 }),
    nudge() {},
    scoreChanged: (s) => changes.push(s),
    beatState() {},
    repaint() {},
    say() {},
    overlay: {},
    ...over,
  });
  if (load) {
    editor.prepare();
    await settle();
  }
  return { editor, changes, draft };
}

test("a section op goes through the editor's draft; undo and redo work with no tab open", async () => {
  const { editor, changes } = await editorSetup();
  const names = () => editor.score.sections.map((s) => s.name);
  assert.deepEqual(names(), ["intro", "verse", "hook"]);
  const moved = moveSection(editor.score, 0, 2);
  assert.equal(moved.ok, true);
  assert.equal(editor.editScore(moved.score), true);
  assert.deepEqual(names(), ["verse", "hook", "intro"]);
  assert.equal(editor.beatDirty, true);
  assert.equal(changes.length, 1);
  assert.equal(editor.undo(false), true);
  assert.deepEqual(names(), ["intro", "verse", "hook"]);
  assert.equal(editor.undo(true), true);
  assert.deepEqual(names(), ["verse", "hook", "intro"]);
  // nothing further to redo: the editor leaves it to the screen's own history
  assert.equal(editor.undo(true), false);
  editor.mount({ replaceChildren() {} }, "beat", "fx", ["roll", "drums", "fx"], false);
  assert.equal(editor.undo(false), true);
  assert.deepEqual(names(), ["intro", "verse", "hook"]);
});

test("a copied section plays the same patterns, so editing one asks «во всех N местах»", async () => {
  const { editor } = await editorSetup();
  const dup = duplicateSection(editor.score, 1);
  assert.equal(editor.editScore(dup.score), true);
  assert.deepEqual(editor.score.sections.map((s) => s.play[0]), ["b", "a", "a", "a"]);
  assert.deepEqual(shareGate(editor.score, 1, "a", new Set()), { kind: "ask", count: 3 });
  assert.equal(editor.undo(false), true);
  assert.equal(editor.score.sections.length, 3);
});

test("a score that is not loaded takes no edit", async () => {
  const { editor } = await editorSetup({ load: false });
  assert.equal(editor.score, null);
  assert.equal(editor.editScore(score()), false);
  assert.equal(editor.undo(false), false);
});

test("an audition asks for the layer again only when the edited block's content changed", async () => {
  let asked = 0;
  let swapped = 0;
  const playback = { audition: async () => {}, updateAudition: () => void swapped++, stopAudition: async () => {} };
  const { editor, draft } = await editorSetup({
    load: false,
    over: {
      facts: () => ({ beatKind: "flip", beatDirty: false, hasBeatAudio: true }),
      flipApi: { blockScore: async () => (asked++, score()) },
      clips: () => [
        { index: 0, fromBar: 0, bars: 2, label: "a" },
        { index: 1, fromBar: 2, bars: 2, label: "b" },
      ],
      playback: () => playback,
    },
  });
  editor.mount({ replaceChildren() {} }, "beat", "roll", ["roll", "drums", "fx"], true);
  await editor.audition.handlers.click();
  await settle();
  assert.equal(asked, 1);
  const label = (at, text) => draft.apply((d) => ({ ...d, arrangement: d.arrangement.map((b, i) => (i === at ? { ...b, label: text } : b)) }));
  // the other block, and a change that is no edit at all
  label(1, "z");
  editor.flipEdited();
  editor.flipEdited();
  await settle();
  assert.equal(asked, 1);
  assert.equal(swapped, 0);
  // the audited block
  label(0, "y");
  editor.flipEdited();
  await settle();
  assert.equal(asked, 2);
  assert.equal(swapped, 1);
  // the same content again is not asked for twice
  editor.flipEdited();
  await settle();
  assert.equal(asked, 2);
});

test("the editor's own block is the one it was pointed at while it exists, never the playhead's", async () => {
  const clips = [
    { index: 0, fromBar: 0, bars: 4, label: "a" },
    { index: 1, fromBar: 4, bars: 4, label: "b" },
  ];
  const { editor } = await editorSetup({ load: false, over: { clips: () => clips, playheadBar: () => 5 } });
  assert.equal(editor.ownBlock(), null);
  editor.selectBlock(1);
  assert.equal(editor.ownBlock(), 1);
  editor.selectBlock(2);
  assert.equal(editor.ownBlock(), null);
  assert.equal(editor.block()?.section, 1);
});
