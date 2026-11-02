// Pattern, sound and section editing in the beat model. The expected scores follow the score
// format's rules (apps/redline-dsp/core/src/beat/score.rs); `valid` below mirrors them.
import assert from "node:assert/strict";
import { test } from "node:test";

import * as model from "../src/beat/model.ts";

const {
  addSound,
  clonePattern,
  drumSteps,
  ensureRow,
  newPattern,
  patternLabel,
  patternScore,
  placePattern,
  rackRows,
  removeSound,
  setPatternBars,
  setSoundMuted,
  songBars,
  soundMuted,
  splitSection,
} = model;

const spec = (instrument, kind) => ({ instrument, kind, mono: false, max_voices: 8, max_life_s: 2, params: [] });
const SPECS = [
  spec("kick", "drum"),
  spec("snare", "drum"),
  spec("hat_closed", "drum"),
  spec("clap", "drum"),
  spec("bass808", "note"),
  spec("fm", "note"),
  spec("sampler", "note"),
  spec("oneshot", "drum"),
];

const lane = (f) => Array.from({ length: 32 }, (_, i) => f(i));

const base = () => ({
  version: 1,
  bpm: 140,
  seed: 7,
  key: "F# minor",
  sounds: {
    kick: { instrument: "kick" },
    snare: { instrument: "snare" },
    hat: { instrument: "hat_closed", swing: 60 },
    clap: { instrument: "clap" },
    bass: { instrument: "bass808", params: { glide_ms: 120 } },
    keys: { instrument: "fm" },
  },
  patterns: {
    drums: {
      bars: 2,
      rows: {
        kick: "x...|....|x.x.|....|x...|....|x...|..x.",
        snare: "....X.......X... ....X.......X.g.",
        hat: { steps: "x.x.".repeat(8), velocity: lane((i) => (i % 2 ? 0.5 : 0.9)), chance: lane((i) => (i === 7 ? 0.5 : 1)) },
      },
    },
    fill: { bars: 1, rows: { snare: "....X...X.X.XXXX", hat: "x".repeat(16), clap: "............X..." } },
    low: {
      bars: 2,
      rows: {
        bass: [
          { step: 0, len: 8, note: "F#1", vel: 0.9 },
          { step: 12, len: 8, note: "C#2", vel: 0.8, passing: true },
          { step: 24, len: 8, note: "A1", vel: 0.7 },
        ],
      },
    },
    lead: { bars: 4, rows: { keys: [{ step: 0, len: 4, note: "C#4", vel: 0.6 }] } },
    alt: { bars: 1, rows: { kick: "x.......x......." } },
  },
  sections: [
    { name: "intro", bars: 4, play: ["drums"], mute: ["kick"] },
    { name: "hook", bars: 8, play: ["drums", "low", "lead"], mute: ["keys"], fill: "fill" },
    { name: "outro", bars: 2, play: ["low"] },
  ],
  mix: {
    sounds: {
      bass: { gain_db: -3, pan: 0, rack: [{ effect: "saturate" }], sidechain: "kick" },
      keys: { pan: 0.2, sidechain: "kick" },
      hat: { pan: -0.25, sidechain: "snare" },
    },
    master: [{ effect: "limiter" }],
  },
});

const empty = () => ({ version: 1, bpm: 120, seed: 0, key: "C minor", sounds: {}, patterns: {}, sections: [] });

// ---------- the format's rules ----------

const NAME = /^[a-z0-9_]{1,24}$/;
const goodName = (n) => NAME.test(n) && n !== "__proto__" && n !== "constructor";
const goodSectionName = (n) => typeof n === "string" && /^[A-Za-z0-9 _-]{1,48}$/.test(n) && !n.startsWith(" ");
const LANES = ["velocity", "pitch", "decay", "chance"];

