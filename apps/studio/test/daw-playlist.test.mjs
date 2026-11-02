// The playlist of an own beat: one lane per pattern with a clip in each section that plays it,
// painting and erasing clips, the clip-before-section Delete, the cut of a section, the marks drawn
// in a clip and the rack's pager following the playing bar.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_PATTERNS, placePattern } from "../src/beat/model.ts";
import {
  clipAlive,
  clipMarks,
  deleteTarget,
  dragSections,
  PagerFollow,
  paintSections,
  patternClips,
  patternColour,
  PATTERN_COLOURS,
  sectionAtBar,
  sectionCutAt,
  uniqueClip,
} from "../src/daw/playlist.ts";
import { dawKey, editorTabs, keyedBlock, visibleLanes } from "../src/daw/view.ts";

const settle = async () => {
  for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
};

const spec = (instrument, kind) => ({ instrument, kind, mono: false, max_voices: 8, max_life_s: 2, params: [] });
const SPECS = [spec("kick", "drum"), spec("snare", "drum"), spec("hat_closed", "drum"), spec("oneshot", "drum"), spec("bass808", "note"), spec("pluck", "note")];

// intro bars 0-1 plays d; verse bars 2-5 plays a and d with the fill f; hook bars 6-9 plays a.
// b (a kick) and k (keys, four bars) are not played.
const score = () => ({
  version: 1,
  bpm: 140,
  seed: 3,
  key: "C minor",
  sounds: {
    kick: { instrument: "kick" },
    snare: { instrument: "snare" },
    hat: { instrument: "hat_closed" },
    perc: { instrument: "oneshot" },
    bass: { instrument: "bass808" },
    keys: { instrument: "pluck" },
  },
  patterns: {
    a: {
      bars: 2,
      rows: {
        kick: "x...............|........x.......",
        bass: [
          { step: 0, len: 4, note: "C2", vel: 0.8 },
          { step: 20, len: 2, note: "D#2", vel: 0.8 },
        ],
      },
    },
    b: { bars: 1, rows: { kick: "x..............." } },
    d: { bars: 1, rows: { hat: "x.......x.......", perc: "....x..........." } },
    f: { bars: 1, rows: { snare: "............x..." } },
    k: { bars: 4, rows: { keys: [{ step: 0, len: 8, note: "G4", vel: 0.8 }] } },
  },
  sections: [
    { name: "intro", bars: 2, play: ["d"] },
    { name: "verse", bars: 4, play: ["a", "d"], fill: "f" },
    { name: "hook", bars: 4, play: ["a"] },
  ],
});

// ---------- lanes ----------

test("an own beat shows the beat lane and then the pattern lanes; flips and files keep theirs", () => {
  const facts = (o) => ({ beatKind: "own", beatAnalysed: true, voiceKind: "own", ...o });
  assert.deepEqual(visibleLanes(facts({})).lanes, ["text", "voice", "takes", "beat", "patterns"]);
  assert.deepEqual(visibleLanes(facts({ voiceKind: "suno" })).lanes, ["text", "voice", "beat", "patterns"]);
  assert.deepEqual(visibleLanes(facts({ beatKind: "flip" })).lanes, ["text", "voice", "takes", "beat"]);
  assert.deepEqual(visibleLanes(facts({ beatKind: "file" })).lanes, ["text", "voice", "takes", "beat"]);
});

test("the pattern lanes open the rack, the roll and the effects", () => {
  assert.deepEqual(editorTabs("patterns", "own"), ["rack", "roll", "fx"]);
});

test("each pattern gets a colour from the palette by its place, wrapping round", () => {
  assert.equal(PATTERN_COLOURS.length, 8);
  assert.equal(new Set(PATTERN_COLOURS).size, 8);
  for (const c of PATTERN_COLOURS) assert.match(c, /^--c-/);
  assert.equal(patternColour(0), PATTERN_COLOURS[0]);
  assert.equal(patternColour(7), PATTERN_COLOURS[7]);
  assert.equal(patternColour(8), PATTERN_COLOURS[0]);
  assert.equal(patternColour(9), PATTERN_COLOURS[1]);
  assert.equal(patternColour(-1), PATTERN_COLOURS[0]);
  assert.equal(patternColour(Number.NaN), PATTERN_COLOURS[0]);
});

