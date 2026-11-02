// The rack's second part: the pattern played on its own, one hit of a sound, adding a sound from
// the synths or the pack and taking one away, the zones the live engine still lacks, and the step
// the playing position lights.
import assert from "node:assert/strict";
import { test } from "node:test";

import { addSound, drumSteps, patternScore, setPatternBars, setSoundMuted } from "../src/beat/model.ts";
import { wantedZones, ZoneLoader } from "../src/daw/audition.ts";
import { addPicked, cursorColumn, hitScore, packSound, patternPreview, PATTERN_LOOP_BARS, soundUsed, stepAt, synthMenu } from "../src/daw/pattern.ts";
import { Playback } from "../src/daw/player.ts";
import { rackStep } from "../src/daw/rack.ts";

const settle = async () => {
  for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
};

const spec = (instrument, kind) => ({ instrument, kind, mono: false, max_voices: 8, max_life_s: 2, params: [] });
const SPECS = [
  spec("kick", "drum"),
  spec("snare", "drum"),
  spec("hat_closed", "drum"),
  spec("bass808", "note"),
  spec("pluck", "note"),
  spec("sampler", "note"),
  spec("oneshot", "drum"),
  spec("oneshot_hat", "drum"),
  spec("oneshot_open_hat", "drum"),
  spec("sample808", "note"),
];

// c: unplayed; a: two bars, in the verse with d and in the hook; b: the intro; d has the hat
const score = () => ({
  version: 1,
  bpm: 120,
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
        bass: [{ step: 0, len: 4, note: "D#2", vel: 0.8 }],
      },
    },
    b: { bars: 1, rows: { kick: "x...............", keys: [{ step: 0, len: 8, note: "G4", vel: 0.8 }] } },
    d: { bars: 1, rows: { hat: "x.x.x.x.x.x.x.x." } },
  },
  sections: [
    { name: "intro", bars: 4, play: ["b"] },
    { name: "verse", bars: 8, play: ["a", "d"] },
    { name: "hook", bars: 8, play: ["a"] },
  ],
  mix: { sounds: { kick: { gain_db: -3, pan: 0.2, sidechain: "bass" }, bass: { gain_db: -1 } }, master: [{ effect: "limiter" }] },
});

// ---------- the pattern on its own ----------

test("a pattern preview is the pattern alone, repeated to 64 bars", () => {
  const s = score();
  const one = patternPreview(s, "b");
  assert.equal(PATTERN_LOOP_BARS, 64);
  assert.equal(one.bars, 64);
  assert.deepEqual(one.score.sections, [{ name: "loop", bars: 64, play: ["b"] }]);
  assert.deepEqual(one.score.patterns, s.patterns);
  assert.deepEqual(one.score.sounds, s.sounds);
  const two = patternPreview(s, "a");
  assert.equal(two.bars, 64);
  assert.deepEqual(two.score, patternScore(s, "a", 32).score);
  const eight = setPatternBars({ ...s, sections: [] }, "c", 8).score;
  assert.equal(patternPreview(eight, "c").bars, 64);
  assert.deepEqual(patternPreview(eight, "c").score.sections, [{ name: "loop", bars: 64, play: ["c"] }]);
  assert.equal(patternPreview(s, "gone"), null);
  // the score given is left alone
  assert.deepEqual(s, score());
});

test("a sound muted in every section stays muted in the preview; one muted in some sections plays", () => {
  const muted = setSoundMuted(score(), "kick", true).score;
  assert.deepEqual(patternPreview(muted, "a").score.sections, [{ name: "loop", bars: 64, play: ["a"], mute: ["kick"] }]);
  const some = score();
  some.sections[1].mute = ["snare"];
  assert.deepEqual(patternPreview(some, "a").score.sections, [{ name: "loop", bars: 64, play: ["a"] }]);
});

// ---------- the playing step ----------