function valid(score, specs = SPECS) {
  const e = [];
  const kindOf = (sound) => {
    const s = score.sounds[sound];
    return s ? specs.find((x) => x.instrument === s.instrument)?.kind : undefined;
  };
  const sounds = Object.keys(score.sounds);
  if (sounds.length > 16) e.push(`${sounds.length} sounds, at most 16`);
  for (const [n, s] of Object.entries(score.sounds)) {
    if (!goodName(n)) e.push(`sounds.${n}: bad name`);
    if (!kindOf(n)) e.push(`sounds.${n}: no instrument ${s.instrument}`);
  }
  const patterns = Object.entries(score.patterns);
  if (patterns.length > 64) e.push(`${patterns.length} patterns, at most 64`);
  for (const [pn, p] of patterns) {
    if (!goodName(pn)) e.push(`patterns.${pn}: bad name`);
    if (![1, 2, 4, 8].includes(p.bars)) e.push(`patterns.${pn}: ${p.bars} bars`);
    const end = p.bars * 16;
    for (const [sn, row] of Object.entries(p.rows)) {
      const path = `patterns.${pn}.rows.${sn}`;
      const kind = kindOf(sn);
      if (!kind) {
        e.push(`${path}: no sound`);
        continue;
      }
      if (kind === "drum") {
        const text = typeof row === "string" ? row : row && !Array.isArray(row) && typeof row.steps === "string" ? row.steps : null;
        if (text === null) {
          e.push(`${path}: a drum sound takes a step string`);
          continue;
        }
        const steps = [...text].filter((c) => c !== "|" && c !== " ");
        if (steps.some((c) => !".xXg2346".includes(c))) e.push(`${path}: bad step`);
        if (steps.length !== end) e.push(`${path}: ${steps.length} steps, needs ${end}`);
        if (typeof row === "object") {
          for (const l of LANES) if (row[l] !== undefined && row[l].length !== end) e.push(`${path}.${l}: ${row[l].length} values, needs ${end}`);
        }
      } else {
        if (!Array.isArray(row)) {
          e.push(`${path}: a note sound takes a list`);
          continue;
        }
        row.forEach((n, i) => {
          const ok = Number.isInteger(n.step) && n.step >= 0 && n.step < end && Number.isInteger(n.len) && n.len >= 1 && n.step + n.len <= end;
          if (!ok) e.push(`${path}[${i}]: outside the pattern`);
        });
      }
    }
  }
  if (!score.sections.length) e.push("sections: no sections");
  let total = 0;
  score.sections.forEach((s, i) => {
    const path = `sections[${i}]`;
    if (!goodSectionName(s.name)) e.push(`${path}.name: bad name ${s.name}`);
    if (!Number.isInteger(s.bars) || s.bars < 1 || s.bars > 512) e.push(`${path}.bars: ${s.bars}`);
    total += s.bars;
    const play = s.play ?? [];
    play.forEach((n, j) => {
      if (!score.patterns[n]) e.push(`${path}.play[${j}]: no pattern ${n}`);
      if (play.indexOf(n) !== j) e.push(`${path}.play[${j}]: ${n} is played twice`);
    });
    // the fill is exempt: it replaces the played rows on the last bar
    for (let a = 0; a < play.length; a++) {
      for (let b = a + 1; b < play.length; b++) {
        const ra = Object.keys(score.patterns[play[a]]?.rows ?? {});
        const rb = new Set(Object.keys(score.patterns[play[b]]?.rows ?? {}));
        const shared = ra.find((k) => rb.has(k));
        if (shared) e.push(`${path}.play: ${play[a]} and ${play[b]} both have rows for ${shared}`);
      }
    }
    for (const n of play) {
      const pb = score.patterns[n]?.bars;
      if (pb && s.bars % pb !== 0) e.push(`${path}.bars: ${s.bars} is not a multiple of ${n}'s ${pb}`);
    }
    for (const m of s.mute ?? []) if (!score.sounds[m]) e.push(`${path}.mute: no sound ${m}`);
    if (s.fill !== undefined) {
      const f = score.patterns[s.fill];
      if (!f) e.push(`${path}.fill: no pattern ${s.fill}`);
      else if (f.bars !== 1) e.push(`${path}.fill: ${s.fill} has ${f.bars} bars`);
    }
  });
  if (total > 512) e.push(`sections: ${total} bars in all`);
  for (const [n, m] of Object.entries(score.mix?.sounds ?? {})) {
    if (!score.sounds[n]) e.push(`mix.sounds.${n}: no sound`);
    if (m.sidechain !== undefined) {
      if (m.sidechain === n) e.push(`mix.sounds.${n}.sidechain: its own`);
      else if (!score.sounds[m.sidechain]) e.push(`mix.sounds.${n}.sidechain: no sound ${m.sidechain}`);
    }
  }
  return e;
}

