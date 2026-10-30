// Review fixes of the song page: the stale body, refusal words, finished jobs, the surface host.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, createSongApi, staleSong } from "../src/song/api.ts";
import { recPhase } from "../src/song/record.ts";
import { finishedKinds, refusalText, SurfaceHost } from "../src/song/steps.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  delete globalThis.document;
});

function fakeDocument() {
  const make = (tag) => ({
    tag,
    children: [],
    parent: null,
    className: "",
    style: { setProperty() {} },
    setAttribute() {},
    append(...kids) {
      for (const k of kids) this.adopt(k);
    },
    replaceChildren(...kids) {
      for (const k of this.children) k.parent = null;
      this.children = [];
      this.append(...kids);
    },
    adopt(k) {
      if (typeof k !== "object") return;
      if (k.parent) k.parent.children = k.parent.children.filter((c) => c !== k);
      k.parent = this;
      this.children.push(k);
    },
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    },
  });
  return { createElement: make };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));
const job = (kind, state, finished = null) => ({ kind, state, reason: null, started: 1, finished });

test("a 412 body carries song and stations at the top level", () => {
  const body = { error: "stale", code: "stale", song: { name: "s", rev: 9 }, stations: { beat: {} } };
  assert.deepEqual(staleSong(body), { song: { name: "s", rev: 9 }, stations: { beat: {} } });
  assert.equal(staleSong({ error: "stale", code: "stale", song: { rev: 9 } }), null);
  assert.equal(staleSong({ code: "stale", stations: {} }), null);
  assert.equal(staleSong({}), null);
});

test("a take plays over the beat on request, alone otherwise", async () => {
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(new Blob(["x"]), { status: 200 });
  };
  const api = createSongApi({ token: "t" });
  await api.takeAudio("take-04.wav");
  await api.takeAudio("take-04.wav", true);
  assert.deepEqual(urls, ["/api/takes/audio?name=take-04.wav", "/api/takes/audio?name=take-04.wav&with=beat"]);
});

test("a refusal is worded by its code", () => {
  const jobs = [job("separate", "running")];
  const say = (code, message = "server text") => refusalText(new ApiError(409, { code, error: message }), jobs);
  assert.match(say("busy"), /отделение голоса/);
  assert.equal(say("take_running"), "Идёт дубль: дождись конца.");
  for (const code of ["no_voice", "no_takes", "no_beat_seed", "not_ready", "out_of_sync", "closed"]) {
    const text = say(code);
    assert.ok(text.length > 10 && !/занято/.test(text) && text !== "server text", code);
  }
  assert.equal(say("something_new", "own words"), "own words");
  assert.notEqual(say("no_voice"), say("no_takes"));
});

test("finished jobs: active to inactive, or a new finish stamp; not an unchanged one", () => {
  const before = [job("rank", "running"), job("auto", "done", 5), job("separate", "done", 7)];
  const next = [job("rank", "done", 9), job("auto", "done", 5), job("separate", "done", 7), job("beat_render", "done", 11)];
  assert.deepEqual(finishedKinds(before, next).sort(), ["beat_render", "rank"]);
  assert.deepEqual(finishedKinds([job("auto", "done", 5)], [job("auto", "done", 8)]), ["auto"]);
  assert.deepEqual(finishedKinds([job("auto", "running")], [job("auto", "running")]), []);
  assert.deepEqual(finishedKinds([], [job("auto", "queued")]), []);
});

test("calibration is its own recording phase, not an opening one", () => {
  const snap = { stage: "calibrating", out_of_sync: false, stopping: false, recording: false };
  assert.equal(recPhase(snap), "calibrating");
  assert.equal(recPhase({ ...snap, stage: "analysing" }), "opening");
});

test("a surface asked to be active while it mounts ends up active", async () => {
  globalThis.document = fakeDocument();
  const seen = [];
  let release;
  const mount = (_root, options) =>
    new Promise((resolve) => {
      seen.push(["mount", options.active]);
      release = () => resolve({ dispose() {}, setActive: (on) => seen.push(["active", on]) });
    });
  const host = new SurfaceHost();
  host.setActive(false);
  const shown = host.show("beat", mount, document.createElement("div"), "/b", "t");
  host.setActive(true);
  await settle();
  release();
  await shown;
  assert.deepEqual(seen.at(-1), ["active", true]);
});

test("a second surface mounts only after the first one has landed and been disposed", async () => {
  globalThis.document = fakeDocument();
  const log = [];
  let releaseFirst;
  const first = () =>
    new Promise((resolve) => {
      log.push("mount beat");
      releaseFirst = () => resolve({ dispose: async () => void log.push("dispose beat") });
    });
  const second = async () => {
    log.push("mount mix");
    return { dispose() {} };
  };
  const host = new SurfaceHost();
  const a = host.show("beat", first, document.createElement("div"), "/b", "t");
  await settle();
  const b = host.show("mix", second, document.createElement("div"), "/m", "t");
  await settle();
  assert.deepEqual(log, ["mount beat"]);
  releaseFirst();
  await Promise.all([a, b]);
  assert.deepEqual(log, ["mount beat", "dispose beat", "mount mix"]);
  assert.equal(host.kind, "mix");
});

test("a parked surface stays mounted, silent, and comes back to a slot; unsaved work is reported", async () => {
  globalThis.document = fakeDocument();
  let dirty = true;
  let disposed = 0;
  const states = [];
  const mount = async () => ({ dispose() { disposed += 1; }, dirty: () => dirty, setActive: (on) => states.push(on) });
  const holder = document.createElement("div");
  const host = new SurfaceHost(holder);
  const slot = document.createElement("div");
  await host.show("beat", mount, slot, "/b", "t");
  host.park();
  assert.equal(disposed, 0);
  assert.equal(slot.children.length, 0);
  assert.equal(holder.children.length, 1);
  assert.equal(states.at(-1), false);
  assert.equal(host.kind, "beat");
  assert.equal(host.unsavedFor("mix"), "beat");
  assert.equal(host.unsavedFor("beat"), null);
  dirty = false;
  assert.equal(host.unsavedFor("mix"), null);
  const again = document.createElement("div");
  assert.equal(host.place("beat", again), true);
  assert.equal(again.children.length, 1);
  assert.equal(holder.children.length, 0);
});

test("a parked surface holds its sound and lets it go when it comes back", async () => {
  globalThis.document = fakeDocument();
  const held = [];
  const mount = async () => ({ dispose() {}, hold: (on) => held.push(on) });
  const host = new SurfaceHost(document.createElement("div"));
  await host.show("mix", mount, document.createElement("div"), "/m", "t");
  assert.deepEqual(held, []);
  host.park();
  assert.deepEqual(held, [true]);
  host.place("mix", document.createElement("div"));
  assert.deepEqual(held, [true, false]);
  host.place("mix", document.createElement("div"));
  assert.deepEqual(held, [true, false]);
});
