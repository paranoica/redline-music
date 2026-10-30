// The page's side of the worklet protocol against the engine, through a fake port pair.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import createRedlineDsp from "../src/engine/redline_dsp.js";
import { EngineClient } from "../src/engine/client.ts";
import { Engine } from "../src/engine/core.ts";
import { catalogueOf } from "../src/core/diff.ts";
import { buildRacks } from "../src/core/racks.ts";

const dsp = await createRedlineDsp();
const effects = JSON.parse(dsp.UTF8ToString(dsp._effects_json()));
const fixture = JSON.parse(readFileSync(new URL("./fixtures/racks-python.json", import.meta.url), "utf8"));
const RATE = 48000;

function pair() {
  const sent = [];
  const page = { onmessage: null, postMessage: (m) => { sent.push(m); engine.handle(structuredClone(m)); } };
  const engine = new Engine(dsp, RATE, (m) => page.onmessage?.({ data: structuredClone(m) }));
  return { page, engine, sent };
}

function render(engine, frames) {
  const l = new Float32Array(128);
  const r = new Float32Array(128);
  for (let at = 0; at < frames; at += 128) engine.render(l, r);
}

test("the first racks go over whole; a moved number goes over as a set; a switch rebuilds", () => {
  const { page, engine, sent } = pair();
  const client = new EngineClient(page, catalogueOf(effects));
  const acks = [];
  client.onRack = (track, latency) => acks.push([track, latency]);
  const frames = RATE;
  const silent = new Float32Array(2 * frames);
  client.loadStems(frames, silent.slice(), silent.slice(), silent.slice());
  const base = fixture.cases.defaults.settings;
  client.setRacks(buildRacks(base, fixture.numbers), []);
  assert.deepEqual(sent.filter((m) => m.type === "rack").map((m) => [m.track, m.version]), [["vocal", 1], ["beat", 1], ["master", 1]]);
  render(engine, 512);
  assert.deepEqual(acks.map(([t]) => t).sort(), ["beat", "master", "vocal"]);

  sent.length = 0;
  client.setRacks(buildRacks({ ...base, comp_threshold_db: -30 }, fixture.numbers), []);
  assert.deepEqual(sent, [{ type: "set", track: "vocal", version: 1, slot: 1, param: "threshold", value: -30 }]);

  sent.length = 0;
  client.setRacks(buildRacks({ ...base, comp_threshold_db: -30 }, fixture.numbers), []);
  assert.deepEqual(sent, [], "nothing changed, nothing sent");

  client.setRacks(buildRacks({ ...base, comp_threshold_db: -30, doubles_on: false }, fixture.numbers), []);
  assert.deepEqual(sent.map((m) => [m.type, m.track, m.version]), [["rack", "vocal", 2]]);
  assert.equal(JSON.parse(sent[0].json).effects.length, 10);
});

test("lanes that change alone go over without a rebuild", () => {
  const { page, sent } = pair();
  const client = new EngineClient(page, catalogueOf(effects));
  const racks = buildRacks(fixture.cases.defaults.settings, fixture.numbers);
  client.setRacks(racks, []);
  sent.length = 0;
  const lanes = [{ slot: 0, param: "db", points: [[0, 0], [1, -6]] }];
  client.setRacks(racks, lanes);
  assert.deepEqual(sent, [{ type: "lanes", version: 1, lanes }]);
});

test("levels and errors reach the page's callbacks", () => {
  const { page, engine } = pair();
  const client = new EngineClient(page, catalogueOf(effects));
  const levels = [];
  const errors = [];
  client.onLevels = (l) => levels.push(l);
  client.onError = (e) => errors.push(e);
  const silent = new Float32Array(2 * RATE);
  client.loadStems(RATE, silent.slice(), silent.slice(), silent.slice());
  client.setRacks({ vocal: [{ effect: "nope", on: true, params: {} }], beat: [], master: [] }, []);
  client.play(true);
  render(engine, RATE / 4);
  assert.ok(levels.length > 3);
  assert.equal(errors.length, 1);
  assert.match(errors[0].message, /nope/);
});

test("a stopped engine is told to the page once, as fatal, not as a rack's error", () => {
  const port = { onmessage: null, postMessage: () => {} };
  const client = new EngineClient(port, catalogueOf(effects));
  const fatal = [];
  const errors = [];
  client.onFatal = (message) => fatal.push(message);
  client.onError = (e) => errors.push(e);
  port.onmessage({ data: { type: "error", fatal: true, message: "Aborted(OOM)" } });
  client.fail("the audio processor threw");
  port.onmessage({ data: { type: "error", fatal: true, message: "again" } });
  assert.deepEqual(fatal, ["Aborted(OOM)"]);
  assert.deepEqual(errors, []);
});

// a client on an engine that has played `ahead` frames of a four-second track
function playing(ahead) {
  const { page, engine } = pair();
  const client = new EngineClient(page, catalogueOf(effects));
  const frames = 4 * RATE;
  const tone = new Float32Array(2 * frames).fill(0.25);
  client.loadStems(frames, tone.slice(), tone.slice(), tone.slice());
  client.setRacks({ vocal: [], beat: [], master: [] }, []);
  client.play(true);
  render(engine, ahead);
  const shown = [];
  client.onLevels = (l) => shown.push(l.frame);
  return { client, engine, shown };
}

test("after a seek while playing the page is shown no report of the old spot", () => {
  // reports come every 1600 frames: for some of these one falls in the fade before the seek
  for (let k = 0; k < 13; k++) {
    const { client, engine, shown } = playing(9600 + 128 * k);
    client.seek(96000);
    render(engine, RATE / 4);
    assert.ok(shown.length > 0 && shown.every((f) => f >= 96000), `ahead ${9600 + 128 * k}: ${shown}`);
  }
});

test("after a loop that moves the position while playing the page is shown no report of the old spot", () => {
  for (let k = 0; k < 13; k++) {
    const { client, engine, shown } = playing(9600 + 128 * k);
    client.loop(true, 48000, 72000);
    render(engine, RATE / 4);
    assert.ok(shown.length > 0 && shown.every((f) => f >= 48000 && f < 72000), `ahead ${9600 + 128 * k}: ${shown}`);
  }
});

test("a loop that leaves the position where it is drops no report of it", () => {
  const { client, engine, shown } = playing(9600);
  client.loop(true, 0, 96000);
  render(engine, 9600);
  assert.ok(shown.length >= 5 && shown.every((f) => f > 9600 - 2000 && f < 2 * 9600), `${shown}`);
});