// ---------- helpers ----------

const ok = (r) => {
  assert.equal(r.ok, true, r.reason);
  return r;
};

const refused = (r, ...words) => {
  assert.equal(r.ok, false, "expected a refusal");
  assert.equal(typeof r.reason, "string");
  assert.match(r.reason, /[А-Яа-яЁё]/, "the reason reads in Russian");
  for (const w of words) assert.ok(r.reason.includes(w), `"${r.reason}" should name ${w}`);
  assert.equal("score" in r, false);
};

// runs f on the score and checks the score itself is left as it was
const pure = (score, f) => {
  const before = structuredClone(score);
  const r = f(score);
  assert.deepEqual(score, before, "the input score was changed");
  return r;
};

const validScore = (score) => assert.deepEqual(valid(score), []);

test("the rules helper: the fixture is valid, a shared played row is not, a shared fill row is", () => {
  validScore(base());
  const shared = base();
  shared.sections[2].play.push("alt");
  shared.patterns.alt.rows.bass = [];
  assert.equal(valid(shared).length, 1);
  const bad = base();
  bad.sections[0].bars = 3;
  bad.mix.sounds.keys.sidechain = "nothing";
  bad.patterns.drums.rows.kick = "x...";
  assert.equal(valid(bad).length, 3);
  assert.deepEqual(valid(empty()), ["sections: no sections"]);
});

// ---------- patternScore ----------

test("patternScore plays only the pattern, loops times, in one section called loop", () => {
  const s = base();
  const r = ok(pure(s, (x) => patternScore(x, "lead", 2)));
  assert.deepEqual(r.score.sections, [{ name: "loop", bars: 8, play: ["lead"] }]);
  assert.deepEqual(r.score.sounds, s.sounds);
  assert.deepEqual(r.score.mix, s.mix);
  assert.deepEqual(r.score.patterns, s.patterns);
  assert.equal(r.score.bpm, 140);
  validScore(r.score);
  // the fill alone, and a pattern whose section muted a sound: no mute comes along
  const fill = ok(patternScore(s, "fill", 4));
  assert.deepEqual(fill.score.sections, [{ name: "loop", bars: 4, play: ["fill"] }]);
  const drums = ok(patternScore(s, "drums", 1));
  assert.deepEqual(drums.score.sections, [{ name: "loop", bars: 2, play: ["drums"] }]);
  validScore(drums.score);
});

test("patternScore refuses an unknown pattern, a loop count that is not a whole number from 1, and past 512 bars", () => {
  const s = base();
  refused(pure(s, (x) => patternScore(x, "nothing", 1)));
  refused(patternScore(s, "drums", 0));
  refused(patternScore(s, "drums", 1.5));
  refused(patternScore(empty(), "p_1", 1));
  const long = ok(setPatternBars(s, "alt", 8)).score;
  validScore(ok(patternScore(long, "alt", 64)).score);
  refused(patternScore(long, "alt", 65), "512");
});

// ---------- newPattern and clonePattern ----------