test("the playing position maps to the pattern's bar and step only inside a section that plays it", () => {
  const s = score();
  assert.deepEqual(stepAt(s, "a", 4), { bar: 0, step: 0 });
  assert.deepEqual(stepAt(s, "a", 5.5), { bar: 1, step: 8 });
  assert.deepEqual(stepAt(s, "a", 6.25), { bar: 0, step: 4 });
  assert.deepEqual(stepAt(s, "a", 12), { bar: 0, step: 0 });
  // the last 16th of the song
  assert.deepEqual(stepAt(s, "a", 19.99), { bar: 1, step: 15 });
  assert.deepEqual(stepAt(s, "b", 3.5), { bar: 0, step: 8 });
  assert.deepEqual(stepAt(s, "d", 11.97), { bar: 0, step: 15 });
  // the intro plays b, the hook no d; before the song and past it nothing
  assert.equal(stepAt(s, "a", 3.99), null);
  assert.equal(stepAt(s, "d", 12), null);
  assert.equal(stepAt(s, "a", 20), null);
  assert.equal(stepAt(s, "a", -0.01), null);
  assert.equal(stepAt(s, "a", Number.NaN), null);
  assert.equal(stepAt(s, "c", 1), null);
  assert.equal(stepAt(s, "gone", 1), null);
});

test("a fill lights on its section's last bar only", () => {
  const s = { ...score(), sections: [{ name: "x", bars: 4, play: ["b"], fill: "d" }] };
  assert.deepEqual(stepAt(s, "d", 3.25), { bar: 0, step: 4 });
  assert.equal(stepAt(s, "d", 2.99), null);
  assert.deepEqual(stepAt(s, "b", 3.25), { bar: 0, step: 4 });
});

test("the lit column is the step when its bar is the one shown, else none", () => {
  assert.equal(cursorColumn({ bar: 1, step: 8 }, 1), 8);
  assert.equal(cursorColumn({ bar: 0, step: 0 }, 0), 0);
  assert.equal(cursorColumn({ bar: 0, step: 15 }, 0), 15);
  assert.equal(cursorColumn({ bar: 1, step: 8 }, 0), null);
  assert.equal(cursorColumn(null, 0), null);
});

// ---------- one hit of a sound ----------

test("a drum's hit is one step of it alone, its mix kept but no sidechain to a sound not there", () => {
  const hit = hitScore(score(), SPECS, "kick", "a");
  assert.deepEqual(hit.sounds, { kick: { instrument: "kick" } });
  assert.deepEqual(hit.patterns, { hit: { bars: 1, rows: { kick: "x..............." } } });
  assert.deepEqual(hit.sections, [{ name: "hit", bars: 1, play: ["hit"] }]);
  assert.deepEqual(hit.mix, { sounds: { kick: { gain_db: -3, pan: 0.2 } }, master: [{ effect: "limiter" }] });
  assert.equal(hit.bpm, 120);
  assert.equal(hit.key, "C minor");
});

test("a note sound's hit is one beat at its pattern's first note, else another pattern's, else C2 for an 808 and C4 for others", () => {
  const note = (s, sound, pattern, midi) => hitScore(s, SPECS, sound, pattern, midi)?.patterns.hit.rows[sound];
  assert.deepEqual(note(score(), "bass", "a"), [{ step: 0, len: 4, note: "D#2", vel: 0.8 }]);
  // keys has no row in a: the first note it has anywhere
  assert.deepEqual(note(score(), "keys", "a"), [{ step: 0, len: 4, note: "G4", vel: 0.8 }]);
  const own = score();
  own.patterns.a.rows.keys = [{ step: 4, len: 2, note: "A3", vel: 0.5 }];
  assert.deepEqual(note(own, "keys", "a"), [{ step: 0, len: 4, note: "A3", vel: 0.8 }]);
  const bare = score();
  delete bare.patterns.a.rows.bass;
  delete bare.patterns.b.rows.keys;
  assert.deepEqual(note(bare, "bass", "a"), [{ step: 0, len: 4, note: "C2", vel: 0.8 }]);
  assert.deepEqual(note(bare, "keys", "a"), [{ step: 0, len: 4, note: "C4", vel: 0.8 }]);
  assert.deepEqual(note(bare, "keys", null), [{ step: 0, len: 4, note: "C4", vel: 0.8 }]);
  // a recorded 808 counts as an 808
  const rec = addSound(bare, SPECS, "low", "sample808", undefined, "pack-9");
  assert.deepEqual(note(rec.score, rec.name, "a"), [{ step: 0, len: 4, note: "C2", vel: 0.8 }]);
  assert.deepEqual(hitScore(rec.score, SPECS, rec.name, "a").sounds, { low: { instrument: "sample808", library: "pack-9" } });
  // a pitch asked for wins
  assert.deepEqual(note(score(), "bass", "a", 40), [{ step: 0, len: 4, note: "E2", vel: 0.8 }]);
});