// ---------- clips ----------

test("a pattern's clips span the sections that play it; a fill is a one-bar mark on the section's last bar", () => {
  const s = score();
  assert.deepEqual(patternClips(s, "a"), [
    { section: 1, fromBar: 2, bars: 4, fill: false },
    { section: 2, fromBar: 6, bars: 4, fill: false },
  ]);
  assert.deepEqual(patternClips(s, "d"), [
    { section: 0, fromBar: 0, bars: 2, fill: false },
    { section: 1, fromBar: 2, bars: 4, fill: false },
  ]);
  assert.deepEqual(patternClips(s, "f"), [{ section: 1, fromBar: 5, bars: 1, fill: true }]);
  assert.deepEqual(patternClips(s, "k"), []);
  assert.deepEqual(patternClips(s, "gone"), []);
});

test("a pattern played and used as the fill of the same section has both, the clip first", () => {
  const s = score();
  s.sections[0].fill = "d";
  assert.deepEqual(patternClips(s, "d"), [
    { section: 0, fromBar: 0, bars: 2, fill: false },
    { section: 0, fromBar: 1, bars: 1, fill: true },
    { section: 1, fromBar: 2, bars: 4, fill: false },
  ]);
});

test("the section under a bar, none before the song or past it", () => {
  const s = score();
  assert.equal(sectionAtBar(s, 0), 0);
  assert.equal(sectionAtBar(s, 1.99), 0);
  assert.equal(sectionAtBar(s, 2), 1);
  assert.equal(sectionAtBar(s, 9.99), 2);
  assert.equal(sectionAtBar(s, 10), null);
  assert.equal(sectionAtBar(s, -0.1), null);
  assert.equal(sectionAtBar(s, Number.NaN), null);
});

test("a drag covers every section from where it went down to the one under the pointer, the ends clamped", () => {
  const s = score();
  assert.deepEqual(dragSections(s, 0, 7.5), [0, 1, 2]);
  assert.deepEqual(dragSections(s, 2, 0.5), [0, 1, 2]);
  assert.deepEqual(dragSections(s, 1, 3), [1]);
  assert.deepEqual(dragSections(s, 1, 99), [1, 2]);
  assert.deepEqual(dragSections(s, 1, -5), [0, 1]);
  assert.deepEqual(dragSections(s, 0, 0), [0]);
});

// ---------- painting ----------

test("painting over sections makes one score; a refused section is left as it was and its reason kept", () => {
  const s = score();
  const out = paintSections(s, "k", [0, 1, 2]);
  assert.deepEqual(out.painted, [1, 2]);
  assert.deepEqual(out.refused, [{ section: 0, reason: "«Паттерн 5» длиной 4 такта не ложится в секцию intro длиной 2 такта." }]);
  assert.deepEqual(out.score.sections.map((x) => x.play), [["d"], ["a", "d", "k"], ["a", "k"]]);
  // the fill stays
  assert.equal(out.score.sections[1].fill, "f");
  // the score given is left alone
  assert.deepEqual(s, score());
});

test("a section whose sound another pattern already plays refuses; the rest are painted", () => {
  const out = paintSections(score(), "b", [0, 1, 2]);
  assert.deepEqual(out.painted, [0]);
  assert.deepEqual(out.refused, [
    { section: 1, reason: "В секции verse звук kick уже играет «Паттерн 1 · verse»." },
    { section: 2, reason: "В секции hook звук kick уже играет «Паттерн 1 · verse»." },
  ]);
  assert.deepEqual(out.score.sections.map((x) => x.play), [["d", "b"], ["a", "d"], ["a"]]);
});