test("newPattern takes the first free p_n, has no rows and the bars asked for", () => {
  const s = base();
  const a = ok(pure(s, (x) => newPattern(x, 8)));
  assert.equal(a.name, "p_1");
  assert.deepEqual(a.score.patterns.p_1, { bars: 8, rows: {} });
  validScore(a.score);
  const b = ok(newPattern(a.score, 1));
  assert.equal(b.name, "p_2");
  const gap = base();
  gap.patterns.p_1 = { bars: 1, rows: {} };
  gap.patterns.p_3 = { bars: 1, rows: {} };
  assert.equal(ok(newPattern(gap, 2)).name, "p_2");
  const e = ok(pure(empty(), (x) => newPattern(x, 1)));
  assert.equal(e.name, "p_1");
  assert.deepEqual(e.score.patterns, { p_1: { bars: 1, rows: {} } });
  assert.deepEqual(e.score.sections, []);
});

test("newPattern refuses bars other than 1, 2, 4, 8 and the 65th pattern", () => {
  const s = base();
  for (const bars of [0, 3, 16, 1.5]) refused(newPattern(s, bars));
  const full = empty();
  for (let i = 0; i < 64; i++) full.patterns[`q${i}`] = { bars: 1, rows: {} };
  refused(pure(full, (x) => newPattern(x, 1)), "64");
  delete full.patterns.q63;
  assert.equal(Object.keys(ok(newPattern(full, 1)).score.patterns).length, 64);
});

test("clonePattern copies rows and bars under a fresh name; the copy is its own", () => {
  const s = base();
  const r = ok(pure(s, (x) => clonePattern(x, "drums")));
  assert.equal(r.name, "drums_2");
  assert.deepEqual(r.score.patterns.drums_2, s.patterns.drums);
  r.score.patterns.drums_2.rows.hat.velocity[0] = 0.1;
  assert.equal(r.score.patterns.drums.rows.hat.velocity[0], 0.9);
  assert.deepEqual(r.score.sections, s.sections);
  validScore(r.score);
  const again = ok(clonePattern(r.score, "drums_2"));
  assert.equal(again.name, "drums_3");
  const long = base();
  long.patterns["a".repeat(24)] = { bars: 1, rows: {} };
  const named = ok(clonePattern(long, "a".repeat(24))).name;
  assert.ok(NAME.test(named), named);
  assert.notEqual(named, "a".repeat(24));
});

test("clonePattern refuses an unknown pattern and the 65th pattern", () => {
  refused(clonePattern(base(), "nothing"));
  refused(clonePattern(empty(), "p_1"));
  const full = base();
  for (let i = Object.keys(full.patterns).length; i < 64; i++) full.patterns[`q${i}`] = { bars: 1, rows: {} };
  refused(pure(full, (x) => clonePattern(x, "drums")), "64");
});

// ---------- setPatternBars ----------

test("setPatternBars cuts drum rows and their lanes to the new length", () => {
  const s = base();
  const r = ok(pure(s, (x) => setPatternBars(x, "drums", 1)));
  const p = r.score.patterns.drums;
  assert.equal(p.bars, 1);
  assert.equal(drumSteps(r.score, "drums", "kick").join(""), "x.......x.x.....");
  assert.equal(drumSteps(r.score, "drums", "snare").join(""), "....X.......X...");
  assert.equal(drumSteps(r.score, "drums", "hat").join(""), "x.x.".repeat(4));
  assert.deepEqual(p.rows.hat.velocity, s.patterns.drums.rows.hat.velocity.slice(0, 16));
  assert.deepEqual(p.rows.hat.chance, s.patterns.drums.rows.hat.chance.slice(0, 16));
  validScore(r.score);
});

test("setPatternBars pads drum rows and lanes with rests; the old steps and values stay", () => {
  const s = base();
  const r = ok(setPatternBars(s, "drums", 4));
  const kick = drumSteps(r.score, "drums", "kick");
  assert.equal(kick.length, 64);
  assert.deepEqual(kick.slice(0, 32), drumSteps(s, "drums", "kick"));
  assert.equal(kick.slice(32).join(""), ".".repeat(32));
  const hat = r.score.patterns.drums.rows.hat;
  assert.equal(hat.velocity.length, 64);
  assert.deepEqual(hat.velocity.slice(0, 32), s.patterns.drums.rows.hat.velocity);
  validScore(r.score);
  // 1 bar to 8: the unused one-bar pattern
  const eight = ok(setPatternBars(s, "alt", 8));
  assert.equal(drumSteps(eight.score, "alt", "kick").join(""), "x.......x......." + ".".repeat(112));
  validScore(eight.score);
});

