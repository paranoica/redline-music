// The simple knobs: a value 0..1 to the settings keys and back.
import assert from "node:assert/strict";
import { test } from "node:test";

import { KNOBS, knobText, knobsFromSettings, settingsFromKnob } from "../src/daw/knobs.ts";

// the defaults of redline.engine.settings.Settings for the keys the knobs touch, plus one they do not
const DEFAULTS = {
  version: 2,
  chain_on: true,
  comp_threshold_db: -38,
  drive_db: 30,
  vocal_db: -6,
  hard_tune: 0,
  throw_on: false,
  throw_db: -6,
  reverb_on: false,
  reverb_mix_db: -16,
  beat_db: 0,
  bass_drive_db: 12,
  rack: [],
};

// [low, high, step] from the settings specs
const RANGES = {
  vocal_db: [-24, 6, 0.5],
  comp_threshold_db: [-60, 0, 1],
  drive_db: [0, 40, 1],
  throw_db: [-24, 0, 0.5],
  reverb_mix_db: [-40, 0, 0.5],
  hard_tune: [0, 100, 5],
  beat_db: [-12, 6, 0.5],
  bass_drive_db: [0, 30, 1],
};

// the page writes numbers with a narrow no-break space before the unit
const NB = "\u202f";
const IDS = ["vocal", "clean", "echo", "reverb", "tune", "beat", "bass"];

test("five voice knobs and two beat knobs, named as on the screen", () => {
  assert.deepEqual(
    KNOBS.filter((k) => k.group === "voice").map((k) => [k.id, k.name]),
    [
      ["vocal", "Громкость"],
      ["clean", "Чистота"],
      ["echo", "Эхо"],
      ["reverb", "Реверб"],
      ["tune", "Тюн"],
    ],
  );
  assert.deepEqual(
    KNOBS.filter((k) => k.group === "beat").map((k) => [k.id, k.name]),
    [
      ["beat", "Громкость"],
      ["bass", "808"],
    ],
  );
});

test("each knob sets its keys at 0, 0.5 and 1", () => {
  const at = (knob, v) => settingsFromKnob(DEFAULTS, knob, v);
  assert.deepEqual([0, 0.5, 1].map((v) => at("vocal", v).vocal_db), [-24, -9, 6]);
  assert.deepEqual(
    [0, 0.5, 1].map((v) => [at("clean", v).comp_threshold_db, at("clean", v).drive_db]),
    [
      [-10, 0],
      [-30, 20],
      [-50, 40],
    ],
  );
  assert.deepEqual(
    [0, 0.5, 1].map((v) => [at("echo", v).throw_on, at("echo", v).throw_db]),
    [
      [false, -6],
      [true, -12],
      [true, 0],
    ],
  );
  assert.deepEqual(
    [0, 0.5, 1].map((v) => [at("reverb", v).reverb_on, at("reverb", v).reverb_mix_db]),
    [
      [false, -16],
      [true, -20],
      [true, 0],
    ],
  );
  assert.deepEqual([0, 0.5, 1].map((v) => at("tune", v).hard_tune), [0, 50, 100]);
  assert.deepEqual([0, 0.5, 1].map((v) => at("beat", v).beat_db), [-12, -3, 6]);
  assert.deepEqual([0, 0.5, 1].map((v) => at("bass", v).bass_drive_db), [0, 15, 30]);
});

test("a knob read back from the settings it wrote is where it was set", () => {
  for (const id of IDS) {
    for (const v of [0, 0.5, 1]) {
      assert.equal(knobsFromSettings(settingsFromKnob(DEFAULTS, id, v))[id], v, `${id} at ${v}`);
    }
  }
});

test("the default settings read as these knob positions", () => {
  const k = knobsFromSettings(DEFAULTS);
  assert.equal(k.vocal, 0.6);
  assert.ok(Math.abs(k.clean - (0.7 + 0.75) / 2) < 1e-9);
  assert.equal(k.echo, 0);
  assert.equal(k.reverb, 0);
  assert.equal(k.tune, 0);
  assert.ok(Math.abs(k.beat - 12 / 18) < 1e-9);
  assert.equal(k.bass, 0.4);
});