test("sections that already play the pattern are skipped, not painted and not refused", () => {
  const s = score();
  const some = paintSections(s, "d", [0, 1, 2]);
  assert.deepEqual(some.painted, [2]);
  assert.deepEqual(some.refused, []);
  assert.deepEqual(some.score.sections[2].play, ["a", "d"]);
  const none = paintSections(s, "a", [1, 2]);
  assert.deepEqual(none.painted, []);
  assert.deepEqual(none.refused, []);
  // nothing painted: the same score, so no undo step is made of it
  assert.equal(none.score, s);
});

test("all refused paints nothing and keeps the score; repeated and unordered sections count once in order", () => {
  const s = score();
  const all = paintSections(s, "k", [0]);
  assert.deepEqual(all.painted, []);
  assert.equal(all.refused.length, 1);
  assert.equal(all.score, s);
  const twice = paintSections(s, "k", [2, 2, 1]);
  assert.deepEqual(twice.painted, [1, 2]);
  assert.deepEqual(twice.score.sections[2].play, ["a", "k"]);
});

test("a section that is not there, or a pattern that is not, is refused", () => {
  const s = score();
  const far = paintSections(s, "k", [1, 3]);
  assert.deepEqual(far.painted, [1]);
  assert.deepEqual(far.refused, [{ section: 3, reason: "Такой секции нет." }]);
  const gone = paintSections(s, "gone", [0]);
  assert.deepEqual(gone.painted, []);
  assert.equal(gone.refused.length, 1);
  assert.equal(gone.refused[0].section, 0);
  assert.equal(gone.score, s);
});

test("erasing takes the pattern out of play only where it plays; a fill is left", () => {
  const s = score();
  const off = paintSections(s, "a", [1], false);
  assert.deepEqual(off.painted, [1]);
  assert.deepEqual(off.score.sections[1], { name: "verse", bars: 4, play: ["d"], fill: "f" });
  assert.deepEqual(off.score.sections[2].play, ["a"]);
  const elsewhere = paintSections(s, "a", [0], false);
  assert.deepEqual(elsewhere.painted, []);
  assert.equal(elsewhere.score, s);
  const fill = paintSections(s, "f", [1], false);
  assert.deepEqual(fill.painted, []);
  assert.equal(fill.score, s);
});

// ---------- a clip picked ----------

test("a picked clip is alive while its section plays its pattern", () => {
  const s = score();
  assert.equal(clipAlive(s, { pattern: "a", section: 1 }), true);
  assert.equal(clipAlive(s, { pattern: "a", section: 2 }), true);
  assert.equal(clipAlive(s, { pattern: "a", section: 0 }), false);
  // a fill is not a clip
  assert.equal(clipAlive(s, { pattern: "f", section: 1 }), false);
  assert.equal(clipAlive(s, { pattern: "a", section: 3 }), false);
  assert.equal(clipAlive(s, { pattern: "gone", section: 1 }), false);
});

test("Delete takes the picked clip before the picked section and never the section while a clip is picked", () => {
  const s = score();
  const clip = { pattern: "a", section: 1 };
  assert.deepEqual(deleteTarget(clip, s, "patterns", null, 3), { kind: "clip", pattern: "a", section: 1 });
  // a section picked on the beat lane too: the clip still wins
  assert.deepEqual(deleteTarget(clip, s, "beat", 2, 3), { kind: "clip", pattern: "a", section: 1 });
  // a clip that is gone deletes nothing, not the section picked
  assert.equal(deleteTarget({ pattern: "a", section: 0 }, s, "beat", 0, 3), null);
  assert.equal(deleteTarget(clip, null, "beat", 1, 3), null);
  // no clip: the section picked on the beat lane, as before
  assert.deepEqual(deleteTarget(null, s, "beat", 1, 3), { kind: "section", index: 1 });
  assert.deepEqual(deleteTarget(null, s, "beat", 0, 3), { kind: "section", index: 0 });
  assert.deepEqual(deleteTarget(null, s, "beat", 2, 3), { kind: "section", index: 2 });
  assert.equal(deleteTarget(null, s, "beat", 3, 3), null);
  assert.equal(deleteTarget(null, s, "beat", null, 3), null);
  // the pattern lanes never delete a section
  assert.equal(deleteTarget(null, s, "patterns", 1, 3), null);
  assert.equal(deleteTarget(null, s, "voice", 1, 3), null);
});

