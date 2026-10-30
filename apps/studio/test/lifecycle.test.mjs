// What a mounted app owns: listeners, timers and frames go with one dispose, and the
// keyboard belongs to the active surface only.
import assert from "node:assert/strict";
import { test } from "node:test";

import { KeySurface, Scope } from "../src/core/lifecycle.ts";

function fakeDocument() {
  const listeners = new Map();
  return {
    add: (type, fn) => listeners.set(type, [...(listeners.get(type) ?? []), fn]),
    addEventListener(type, fn) {
      this.add(type, fn);
    },
    removeEventListener(type, fn) {
      listeners.set(type, (listeners.get(type) ?? []).filter((f) => f !== fn));
    },
    count: () => [...listeners.values()].reduce((n, l) => n + l.length, 0),
    fire: (type, event) => (listeners.get(type) ?? []).slice().forEach((fn) => fn(event)),
  };
}

function fakeClock() {
  const timers = new Map();
  const frames = new Map();
  let next = 0;
  return {
    timers,
    frames,
    setTimeout: (fn) => (timers.set(++next, fn), next),
    clearTimeout: (id) => timers.delete(id),
    setInterval: (fn) => (timers.set(++next, fn), next),
    clearInterval: (id) => timers.delete(id),
    requestAnimationFrame: (fn) => (frames.set(++next, fn), next),
    cancelAnimationFrame: (id) => frames.delete(id),
  };
}

test("dispose removes every listener the scope added", () => {
  const doc = fakeDocument();
  const scope = new Scope(fakeClock());
  scope.listen(doc, "keydown", () => {});
  scope.listen(doc, "pointerdown", () => {}, true);
  assert.equal(doc.count(), 2);
  scope.dispose();
  assert.equal(doc.count(), 0);
  assert.equal(scope.size, 0);
});

test("dispose clears timers, intervals and frames that are still waiting", () => {
  const clock = fakeClock();
  const scope = new Scope(clock);
  scope.setTimeout(() => {}, 10);
  scope.setInterval(() => {}, 10);
  scope.requestFrame(() => {});
  assert.equal(clock.timers.size + clock.frames.size, 3);
  scope.dispose();
  assert.equal(clock.timers.size + clock.frames.size, 0);
});

test("a timer that fired or was cleared is no longer held", () => {
  const clock = fakeClock();
  const scope = new Scope(clock);
  const a = scope.setTimeout(() => {}, 10);
  const b = scope.requestFrame(() => {});
  assert.equal(scope.size, 2);
  scope.clearTimeout(a);
  assert.equal(scope.size, 1);
  clock.frames.get(2)();
  assert.equal(scope.size, 0);
  assert.equal(typeof b, "number");
});

test("after dispose nothing new is started", () => {
  const doc = fakeDocument();
  const clock = fakeClock();
  const scope = new Scope(clock);
  scope.dispose();
  scope.listen(doc, "keydown", () => {});
  assert.equal(scope.setTimeout(() => {}, 1), 0);
  assert.equal(scope.requestFrame(() => {}), 0);
  assert.equal(doc.count(), 0);
  assert.equal(clock.timers.size + clock.frames.size, 0);
  assert.equal(scope.closed, true);
});

test("a cleanup added to a closed scope runs at once; dispose twice runs each once", () => {
  const scope = new Scope(fakeClock());
  let runs = 0;
  scope.add(() => runs++);
  scope.dispose();
  scope.dispose();
  assert.equal(runs, 1);
  scope.add(() => runs++);
  assert.equal(runs, 2);
});

test("sleep resolves on dispose instead of hanging", async () => {
  const scope = new Scope(fakeClock());
  const slept = scope.sleep(60_000);
  scope.dispose();
  await slept;
});

test("a surface gets keys only while active", () => {
  const doc = fakeDocument();
  const seen = [];
  const surface = new KeySurface(doc, (e) => seen.push(e));
  assert.equal(doc.count(), 0);
  surface.setActive(true);
  doc.fire("keydown", 1);
  surface.setActive(true);
  assert.equal(doc.count(), 1);
  surface.setActive(false);
  doc.fire("keydown", 2);
  assert.deepEqual(seen, [1]);
  assert.equal(doc.count(), 0);
});

test("with two surfaces on a document one space reaches only the active one", () => {
  const doc = fakeDocument();
  const mix = [];
  const beat = [];
  const a = new KeySurface(doc, (e) => mix.push(e));
  const b = new KeySurface(doc, (e) => beat.push(e));
  a.setActive(true);
  doc.fire("keydown", "space");
  a.setActive(false);
  b.setActive(true);
  doc.fire("keydown", "space");
  assert.deepEqual(mix, ["space"]);
  assert.deepEqual(beat, ["space"]);
  b.setActive(false);
  assert.equal(doc.count(), 0);
});