test("a sound that is not there, or whose instrument is unknown, has no hit", () => {
  assert.equal(hitScore(score(), SPECS, "gone", "a"), null);
  const odd = score();
  odd.sounds.zz = { instrument: "theremin" };
  assert.equal(hitScore(odd, SPECS, "zz", "a"), null);
});

// ---------- adding and taking away ----------

test("a pack sound's category picks its instrument; the library is the sound's", () => {
  const item = (library, name) => ({ library, name, root: null });
  assert.deepEqual(packSound("kick", item("pack-1", "Hard Kick 01")), { name: "Hard Kick 01", instrument: "oneshot", library: "pack-1" });
  assert.equal(packSound("hat_closed", item("pack-2", "h")).instrument, "oneshot_hat");
  assert.equal(packSound("hat_open", item("pack-3", "o")).instrument, "oneshot_open_hat");
  assert.equal(packSound("808", item("pack-4", "b")).instrument, "sample808");
  assert.equal(packSound("melodic", item("pack-5", "m")).instrument, "sampler");
  for (const c of ["snare", "clap", "crash", "perc", "fx", "vox", "something new"]) assert.equal(packSound(c, item("pack-6", "x")).instrument, "oneshot", c);
});

test("the synth menu lists the instruments that need no samples, drums and note sounds apart", () => {
  const menu = synthMenu(SPECS);
  assert.deepEqual(menu.drums.map((s) => s.instrument), ["kick", "snare", "hat_closed"]);
  assert.deepEqual(menu.notes.map((s) => s.instrument), ["bass808", "pluck"]);
  assert.deepEqual(synthMenu([]), { drums: [], notes: [] });
});

test("an added sound gets an empty row in the pattern shown", () => {
  const pack = addPicked(score(), SPECS, "b", packSound("808", { library: "pack-4", name: "Big 808 (C)", root: 36 }));
  assert.equal(pack.ok, true);
  assert.equal(pack.name, "big_808_c");
  assert.deepEqual(pack.score.sounds.big_808_c, { instrument: "sample808", library: "pack-4" });
  assert.deepEqual(pack.score.patterns.b.rows.big_808_c, []);
  // no other pattern has it
  assert.equal(pack.score.patterns.a.rows.big_808_c, undefined);
  const drum = addPicked(score(), SPECS, "a", packSound("kick", { library: "pack-1", name: "Kick", root: null }));
  assert.equal(drum.name, "kick_2");
  assert.equal(drum.score.patterns.a.rows.kick_2, ".".repeat(32));
  const synth = addPicked(score(), SPECS, "a", { name: "snare", instrument: "snare" });
  assert.equal(synth.name, "snare_2");
  assert.deepEqual(synth.score.sounds.snare_2, { instrument: "snare" });
  // no pattern to put it in: the sound alone
  const loose = addPicked(score(), SPECS, null, { name: "pluck", instrument: "pluck" });
  assert.equal(loose.ok, true);
  assert.deepEqual(loose.score.sounds.pluck, { instrument: "pluck" });
});

test("a refused add says why and changes nothing", () => {
  const full = score();
  for (let i = 0; i < 10; i++) full.sounds[`s${i}`] = { instrument: "kick" };
  const made = addPicked(full, SPECS, "a", { name: "kick", instrument: "kick" });
  assert.equal(made.ok, false);
  assert.match(made.reason, /16/);
  const synth = addPicked(score(), SPECS, "a", { name: "x", instrument: "kick", library: "pack-1" });
  assert.equal(synth.ok, false);
  assert.equal(addPicked(score(), SPECS, "a", { name: "x", instrument: "theremin" }).ok, false);
});