test("the section keys stay with the beat lane: the pattern lanes have none", () => {
  assert.equal(keyedBlock("patterns", 1, 3), null);
  assert.equal(keyedBlock("beat", 1, 3), 1);
});

test("Escape is the deselect key, unless the focus keeps its keys", () => {
  const key = (code, o = {}) => ({ code, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });
  assert.equal(dawKey(key("Escape"), false), "deselect");
  assert.equal(dawKey(key("Escape"), true), null);
  assert.equal(dawKey(key("Delete"), false), "delete");
});

test("a shared clip made unique gets a copy of the pattern in its section alone", () => {
  const s = score();
  const out = uniqueClip(s, { pattern: "a", section: 2 });
  assert.equal(out.ok, true);
  assert.equal(out.name, "a_2");
  assert.deepEqual(out.score.patterns.a_2, s.patterns.a);
  assert.deepEqual(out.score.sections.map((x) => x.play), [["d"], ["a", "d"], ["a_2"]]);
  assert.deepEqual(s, score());
});

test("a pattern played once and used as a fill elsewhere is shared too", () => {
  const s = score();
  s.sections = [
    { name: "intro", bars: 2, play: ["b"] },
    { name: "verse", bars: 4, play: ["a"], fill: "d" },
    { name: "hook", bars: 4, play: ["a", "d"] },
  ];
  const out = uniqueClip(s, { pattern: "d", section: 2 });
  assert.equal(out.ok, true);
  assert.deepEqual(out.score.sections[2].play, ["a", "d_2"]);
  assert.equal(out.score.sections[1].fill, "d");
});

test("a clip played only there, gone, or with the patterns full is not made unique", () => {
  const s = placePattern(score(), "k", 2, true).score;
  assert.deepEqual(uniqueClip(s, { pattern: "k", section: 2 }), { ok: false, reason: "Паттерн играет только здесь: он и так свой." });
  assert.deepEqual(uniqueClip(score(), { pattern: "a", section: 0 }), { ok: false, reason: "Этого клипа уже нет." });
  const full = score();
  for (let i = Object.keys(full.patterns).length; i < MAX_PATTERNS; i++) full.patterns[`x_${i}`] = { bars: 1, rows: {} };
  assert.equal(Object.keys(full.patterns).length, MAX_PATTERNS);
  assert.deepEqual(uniqueClip(full, { pattern: "a", section: 2 }), { ok: false, reason: `Паттернов уже ${MAX_PATTERNS}, больше нельзя.` });
});

// ---------- the cut ----------

test("a section is cut at the bar line nearest the pointer inside it, else in the middle", () => {
  assert.equal(sectionCutAt(2, 4, 3.4), 1);
  assert.equal(sectionCutAt(2, 4, 4.6), 3);
  assert.equal(sectionCutAt(2, 4, 4.5), 3);
  // next to an edge: the first or the last bar line inside
  assert.equal(sectionCutAt(2, 4, 2.1), 1);
  assert.equal(sectionCutAt(2, 4, 5.9), 3);
  // no pointer, or one outside the section
  assert.equal(sectionCutAt(2, 4, null), 2);
  assert.equal(sectionCutAt(2, 4, 7), 2);
  assert.equal(sectionCutAt(2, 4, 2), 2);
  assert.equal(sectionCutAt(2, 8, null), 4);
  assert.equal(sectionCutAt(2, 5, null), 2);
  // one bar has no inside: the cut the model refuses
  assert.equal(sectionCutAt(0, 1, 0.5), 1);
});

// ---------- the marks in a clip ----------