test("zero turns the echo and the reverb off, any other value turns them on", () => {
  const on = { ...DEFAULTS, throw_on: true, reverb_on: true };
  assert.equal(settingsFromKnob(on, "echo", 0).throw_on, false);
  assert.equal(settingsFromKnob(on, "reverb", 0).reverb_on, false);
  assert.equal(settingsFromKnob(DEFAULTS, "echo", 0.01).throw_on, true);
  assert.equal(settingsFromKnob(DEFAULTS, "reverb", 0.01).reverb_on, true);
  assert.equal(settingsFromKnob(on, "echo", 0).reverb_on, true, "the other flag stays");
});

test("an effect that is on never reads as zero, even at its quietest", () => {
  const quiet = { ...DEFAULTS, throw_on: true, throw_db: -24, reverb_on: true, reverb_mix_db: -40 };
  const k = knobsFromSettings(quiet);
  assert.ok(k.echo > 0 && k.echo < 0.05);
  assert.ok(k.reverb > 0 && k.reverb < 0.05);
  const off = knobsFromSettings({ ...DEFAULTS, throw_db: 0, reverb_mix_db: 0 });
  assert.equal(off.echo, 0);
  assert.equal(off.reverb, 0);
});

test("values stay inside the settings ranges and on their steps, whatever the knob gets", () => {
  for (const id of IDS) {
    for (let i = -50; i <= 150; i += 1) {
      const s = settingsFromKnob(DEFAULTS, id, i / 100);
      for (const [key, [low, high, step]] of Object.entries(RANGES)) {
        const v = s[key];
        assert.ok(v >= low && v <= high, `${id} at ${i / 100}: ${key}=${v}`);
        assert.ok(Math.abs(v / step - Math.round(v / step)) < 1e-9, `${id} at ${i / 100}: ${key}=${v} off its step`);
      }
    }
  }
});

test("a knob leaves every other key and the settings it was given alone", () => {
  const before = structuredClone(DEFAULTS);
  const s = settingsFromKnob(DEFAULTS, "clean", 0.5);
  assert.deepEqual(DEFAULTS, before);
  const { comp_threshold_db, drive_db, ...rest } = s;
  const { comp_threshold_db: _a, drive_db: _b, ...restBefore } = before;
  assert.deepEqual(rest, restBefore);
});

test("a value that is not a number is refused", () => {
  assert.throws(() => settingsFromKnob(DEFAULTS, "vocal", Number.NaN), RangeError);
  assert.throws(() => settingsFromKnob(DEFAULTS, "vocal", Number.POSITIVE_INFINITY), RangeError);
});

test("settings missing a key the knobs read are an error, not a guess", () => {
  const { vocal_db, ...partial } = DEFAULTS;
  assert.throws(() => knobsFromSettings(partial), /vocal_db/);
});

test("each knob says its value in words or decibels", () => {
  const text = (patch, knob) => knobText({ ...DEFAULTS, ...patch }, knob);
  assert.equal(text({ vocal_db: -4 }, "vocal"), `−4${NB}дБ`);
  assert.equal(text({ vocal_db: -4.5 }, "vocal"), `−4.5${NB}дБ`);
  assert.equal(text({ vocal_db: 0 }, "vocal"), `0${NB}дБ`);
  assert.equal(knobText(settingsFromKnob(DEFAULTS, "clean", 0), "clean"), "мягко");
  assert.equal(knobText(settingsFromKnob(DEFAULTS, "clean", 0.5), "clean"), "средне");
  assert.equal(knobText(settingsFromKnob(DEFAULTS, "clean", 1), "clean"), "жёстко");
  assert.equal(text({}, "echo"), "выкл");
  assert.equal(text({ throw_on: true, throw_db: -12 }, "echo"), `−12${NB}дБ`);
  assert.equal(text({}, "reverb"), "выкл");
  assert.equal(text({ reverb_on: true, reverb_mix_db: -20 }, "reverb"), `−20${NB}дБ`);
  assert.equal(text({ hard_tune: 0 }, "tune"), "выкл");
  assert.equal(text({ hard_tune: 50 }, "tune"), `50${NB}%`);
  assert.equal(text({ beat_db: -3 }, "beat"), `−3${NB}дБ`);
  assert.equal(text({ bass_drive_db: 0 }, "bass"), "выкл");
  assert.equal(text({ bass_drive_db: 15 }, "bass"), `15${NB}дБ`);
});