test("a sound is in use when some pattern has a hit or a note of it", () => {
  const s = score();
  assert.equal(soundUsed(s, "kick"), true);
  assert.equal(soundUsed(s, "snare"), true);
  assert.equal(soundUsed(s, "bass"), true);
  assert.equal(soundUsed(s, "rim"), false);
  s.patterns.c.rows.rim = "................";
  s.patterns.c.rows.keys2 = [];
  assert.equal(soundUsed(s, "rim"), false);
  assert.equal(soundUsed(s, "keys2"), false);
  s.patterns.c.rows.rim = { steps: "...............g" };
  assert.equal(soundUsed(s, "rim"), true);
  assert.equal(soundUsed(s, "gone"), false);
});

// ---------- the zones the engine lacks ----------

const zone = (library, file) => ({ library, file, root: 60, vel: [0, 127], rate: 48000, channels: 1, frames: 10 });
const lib = (name, fetched, zones) => ({ name, sounds: [], licence: null, commercial: false, kind: null, fetched, zones, error: null });

test("the zones wanted are the listed ones of the libraries the score plays, and a pack sound the list lacks", () => {
  const s = score();
  s.sounds.piano = { instrument: "sampler", library: "keys_lib" };
  s.sounds.cold = { instrument: "sampler", library: "unfetched" };
  s.sounds.boom = { instrument: "sample808", library: "pack-7" };
  s.sounds.tick = { instrument: "oneshot", library: "pack-8" };
  const libraries = [
    lib("keys_lib", true, [zone("keys_lib", "0"), zone("keys_lib", "3")]),
    lib("unfetched", false, []),
    lib("other", true, [zone("other", "0")]),
    lib("pack-8", true, [zone("pack-8", "0")]),
  ];
  assert.deepEqual(
    wantedZones(libraries, s).map((z) => [z.library, z.file]),
    [
      ["keys_lib", "0"],
      ["keys_lib", "3"],
      ["pack-8", "0"],
      ["pack-7", "0"],
    ],
  );
  assert.deepEqual(wantedZones([], s).find((z) => z.library === "pack-7"), { library: "pack-7", file: "0", vel: [0, 127] });
  assert.deepEqual(wantedZones(libraries, score()), []);
});

function fakeClient() {
  const log = { loaded: [], rebuilt: 0 };
  return {
    log,
    loadZone: async (z) => {
      log.loaded.push(z.library);
      return { ok: true, errors: [], warnings: [] };
    },
    rebuildSamplers: async () => {
      log.rebuilt++;
      return { ok: true, errors: [], warnings: [] };
    },
  };
}

test("the zone loader asks only for zones not loaded yet, and rebuilds the samplers only after new ones", async () => {
  const asked = [];
  const loader = new ZoneLoader(async (library, file) => {
    asked.push(`${library}/${file}`);
    return { pcm: new ArrayBuffer(4), rate: 48000, channels: 1, frames: 2, root: 60 };
  });
  const client = fakeClient();
  const a = { library: "a", file: "0", vel: [0, 127] };
  const p = { library: "pack-1", file: "0", vel: [0, 127] };
  const q = { library: "pack-2", file: "0", vel: [0, 127] };
  assert.deepEqual(await loader.load(client, [a, p]), []);
  assert.deepEqual(asked, ["a/0", "pack-1/0"]);
  assert.equal(client.log.rebuilt, 1);
  assert.deepEqual(await loader.load(client, [a, p]), []);
  assert.deepEqual(asked, ["a/0", "pack-1/0"]);
  assert.equal(client.log.rebuilt, 1);
  await loader.load(client, [p, q, a]);
  assert.deepEqual(asked, ["a/0", "pack-1/0", "pack-2/0"]);
  assert.equal(client.log.rebuilt, 2);
  // two loads at once of the same new zone fetch it once
  const r = { library: "pack-3", file: "0", vel: [0, 127] };
  await Promise.all([loader.load(client, [r]), loader.load(client, [r])]);
  assert.deepEqual(asked, ["a/0", "pack-1/0", "pack-2/0", "pack-3/0"]);
  assert.deepEqual(client.log.loaded, ["a", "pack-1", "pack-2", "pack-3"]);
});