test("a clip shows only its pattern's notes and hits, on play-order bars", () => {
  const { notes, hits } = clipMarks(score(), "a");
  assert.deepEqual(notes, [
    { bar: 2, step: 0, len: 4, midi: 36 },
    { bar: 3, step: 4, len: 2, midi: 39 },
    { bar: 4, step: 0, len: 4, midi: 36 },
    { bar: 5, step: 4, len: 2, midi: 39 },
    { bar: 6, step: 0, len: 4, midi: 36 },
    { bar: 7, step: 4, len: 2, midi: 39 },
    { bar: 8, step: 0, len: 4, midi: 36 },
    { bar: 9, step: 4, len: 2, midi: 39 },
  ]);
  assert.deepEqual(hits, [
    { bar: 2, step: 0, row: 0 },
    { bar: 3, step: 8, row: 0 },
    { bar: 4, step: 0, row: 0 },
    { bar: 5, step: 8, row: 0 },
    { bar: 6, step: 0, row: 0 },
    { bar: 7, step: 8, row: 0 },
    { bar: 8, step: 0, row: 0 },
    { bar: 9, step: 8, row: 0 },
  ]);
});

test("any note sound draws notes and any drum sound draws hits; an unknown drum sits on the middle row", () => {
  const keys = clipMarks(placePattern(score(), "k", 2, true).score, "k");
  assert.deepEqual(keys.notes, [{ bar: 6, step: 0, len: 8, midi: 67 }]);
  assert.deepEqual(keys.hits, []);
  const d = clipMarks(score(), "d");
  assert.deepEqual(d.notes, []);
  const at = (bar) => d.hits.filter((x) => x.bar === bar).map((x) => [x.row, x.step]);
  assert.deepEqual(at(0), [[1, 4], [2, 0], [2, 8]]);
  assert.deepEqual(at(5), [[1, 4], [2, 0], [2, 8]]);
  assert.deepEqual(at(6), []);
});

test("a muted sound and a row the fill replaces are not drawn; a fill-only pattern has no clip marks", () => {
  const muted = score();
  muted.sections[1].mute = ["kick"];
  assert.deepEqual(clipMarks(muted, "a").hits.map((x) => x.bar), [6, 7, 8, 9]);
  const replaced = score();
  replaced.patterns.f.rows.kick = "x...............";
  assert.deepEqual(clipMarks(replaced, "a").hits.map((x) => x.bar), [2, 3, 4, 6, 7, 8, 9]);
  assert.deepEqual(clipMarks(score(), "f"), { notes: [], hits: [] });
  assert.deepEqual(clipMarks(score(), "gone"), { notes: [], hits: [] });
});

// ---------- the pager follows the playing bar ----------

test("the pager follows the playing bar from each play until a turn by hand", () => {
  const p = new PagerFollow();
  assert.equal(p.next(false, null), null);
  assert.equal(p.next(true, { bar: 0, step: 3 }), 0);
  assert.equal(p.next(true, { bar: 1, step: 0 }), 1);
  // the song is outside the pattern: the page stays
  assert.equal(p.next(true, null), null);
  p.turned();
  assert.equal(p.next(true, { bar: 0, step: 0 }), null);
  assert.equal(p.next(true, { bar: 1, step: 0 }), null);
  // a stop and the next play hand it back
  assert.equal(p.next(false, null), null);
  assert.equal(p.next(true, { bar: 1, step: 4 }), 1);
});

test("a turn while stopped does not hold the pager at the next play; stopped, nothing follows", () => {
  const p = new PagerFollow();
  p.turned();
  assert.equal(p.next(false, { bar: 1, step: 0 }), null);
  assert.equal(p.next(true, { bar: 1, step: 0 }), 1);
});

// ---------- the editor ----------

function fakeDom() {
  const el = () => ({
    children: [],
    style: { setProperty() {} },
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    append(...n) {
      this.children.push(...n);
    },
    replaceChildren(...n) {
      this.children = n;
    },
    setAttribute() {},
    addEventListener() {},
    removeEventListener() {},
    querySelectorAll: () => [],
    focus() {},
  });
  globalThis.document = { createElement: el, createElementNS: el };
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
}