test("setPatternBars drops notes past the end and shortens notes running past it", () => {
  const s = base();
  const r = ok(pure(s, (x) => setPatternBars(x, "low", 1)));
  assert.deepEqual(r.score.patterns.low.rows.bass, [
    { step: 0, len: 8, note: "F#1", vel: 0.9 },
    { step: 12, len: 4, note: "C#2", vel: 0.8, passing: true },
  ]);
  validScore(r.score);
  // at the boundary: a note on the new end goes, a note ending on it stays as it is
  const edge = base();
  edge.patterns.low.rows.bass = [
    { step: 15, len: 1, note: "F#1", vel: 0.9 },
    { step: 16, len: 2, note: "B1", vel: 0.5 },
  ];
  assert.deepEqual(ok(setPatternBars(edge, "low", 1)).score.patterns.low.rows.bass, [{ step: 15, len: 1, note: "F#1", vel: 0.9 }]);
  const longer = ok(setPatternBars(s, "lead", 8));
  assert.deepEqual(longer.score.patterns.lead.rows.keys, s.patterns.lead.rows.keys);
  assert.equal(longer.score.patterns.lead.bars, 8);
  validScore(longer.score);
});

test("setPatternBars refuses a length a playing section is not a multiple of, and a fill longer than a bar", () => {
  const s = base();
  // the first section has 4 bars
  refused(pure(s, (x) => setPatternBars(x, "drums", 8)), "intro");
  // the last section has 2
  refused(setPatternBars(s, "low", 4), "outro");
  refused(setPatternBars(s, "fill", 2));
  validScore(ok(setPatternBars(s, "fill", 1)).score);
  for (const bars of [0, 3, 16]) refused(setPatternBars(s, "alt", bars));
  refused(setPatternBars(s, "nothing", 1));
});

// ---------- placePattern ----------

test("placePattern adds a pattern to a section's play when its bars fit and its sounds are free", () => {
  const s = base();
  const r = ok(pure(s, (x) => placePattern(x, "alt", 2, true)));
  assert.deepEqual(r.score.sections[2].play, ["low", "alt"]);
  assert.deepEqual(r.score.sections.slice(0, 2), s.sections.slice(0, 2));
  validScore(r.score);
  // a sound only the fill has does not block: the fill replaces rows, it is not played with them
  const claps = base();
  claps.patterns.claps = { bars: 1, rows: { clap: "....x.......x..." } };
  const placed = ok(placePattern(claps, "claps", 1, true));
  assert.deepEqual(placed.score.sections[1].play, ["drums", "low", "lead", "claps"]);
  validScore(placed.score);
  // already played: nothing changes, nothing doubles
  assert.deepEqual(ok(placePattern(s, "drums", 0, true)).score, s);
});

test("placePattern refuses a sound another played pattern has, naming both, and bars that do not fit", () => {
  const s = base();
  refused(pure(s, (x) => placePattern(x, "alt", 0, true)), "drums", "kick");
  // a four-bar pattern in the two-bar last section
  refused(placePattern(s, "lead", 2, true), "lead");
  refused(placePattern(s, "nothing", 0, true));
  refused(placePattern(s, "alt", 3, true));
  refused(placePattern(s, "alt", -1, true));
  refused(placePattern(empty(), "p_1", 0, true));
});

test("placePattern off takes a pattern out of play and leaves the fill alone", () => {
  const s = base();
  const r = ok(pure(s, (x) => placePattern(x, "low", 1, false)));
  assert.deepEqual(r.score.sections[1].play, ["drums", "lead"]);
  assert.equal(r.score.sections[1].fill, "fill");
  validScore(r.score);
  const fill = ok(placePattern(s, "fill", 1, false));
  assert.deepEqual(fill.score.sections[1], s.sections[1]);
  const last = ok(placePattern(s, "low", 2, false));
  assert.deepEqual(last.score.sections[2].play, []);
  validScore(last.score);
});