test("a zone that fails comes back as text, rebuilds nothing, and is asked for again on the next load", async () => {
  let asked = 0;
  let down = true;
  const loader = new ZoneLoader(async () => {
    asked++;
    if (down) throw new Error("404");
    return { pcm: new ArrayBuffer(4), rate: 48000, channels: 1, frames: 2, root: 60 };
  });
  const client = fakeClient();
  const z = { library: "pack-9", file: "0", vel: [0, 127] };
  const problems = await loader.load(client, [z]);
  assert.equal(problems.length, 1);
  assert.match(problems[0], /pack-9/);
  assert.equal(client.log.rebuilt, 0);
  // a live edit does not ask again; a failure that repeats is not reported again
  assert.deepEqual(await loader.load(client, [z], false), []);
  assert.equal(asked, 1);
  assert.deepEqual(await loader.load(client, [z]), []);
  assert.equal(asked, 2);
  down = false;
  assert.deepEqual(await loader.load(client, [z]), []);
  assert.equal(asked, 3);
  assert.equal(client.log.rebuilt, 1);
  await loader.load(client, [z]);
  assert.equal(asked, 3);
});

test("a load that finds a zone still on its way waits for it before it returns", async () => {
  let release;
  const gate = new Promise((r) => (release = r));
  const loader = new ZoneLoader(async () => {
    await gate;
    return { pcm: new ArrayBuffer(4), rate: 48000, channels: 1, frames: 2, root: 60 };
  });
  const client = fakeClient();
  const z = { library: "pack-5", file: "0", vel: [0, 127] };
  const first = loader.load(client, [z]);
  let second = false;
  const later = loader.load(client, [z]).then(() => (second = true));
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(second, false);
  release();
  await Promise.all([first, later]);
  assert.deepEqual(client.log.loaded, ["pack-5"]);
  // both loads leave the samplers rebuilt before they return
  assert.equal(client.log.rebuilt, 2);
});

// ---------- the owner: a hit and a retarget ----------

class FakeAudition {
  playing = false;
  started = [];
  updates = [];
  retargets = [];
  hits = [];
  stops = 0;
  at = 0;
  async start(score, fromBar, bars) {
    this.playing = true;
    this.started.push([score, fromBar, bars]);
  }
  update(score) {
    this.updates.push(score);
  }
  retarget(score, fromBar, bars) {
    this.retargets.push([score, fromBar, bars]);
  }
  async hit(score) {
    this.hits.push(score);
    return true;
  }
  position() {
    return this.at;
  }
  async stop() {
    this.playing = false;
    this.stops++;
  }
  async dispose() {}
}

class FakeSong {
  kind = "simple";
  playing = false;
  duration = 100;
  pauses = 0;
  async load() {}
  async play() {
    this.playing = true;
  }
  pause() {
    this.playing = false;
    this.pauses++;
  }
  stop() {
    this.playing = false;
  }
  seek() {}
  setLoop() {}
  setLaneGain() {}
  position() {
    return 0;
  }
  meter() {
    return 0;
  }
  async dispose() {}
}

function owner() {
  const audition = new FakeAudition();
  const song = new FakeSong();
  const playback = new Playback({ simple: () => song, studio: () => song, ready: async () => false, audition });
  return { playback, audition, song };
}

test("a hit plays over the song without stopping it, and is skipped while the engine loops a block or a pattern", async () => {
  const { playback, audition, song } = owner();
  await playback.play();
  assert.equal(song.playing, true);
  const hit = { bpm: 120 };
  assert.equal(await playback.hit(hit), true);
  assert.deepEqual(audition.hits, [hit]);
  assert.equal(song.playing, true);
  assert.equal(song.pauses, 0);
  await playback.audition({ bpm: 120 }, 0, 4);
  assert.equal(await playback.hit(hit), false);
  assert.equal(audition.hits.length, 1);
});

test("a retarget goes to the engine only while it plays; its position is null when it does not", async () => {
  const { playback, audition } = owner();
  playback.retargetAudition({ bpm: 1 }, 0, 64);
  assert.deepEqual(audition.retargets, []);
  assert.equal(playback.auditionPosition(), null);
  await playback.audition({ bpm: 120 }, 0, 64);
  audition.at = 2.5;
  playback.retargetAudition({ bpm: 2 }, 0, 32);
  assert.deepEqual(audition.retargets, [[{ bpm: 2 }, 0, 32]]);
  assert.equal(playback.auditionPosition(), 2.5);
});

