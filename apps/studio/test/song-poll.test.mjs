// The poll scheduler: it runs only while wanted and visible, and never holds two timers.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Poller } from "../src/song/poll.ts";

function fakeClock() {
  let next = 1;
  const timers = new Map();
  return {
    timers,
    setTimeout(fn) {
      const id = next++;
      timers.set(id, fn);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    fire() {
      const [id, fn] = [...timers][0];
      timers.delete(id);
      fn();
    },
  };
}
const settle = () => new Promise((r) => setImmediate(r));

test("nothing runs until the station is shown", () => {
  const clock = fakeClock();
  new Poller({ ms: 50, tick: () => {}, clock });
  assert.equal(clock.timers.size, 0);
});

test("shown and visible: ticks follow one another with one timer", async () => {
  const clock = fakeClock();
  let ticks = 0;
  const p = new Poller({ ms: 50, tick: () => void ticks++, clock });
  p.setWanted(true);
  p.setWanted(true);
  p.setVisible(true);
  assert.equal(clock.timers.size, 1);
  clock.fire();
  await settle();
  assert.equal(ticks, 1);
  assert.equal(clock.timers.size, 1);
});

test("a hidden tab or another station stops it, and coming back restarts it once", async () => {
  const clock = fakeClock();
  let ticks = 0;
  const p = new Poller({ ms: 50, tick: () => void ticks++, clock });
  p.setWanted(true);
  p.setVisible(false);
  assert.equal(clock.timers.size, 0);
  p.setVisible(false);
  p.setVisible(true);
  p.setVisible(true);
  assert.equal(clock.timers.size, 1);
  p.setWanted(false);
  assert.equal(clock.timers.size, 0);
  p.setWanted(true);
  assert.equal(clock.timers.size, 1);
  clock.fire();
  await settle();
  assert.equal(ticks, 1);
});

test("stopping and starting during a slow tick never makes a second timer", async () => {
  const clock = fakeClock();
  let release;
  let ticks = 0;
  const p = new Poller({
    ms: 50,
    tick: () => {
      ticks++;
      return new Promise((r) => (release = r));
    },
    clock,
  });
  p.setWanted(true);
  clock.fire();
  await settle();
  p.setWanted(false);
  p.setWanted(true);
  assert.equal(clock.timers.size, 0);
  release();
  await settle();
  assert.equal(clock.timers.size, 1);
  assert.equal(ticks, 1);
});

test("a tick finishing after the poll stopped schedules nothing; a failing tick is retried", async () => {
  const clock = fakeClock();
  let release;
  const p = new Poller({ ms: 50, tick: () => new Promise((r) => (release = r)), clock });
  p.setWanted(true);
  clock.fire();
  await settle();
  p.setVisible(false);
  release();
  await settle();
  assert.equal(clock.timers.size, 0);

  const bad = new Poller({
    ms: 50,
    tick: () => {
      throw new Error("down");
    },
    clock,
  });
  bad.setWanted(true);
  clock.fire();
  await settle();
  assert.equal(clock.timers.size, 1);
  bad.dispose();
  assert.equal(clock.timers.size, 0);
});

test("the interval can change: the next wait uses it, and a shorter one replaces a longer wait", async () => {
  const waits = [];
  let next = 1;
  const timers = new Map();
  const clock = {
    setTimeout(fn, ms) {
      const id = next++;
      timers.set(id, fn);
      waits.push(ms);
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
  };
  const fire = () => {
    const [id, fn] = [...timers][0];
    timers.delete(id);
    fn();
  };
  let ticks = 0;
  const p = new Poller({ ms: 1000, tick: () => void ticks++, clock });
  p.setWanted(true);
  fire();
  await settle();
  assert.equal(ticks, 1);
  assert.equal(waits.at(-1), 1000);
  p.setMs(50);
  assert.equal(timers.size, 1);
  assert.equal(waits.at(-1), 50);
  fire();
  await settle();
  assert.equal(waits.at(-1), 50);
  // a longer interval waits for the timer already set
  p.setMs(1000);
  assert.equal(timers.size, 1);
  assert.equal(waits.at(-1), 50);
  fire();
  await settle();
  assert.equal(waits.at(-1), 1000);
  // the same interval again changes nothing
  const set = waits.length;
  p.setMs(1000);
  assert.equal(waits.length, set);
});