// ---------- splitSection ----------

test("splitSection makes two halves with the same play and mute; the fill goes to the second", () => {
  const s = base();
  const r = ok(pure(s, (x) => splitSection(x, 1, 4)));
  assert.deepEqual(r.score.sections, [
    s.sections[0],
    { name: "hook", bars: 4, play: ["drums", "low", "lead"], mute: ["keys"] },
    { name: "hook_2", bars: 4, play: ["drums", "low", "lead"], mute: ["keys"], fill: "fill" },
    s.sections[2],
  ]);
  assert.equal(r.selected, 1);
  assert.equal(songBars(r.score), songBars(s));
  validScore(r.score);
  // the first section
  const first = ok(splitSection(s, 0, 2));
  assert.deepEqual(first.score.sections.slice(0, 2), [
    { name: "intro", bars: 2, play: ["drums"], mute: ["kick"] },
    { name: "intro_2", bars: 2, play: ["drums"], mute: ["kick"] },
  ]);
  validScore(first.score);
  // the second half's name is free among the sections
  const taken = base();
  taken.sections.push({ name: "hook_2", bars: 2, play: [] });
  const named = ok(splitSection(taken, 1, 4));
  assert.equal(named.score.sections[2].name, "hook_3");
  validScore(named.score);
});

test("splitSection refuses a split point outside the section and halves a played pattern does not divide", () => {
  const s = base();
  refused(pure(s, (x) => splitSection(x, 1, 2)), "lead");
  refused(splitSection(s, 1, 6), "lead");
  refused(splitSection(s, 0, 1), "drums");
  // the last section: 2 bars of a 2-bar pattern
  refused(splitSection(s, 2, 1), "low");
  for (const at of [0, 8, -1, 9, 1.5]) refused(splitSection(s, 1, at));
  refused(splitSection(s, 3, 1));
  refused(splitSection(empty(), 0, 1));
  const one = base();
  one.sections.push({ name: "tail", bars: 1, play: [] });
  refused(splitSection(one, 3, 1));
});

// ---------- addSound and removeSound ----------

test("addSound adds a sound under a fresh name from the base, with its params and library", () => {
  const s = base();
  const r = ok(pure(s, (x) => addSound(x, SPECS, "kick", "kick")));
  assert.equal(r.name, "kick_2");
  assert.deepEqual(r.score.sounds.kick_2, { instrument: "kick" });
  assert.deepEqual(Object.keys(r.score.sounds).at(-1), "kick_2");
  validScore(r.score);
  const params = { tune: -2 };
  const p = ok(addSound(s, SPECS, "piano", "sampler", params, "lib_a"));
  assert.equal(p.name, "piano");
  assert.deepEqual(p.score.sounds.piano, { instrument: "sampler", params: { tune: -2 }, library: "lib_a" });
  params.tune = 5;
  assert.equal(p.score.sounds.piano.params.tune, -2);
  const e = ok(addSound(empty(), SPECS, "bass", "bass808"));
  assert.deepEqual(e.score.sounds, { bass: { instrument: "bass808" } });
  // a base that is not a sound name is made into one
  const odd = ok(addSound(s, SPECS, "Big Kick", "kick"));
  assert.ok(goodName(odd.name), odd.name);
  assert.ok(!(odd.name in s.sounds));
});

test("addSound refuses an unknown instrument and the 17th sound", () => {
  const s = base();
  refused(pure(s, (x) => addSound(x, SPECS, "x", "theremin")), "theremin");
  const full = base();
  for (let i = Object.keys(full.sounds).length; i < 16; i++) full.sounds[`k${i}`] = { instrument: "kick" };
  validScore(full);
  refused(pure(full, (x) => addSound(x, SPECS, "one", "kick")), "16");
});