// ---------- the editor in pattern mode ----------

function fakeDom() {
  const el = (tag) => {
    const e = {
      tag,
      children: [],
      attrs: {},
      className: "",
      style: { setProperty() {} },
      dataset: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      append(...n) {
        this.children.push(...n);
      },
      replaceChildren(...n) {
        this.children = n;
      },
      remove() {},
      setAttribute(k, v) {
        this.attrs[k] = v;
      },
      getAttribute(k) {
        return this.attrs[k] ?? null;
      },
      addEventListener(type, fn) {
        (this.handlers ??= {})[type] = fn;
      },
      removeEventListener() {},
      querySelectorAll: () => [],
      focus() {},
    };
    return e;
  };
  globalThis.document = { createElement: el, createElementNS: (_, tag) => el(tag) };
  globalThis.addEventListener = () => {};
  globalThis.removeEventListener = () => {};
}

const all = (node, out = []) => {
  if (node && typeof node === "object") {
    out.push(node);
    for (const c of node.children ?? []) all(c, out);
  }
  return out;
};
const find = (root, pred) => all(root).find((n) => typeof n === "object" && pred(n));
const textOf = (n) => (typeof n === "string" ? n : (n.children ?? []).map(textOf).join(""));

async function editorSetup({ confirm = () => true, starting = null } = {}) {
  fakeDom();
  const { Editor } = await import("../src/daw/editor.ts");
  const calls = [];
  const delivered = [];
  let auditioning = false;
  const playback = {
    audition: async (s, from, bars) => {
      calls.push(["start", s, from, bars]);
      if (starting) await starting;
      auditioning = true;
    },
    updateAudition: (s) => {
      calls.push(["update", s]);
      if (auditioning) delivered.push(["update", s]);
    },
    retargetAudition: (s, from, bars) => {
      calls.push(["retarget", s, from, bars]);
      // as the real playback: nothing reaches an engine that does not play yet
      if (auditioning) delivered.push(["retarget", s, from, bars]);
    },
    stopAudition: async () => {
      auditioning = false;
      calls.push(["stop"]);
    },
    hit: async (s) => {
      calls.push(["hit", s]);
      return true;
    },
    auditionPosition: () => (auditioning ? 0 : null),
  };
  const asked = [];
  const editor = new Editor({
    base: undefined,
    token: "t",
    api: {},
    beatApi: {
      draft: async () => ({ score: score(), base: "score-1.json" }),
      session: async () => ({ score_file: "score-1.json", score: score(), instruments: SPECS }),
      saveDraft: async () => {},
      save: async () => ({ file: "score-2.json" }),
      render: async () => ({ state: "running", score_file: null, error: null }),
      renderStatus: async () => ({ state: "idle", score_file: null, error: null }),
    },
    flipApi: {},
    flipDraft: { doc: null, flush: async () => {} },
    confirm: (text) => (asked.push(text), confirm(text)),
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
    scoreChanged() {},
    openTab() {},
    beatState() {},
    repaint() {},
    say() {},
    overlay: {},
  });
  editor.prepare();
  await settle();
  const panes = { children: [], replaceChildren(...n) { this.children = n; } };
  editor.mount(panes, "beat", "rack", ["rack", "roll", "fx"], true);
  return { editor, calls, delivered, panes, asked };
}

