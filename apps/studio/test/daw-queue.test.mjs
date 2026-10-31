// The editor's save queue: edits wait a quiet moment, one save is out at a time, the newest
// edit wins, and a flush waits until the server has the newest one.
import assert from "node:assert/strict";
import { test } from "node:test";

import { SaveQueue } from "../src/daw/queue.ts";

function clock() {
  const timers = new Map();
  let next = 1;
  return {
    timer: {
      set: (fn, ms) => {
        const id = next++;
        timers.set(id, { fn, ms });
        return id;
      },
      clear: (id) => timers.delete(id),
    },
    // runs the timers due now
    fire() {
      const due = [...timers.entries()];
      timers.clear();
      for (const [, t] of due) t.fn();
      return due.length;
    },
    get waiting() {
      return timers.size;
    },
    last: () => [...timers.values()].at(-1),
  };
}

function server() {
  const sent = [];
  const open = [];
  return {
    sent,
    send: (v) => {
      sent.push(v);
      return new Promise((resolve, reject) => open.push({ resolve, reject }));
    },
    // answers the oldest save still out
    answer(ok = true) {
      const o = open.shift();
      if (ok) o.resolve();
      else o.reject(new Error("refused"));
    },
    get out() {
      return open.length;
    },
  };
}

const tick = () => new Promise((r) => setImmediate(r));

test("edits in a burst are one save of the last, after the delay", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push(1);
  q.push(2);
  q.push(3);
  assert.equal(c.waiting, 1);
  assert.equal(c.last().ms, 600);
  assert.deepEqual(s.sent, []);
  assert.equal(q.state, "waiting");
  c.fire();
  assert.deepEqual(s.sent, [3]);
  assert.equal(q.state, "saving");
  s.answer();
  await tick();
  assert.equal(q.state, "saved");
  assert.equal(q.pending, false);
});

test("while a save is out no second one goes; the newest waits for it and wins", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  c.fire();
  q.push("b");
  q.push("c");
  c.fire();
  assert.equal(s.out, 1);
  assert.deepEqual(s.sent, ["a"]);
  s.answer();
  await tick();
  assert.deepEqual(s.sent, ["a", "c"]);
  s.answer();
  await tick();
  assert.equal(q.state, "saved");
});

test("an edit during a save whose delay has not run out still waits for its delay", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  c.fire();
  q.push("b");
  s.answer();
  await tick();
  assert.deepEqual(s.sent, ["a"]);
  assert.equal(c.waiting, 1);
  c.fire();
  assert.deepEqual(s.sent, ["a", "b"]);
});

test("flush sends at once and resolves true when the newest is saved", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  const done = q.flush();
  assert.equal(c.waiting, 0);
  assert.deepEqual(s.sent, ["a"]);
  s.answer();
  assert.equal(await done, true);
});

test("flush during a save waits for it and then sends the newest", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  c.fire();
  q.push("b");
  let settled = null;
  const done = q.flush().then((v) => (settled = v));
  await tick();
  assert.equal(settled, null);
  s.answer();
  await tick();
  assert.deepEqual(s.sent, ["a", "b"]);
  assert.equal(settled, null);
  s.answer();
  await done;
  assert.equal(settled, true);
});

test("flush with nothing to save resolves true and sends nothing", async () => {
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: clock().timer });
  assert.equal(await q.flush(), true);
  assert.deepEqual(s.sent, []);
});

test("a refused save keeps the edit pending: flush tries it again and reports the refusal", async () => {
  const c = clock();
  const s = server();
  const states = [];
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer, onState: (st) => states.push(st) });
  q.push("a");
  c.fire();
  s.answer(false);
  await tick();
  assert.equal(q.state, "failed");
  assert.equal(q.pending, true);
  assert.equal(c.waiting, 0);
  const done = q.flush();
  assert.deepEqual(s.sent, ["a", "a"]);
  s.answer(false);
  assert.equal(await done, false);
  assert.ok(states.includes("failed"));
});

test("a refused save with a newer edit behind it sends the newer one, not the old", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  c.fire();
  q.push("b");
  c.fire();
  s.answer(false);
  await tick();
  assert.deepEqual(s.sent, ["a", "b"]);
});

test("a disposed queue sends nothing more", async () => {
  const c = clock();
  const s = server();
  const q = new SaveQueue({ send: s.send, delay: 600, timer: c.timer });
  q.push("a");
  q.dispose();
  assert.equal(c.waiting, 0);
  q.push("b");
  assert.equal(c.waiting, 0);
  assert.deepEqual(s.sent, []);
});