test("removeSound takes its rows, mix, mutes and the sidechains that name it", () => {
  const s = base();
  const r = ok(pure(s, (x) => removeSound(x, "kick")));
  assert.equal("kick" in r.score.sounds, false);
  assert.deepEqual(Object.keys(r.score.patterns.drums.rows), ["snare", "hat"]);
  // a pattern left with no rows stays
  assert.deepEqual(r.score.patterns.alt, { bars: 1, rows: {} });
  // the first section muted only the kick: its mute list goes
  assert.deepEqual(r.score.sections[0], { name: "intro", bars: 4, play: ["drums"] });
  assert.deepEqual(r.score.mix.sounds.bass, { gain_db: -3, pan: 0, rack: [{ effect: "saturate" }] });
  assert.deepEqual(r.score.mix.sounds.keys, { pan: 0.2 });
  assert.deepEqual(r.score.mix.sounds.hat, s.mix.sounds.hat);
  validScore(r.score);
  const snare = ok(removeSound(s, "snare"));
  assert.deepEqual(snare.score.mix.sounds.hat, { pan: -0.25 });
  assert.deepEqual(Object.keys(snare.score.patterns.fill.rows), ["hat", "clap"]);
  validScore(snare.score);
  // a sound with its own mix entry, muted in the second section
  const keys = ok(removeSound(s, "keys"));
  assert.equal("keys" in keys.score.mix.sounds, false);
  assert.deepEqual(keys.score.sections[1].mute, undefined);
  assert.deepEqual(keys.score.patterns.lead, { bars: 4, rows: {} });
  validScore(keys.score);
  refused(removeSound(s, "nothing"));
  refused(removeSound(empty(), "kick"));
});

// ---------- ensureRow ----------

test("ensureRow gives the pattern an empty row of the sound's kind and length", () => {
  const s = base();
  const r = ok(pure(s, (x) => ensureRow(x, SPECS, "alt", "snare")));
  assert.equal(r.score.patterns.alt.rows.snare, ".".repeat(16));
  assert.equal(r.score.patterns.alt.rows.kick, s.patterns.alt.rows.kick);
  validScore(r.score);
  const p = ok(newPattern(s, 8));
  const drum = ok(ensureRow(p.score, SPECS, p.name, "hat"));
  assert.equal(drum.score.patterns[p.name].rows.hat, ".".repeat(128));
  const note = ok(ensureRow(drum.score, SPECS, p.name, "bass"));
  assert.deepEqual(note.score.patterns[p.name].rows.bass, []);
  validScore(note.score);
  // a row it has already keeps its hits
  assert.deepEqual(ok(ensureRow(s, SPECS, "drums", "kick")).score, s);
});

test("ensureRow: a fill may share sounds with the patterns of its section", () => {
  const s = base();
  const fill = ok(ensureRow(s, SPECS, "fill", "kick"));
  assert.equal(fill.score.patterns.fill.rows.kick, ".".repeat(16));
  validScore(fill.score);
  // the clap is only in the fill; low is played with that fill
  const low = ok(ensureRow(s, SPECS, "low", "clap"));
  assert.equal(low.score.patterns.low.rows.clap, ".".repeat(32));
  validScore(low.score);
});

test("ensureRow refuses a sound another pattern of a shared section has, naming that pattern", () => {
  const s = base();
  refused(pure(s, (x) => ensureRow(x, SPECS, "lead", "bass")), "low");
  refused(ensureRow(s, SPECS, "low", "keys"), "lead");
  refused(ensureRow(s, SPECS, "lead", "snare"), "drums");
  refused(ensureRow(s, SPECS, "nothing", "kick"));
  refused(ensureRow(s, SPECS, "alt", "nothing"));
  const odd = base();
  odd.sounds.weird = { instrument: "theremin" };
  refused(ensureRow(odd, SPECS, "alt", "weird"));
});

// ---------- mute ----------

test("soundMuted is true only when every section mutes the sound", () => {
  const s = base();
  assert.equal(soundMuted(s, "kick"), false);
  assert.equal(soundMuted(s, "keys"), false);
  assert.equal(soundMuted(s, "snare"), false);
  const all = base();
  for (const x of all.sections) x.mute = [...(x.mute ?? []), "snare"];
  assert.equal(soundMuted(all, "snare"), true);
  assert.equal(soundMuted(empty(), "kick"), false);
});