async function editorSetup() {
  fakeDom();
  const { Editor } = await import("../src/daw/editor.ts");
  const base = "score-1.json";
  const changes = [];
  const said = [];
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
      { index: 0, fromBar: 0, bars: 2, label: "intro" },
      { index: 1, fromBar: 2, bars: 4, label: "verse" },
      { index: 2, fromBar: 6, bars: 4, label: "hook" },
    ],
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
    openTab() {},
    beatState() {},
    repaint() {},
    say: (text) => said.push(text),
    overlay: {},
  });
  editor.prepare();
  await settle();
  return { editor, changes, said };
}

test("a paint over several sections is one undo step; the refusals are said", async () => {
  const { editor, changes, said } = await editorSetup();
  const out = editor.paintPattern("k", [0, 1, 2]);
  assert.deepEqual(out.painted, [1, 2]);
  assert.deepEqual(editor.score.sections.map((x) => x.play), [["d"], ["a", "d", "k"], ["a", "k"]]);
  assert.equal(changes.length, 1);
  assert.deepEqual(said, ["«Паттерн 5» длиной 4 такта не ложится в секцию intro длиной 2 такта."]);
  assert.equal(editor.undo(false), true);
  assert.deepEqual(editor.score.sections.map((x) => x.play), [["d"], ["a", "d"], ["a"]]);
  assert.equal(editor.undo(false), false);
});

test("a paint with nothing to paint makes no undo step", async () => {
  const { editor, changes, said } = await editorSetup();
  const before = editor.score;
  const out = editor.paintPattern("k", [0]);
  assert.deepEqual(out.painted, []);
  assert.equal(editor.score, before);
  assert.equal(changes.length, 0);
  assert.equal(said.length, 1);
  assert.equal(editor.undo(false), false);
  assert.deepEqual(editor.paintPattern("a", [1, 2]).painted, []);
  assert.equal(changes.length, 0);
});

test("a clip taken out leaves its section's other patterns and is one undo step", async () => {
  const { editor, changes } = await editorSetup();
  assert.equal(editor.clearClip("a", 1), true);
  assert.deepEqual(editor.score.sections.map((x) => x.play), [["d"], ["d"], ["a"]]);
  assert.equal(editor.score.sections[1].fill, "f");
  assert.equal(changes.length, 1);
  // a section that does not play it: nothing happens
  assert.equal(editor.clearClip("a", 0), false);
  assert.equal(changes.length, 1);
  assert.equal(editor.undo(false), true);
  assert.deepEqual(editor.score.sections[1].play, ["a", "d"]);
});

test("a clip picked points the rack at its pattern and the editor at its section", async () => {
  const { editor } = await editorSetup();
  editor.selectClip("d", 1);
  assert.equal(editor.rackPattern, "d");
  assert.equal(editor.block().section, 1);
  editor.selectClip("a", 2);
  assert.equal(editor.rackPattern, "a");
  assert.equal(editor.block().section, 2);
  // a block picked on the beat lane gives the rack back to the block
  editor.selectBlock(0);
  assert.equal(editor.rackPattern, "d");
});

test("a clip made unique is one undo step and the rack shows the new pattern", async () => {
  const { editor, changes, said } = await editorSetup();
  editor.selectClip("a", 2);
  assert.equal(editor.uniqueClip("a", 2), "a_2");
  assert.deepEqual(editor.score.sections[2].play, ["a_2"]);
  assert.equal(editor.rackPattern, "a_2");
  assert.equal(changes.length, 1);
  assert.equal(editor.uniqueClip("a_2", 2), null);
  assert.deepEqual(said, ["Паттерн играет только здесь: он и так свой."]);
  assert.equal(changes.length, 1);
  assert.equal(editor.undo(false), true);
  assert.deepEqual(editor.score.sections[2].play, ["a"]);
});