test("pattern mode plays the rack's pattern alone; edits, a pattern switch and a block pick retarget it; song mode sends the song again", async () => {
  const { editor, calls, panes } = await editorSetup();
  assert.equal(editor.patternMode, false);
  editor.setPatternMode(true);
  assert.equal(editor.patternMode, true);
  // the playhead's block is the intro, which plays b
  await editor.togglePattern();
  assert.equal(editor.patternPlaying, true);
  assert.deepEqual(calls, [["start", patternPreview(score(), "b").score, 0, 64]]);
  calls.length = 0;

  const next = rackStep(editor.score, SPECS, "b", "kick", 4, "click", 0).score;
  editor.editPattern(next);
  assert.deepEqual(calls, [["retarget", patternScore(next, "b", 64).score, 0, 64]]);
  calls.length = 0;
  // a knob's live step goes the same way
  const louder = { ...next, mix: { ...next.mix, sounds: { ...next.mix.sounds, keys: { gain_db: -4 } } } };
  editor.editPattern(louder, { live: true });
  assert.deepEqual(calls, [["retarget", patternPreview(louder, "b").score, 0, 64]]);
  calls.length = 0;

  // the rack's pattern picker
  const select = find(panes, (n) => n.tag === "select" && n.attrs["aria-label"] === "Паттерн");
  select.value = "a";
  select.handlers.change();
  assert.deepEqual(calls, [["retarget", patternPreview(editor.score, "a").score, 0, 64]]);
  calls.length = 0;
  // a block picked on the timeline: the rack follows it, so does the engine
  editor.selectBlock(2);
  assert.deepEqual(calls, [["retarget", patternPreview(editor.score, "a").score, 0, 64]]);
  calls.length = 0;
  editor.selectBlock(0);
  assert.deepEqual(calls, [["retarget", patternPreview(editor.score, "b").score, 0, 64]]);
  calls.length = 0;

  // song mode: the pattern stops and the next edit is the song's
  editor.setPatternMode(false);
  assert.equal(editor.patternPlaying, false);
  assert.deepEqual(calls, [["stop"]]);
  calls.length = 0;
  const again = rackStep(editor.score, SPECS, "b", "kick", 8, "click", 0).score;
  editor.editPattern(again);
  assert.deepEqual(calls, [["update", again]]);
});

test("an edit made while the engine starts reaches it once it plays", async () => {
  let started;
  const starting = new Promise((r) => (started = r));
  const { editor, delivered } = await editorSetup({ starting });
  editor.setPatternMode(true);
  const going = editor.togglePattern();
  // the engine is still opening: an edit has nowhere to go yet
  const next = rackStep(editor.score, SPECS, "b", "kick", 4, "click", 0).score;
  editor.editPattern(next);
  started();
  await going;
  const last = delivered.at(-1);
  assert.equal(last[0], "retarget");
  assert.deepEqual(last[1], patternPreview(next, "b").score);
});

test("a press in pattern mode while it plays stops it; song mode leaves edits as before", async () => {
  const { editor, calls } = await editorSetup();
  editor.setPatternMode(true);
  await editor.togglePattern();
  await editor.togglePattern();
  assert.equal(editor.patternPlaying, false);
  assert.deepEqual(calls.map((c) => c[0]), ["start", "stop"]);
  calls.length = 0;
  const next = rackStep(editor.score, SPECS, "b", "kick", 4, "click", 0).score;
  editor.editPattern(next);
  assert.deepEqual(calls, [["update", next]]);
});

test("a click on a channel's name plays one hit of it", async () => {
  const { editor, calls, panes } = await editorSetup();
  const name = find(panes, (n) => n.className?.startsWith("ch__name") && textOf(n).endsWith("bass"));
  name.handlers.click();
  await settle();
  const hits = calls.filter((c) => c[0] === "hit");
  assert.equal(hits.length, 1);
  assert.deepEqual(hits[0][1], hitScore(editor.score, SPECS, "bass", "b"));
});

test("taking a sound away asks only when it has hits or notes", async () => {
  const kept = await editorSetup({ confirm: () => false });
  const del = (panes, sound) => find(panes, (n) => n.className === "ch__del" && n.attrs["aria-label"]?.endsWith(sound));
  del(kept.panes, "kick").handlers.click();
  assert.equal(kept.asked.length, 1);
  assert.ok(kept.editor.score.sounds.kick);
  // rim plays nowhere: gone without a question
  del(kept.panes, "rim").handlers.click();
  assert.equal(kept.asked.length, 1);
  assert.equal(kept.editor.score.sounds.rim, undefined);
  const yes = await editorSetup({ confirm: () => true });
  del(yes.panes, "kick").handlers.click();
  assert.equal(yes.editor.score.sounds.kick, undefined);
  assert.equal(yes.editor.score.patterns.a.rows.kick, undefined);
  assert.deepEqual(drumSteps(yes.editor.score, "a", "snare").length, 32);
});