test("setSoundMuted adds the sound to every section's mute once and takes it out again", () => {
  const s = base();
  const on = ok(pure(s, (x) => setSoundMuted(x, "kick", true)));
  assert.deepEqual(
    on.score.sections.map((x) => x.mute),
    [["kick"], ["keys", "kick"], ["kick"]],
  );
  assert.equal(soundMuted(on.score, "kick"), true);
  validScore(on.score);
  const off = ok(pure(on.score, (x) => setSoundMuted(x, "kick", false)));
  assert.deepEqual(
    off.score.sections.map((x) => x.mute),
    [undefined, ["keys"], undefined],
  );
  assert.equal("mute" in off.score.sections[0], false);
  assert.equal(soundMuted(off.score, "kick"), false);
  validScore(off.score);
  const twice = ok(setSoundMuted(on.score, "kick", true));
  assert.deepEqual(twice.score, on.score);
  refused(setSoundMuted(s, "nothing", true));
  assert.deepEqual(ok(setSoundMuted({ ...empty(), sounds: { kick: { instrument: "kick" } } }, "kick", true)).score.sections, []);
});

// ---------- rackRows and patternLabel ----------

test("rackRows lists every sound in score order with its kind, row, lock and mute", () => {
  const s = base();
  s.sections.forEach((x) => (x.mute = [...(x.mute ?? []), "clap"]));
  const rows = pure(s, (x) => rackRows(x, SPECS, "lead"));
  assert.deepEqual(rows, [
    { sound: "kick", instrument: "kick", kind: "drum", inPattern: false, lockedBy: "drums", muted: false },
    { sound: "snare", instrument: "snare", kind: "drum", inPattern: false, lockedBy: "drums", muted: false },
    { sound: "hat", instrument: "hat_closed", kind: "drum", inPattern: false, lockedBy: "drums", muted: false },
    { sound: "clap", instrument: "clap", kind: "drum", inPattern: false, lockedBy: null, muted: true },
    { sound: "bass", instrument: "bass808", kind: "note", inPattern: false, lockedBy: "low", muted: false },
    { sound: "keys", instrument: "fm", kind: "note", inPattern: true, lockedBy: null, muted: false },
  ]);
  const free = rackRows(s, SPECS, "alt");
  assert.deepEqual(
    free.map((r) => [r.sound, r.inPattern, r.lockedBy]),
    [
      ["kick", true, null],
      ["snare", false, null],
      ["hat", false, null],
      ["clap", false, null],
      ["bass", false, null],
      ["keys", false, null],
    ],
  );
  // the fill locks nothing and is locked by nothing
  assert.ok(rackRows(s, SPECS, "fill").every((r) => r.lockedBy === null));
  assert.deepEqual(rackRows(empty(), SPECS, "p_1"), []);
  const odd = base();
  odd.sounds.weird = { instrument: "theremin" };
  assert.equal(rackRows(odd, SPECS, "alt").at(-1).kind, null);
});

test("patternLabel numbers from 1 and names the first section that plays the pattern", () => {
  const s = base();
  assert.equal(patternLabel(s, "drums", 0), "Паттерн 1 · intro");
  assert.equal(patternLabel(s, "low", 2), "Паттерн 3 · hook");
  assert.equal(patternLabel(s, "fill", 1), "Паттерн 2 · hook");
  assert.equal(patternLabel(s, "alt", 4), "Паттерн 5");
  assert.equal(patternLabel(empty(), "p_1", 0), "Паттерн 1");
});

test("addSound refuses a library on an instrument that plays no recordings", () => {
  const got = addSound(base(), SPECS, "k", "kick", undefined, "pack-0000000001");
  assert.equal(got.ok, false);
  const ok = addSound(base(), SPECS, "k", "oneshot", undefined, "pack-0000000001");
  assert.equal(ok.ok, true);
});
