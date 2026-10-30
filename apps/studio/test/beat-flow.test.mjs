// The beat editor's message flow: what reaches the engine and the check, in what order.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Checker, FrameLoop, Latest } from "../src/beat/flow.ts";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((ok, no) => {
    resolve = ok;
    reject = no;
  });
  return { promise, resolve, reject };
}

const settle = () => new Promise((ok) => setImmediate(ok));

// a sender whose calls finish only when the test says so
function manualSender() {
  const sent = [];
  const calls = [];
  const latest = new Latest((value) => {
    sent.push(value);
    const d = deferred();
    calls.push(d);
    return d.promise;
  });
  return { latest, sent, calls };
}

test("a rack edit replaces the live text waiting behind one in flight", async () => {
  const { latest, sent, calls } = manualSender();
  latest.push("old score");
  latest.push("edit with the old rack");
  latest.replacePending("score with the new rack");
  calls[0].resolve();
  await settle();
  assert.deepEqual(sent, ["old score", "score with the new rack"]);
});

test("replacing with nothing waiting sends nothing more", async () => {
  const { latest, sent, calls } = manualSender();
  latest.replacePending("unasked");
  assert.deepEqual(sent, []);
  latest.push("a");
  latest.replacePending("b");
  calls[0].resolve();
  await settle();
  assert.deepEqual(sent, ["a"]);
});

test("the newest pushed value still wins over older waiting ones", async () => {
  const { latest, sent, calls } = manualSender();
  latest.push("a");
  latest.push("b");
  latest.push("c");
  calls[0].resolve();
  await settle();
  assert.deepEqual(sent, ["a", "c"]);
});

// a checker with timers the test fires and checks it answers
function manualChecker() {
  const timers = new Map();
  let nextId = 1;
  const runs = [];
  const applied = [];
  const checker = new Checker({
    run: (score) => {
      const d = deferred();
      runs.push({ score, d });
      return d.promise;
    },
    apply: (score, outcome) => applied.push({ score, outcome }),
    setTimer: (fn) => {
      const id = nextId++;
      timers.set(id, fn);
      return id;
    },
    clearTimer: (id) => timers.delete(id),
  });
  const fire = () => {
    const all = [...timers.values()];
    timers.clear();
    for (const fn of all) fn();
  };
  return { checker, runs, applied, fire };
}

test("a check in flight for an older score is dropped once a newer one is scheduled", async () => {
  const { checker, runs, applied, fire } = manualChecker();
  let score = "A";
  checker.schedule(() => score, 0);
  fire();
  score = "B";
  checker.schedule(() => score, 300);
  runs[0].d.resolve({ errors: ["for A"] });
  await settle();
  assert.deepEqual(applied, []);
  fire();
  runs[1].d.resolve({ errors: [] });
  await settle();
  assert.deepEqual(applied, [{ score: "B", outcome: { ok: true, value: { errors: [] } } }]);
});

test("a failed check of the current score is applied with its error", async () => {
  const { checker, runs, applied, fire } = manualChecker();
  checker.schedule(() => "A", 0);
  fire();
  const error = new Error("down");
  runs[0].d.reject(error);
  await settle();
  assert.deepEqual(applied, [{ score: "A", outcome: { ok: false, error } }]);
});

test("a reply lands when nothing newer was scheduled while it ran", async () => {
  const { checker, runs, applied, fire } = manualChecker();
  checker.schedule(() => "A", 0);
  fire();
  runs[0].d.resolve({ errors: ["e"] });
  await settle();
  assert.equal(applied.length, 1);
  assert.equal(applied[0].score, "A");
});

// a fake requestAnimationFrame: queued callbacks run when the test steps a frame
function fakeRaf() {
  let queue = [];
  let id = 0;
  return {
    raf: (fn) => {
      queue.push(fn);
      return ++id;
    },
    pending: () => queue.length,
    step: () => {
      const now = queue;
      queue = [];
      for (const fn of now) fn();
    },
  };
}

test("starting the loop while it runs keeps a single loop", () => {
  const r = fakeRaf();
  const loop = new FrameLoop(r.raf);
  let frames = 0;
  const frame = () => {
    frames++;
    return true;
  };
  loop.start(frame);
  loop.start(frame);
  loop.start(frame);
  assert.equal(r.pending(), 1);
  r.step();
  assert.equal(r.pending(), 1);
});

test("a loop that ends can be started again", () => {
  const r = fakeRaf();
  const loop = new FrameLoop(r.raf);
  let playing = true;
  const frame = () => playing;
  loop.start(frame);
  playing = false;
  r.step();
  assert.equal(r.pending(), 0);
  playing = true;
  loop.start(frame);
  assert.equal(r.pending(), 1);
});

test("a start from inside a running frame keeps a single loop", async () => {
  const { FrameLoop } = await import("../src/beat/flow.ts");
  const queued = [];
  const loop = new FrameLoop((fn) => queued.push(fn));
  let frames = 0;
  let inner = 0;
  loop.start(() => {
    frames++;
    if (frames === 1) loop.start(() => ++inner < 100);
    return frames < 3;
  });
  // bounded: a second loop would keep queueing frames
  for (let i = 0; i < 10 && queued.length; i++) queued.shift()();
  assert.equal(inner, 0);
  assert.equal(frames, 3);
  assert.equal(queued.length, 0);
});
