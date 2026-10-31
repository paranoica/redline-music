// A flip's working copy: the draft saved over its revision, one history, and the commit that
// renders, waits and uses.
import assert from "node:assert/strict";
import { test } from "node:test";

import { ApiError } from "../src/song/api.ts";
import { FlipDraft, flipLayout, keyOf, withBlocks } from "../src/daw/flipdraft.ts";
import { copyBlockTo } from "../src/flip/model.ts";

const settle = async () => {
  for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
};

function clock() {
  const timers = new Map();
  let next = 1;
  return {
    set: (fn) => {
      const id = next++;
      timers.set(id, fn);
      return id;
    },
    clear: (id) => timers.delete(id),
    fire() {
      const due = [...timers.values()];
      timers.clear();
      for (const fn of due) fn();
    },
  };
}

const doc = (label = "a") => ({
  format: 1,
  source: "input/x.wav",
  grid: { bpm: 120, downbeat_s: 0, swing: 50 },
  bass808: { mode: "original", params: {} },
  hats: { mode: "original", remove: [], patterns: [] },
  gains: { drums: 0, other: 0, bass: 0 },
  arrangement: [{ label, from_bar: 0, to_bar: 4 }],
  effects: [],
});
const state = (d, rev = 3) => ({ draft: d, draft_rev: rev });
const rename = (label) => (d) => ({ ...d, arrangement: [{ ...d.arrangement[0], label }] });
const err = (status, code) => new ApiError(status, { error: code, code });

// the server: scripted answers, every call noted
function setup({ takes = 0, answers = {}, recording = () => false, confirm = true } = {}) {
  const log = [];
  const seen = { says: [], reloads: 0, confirms: [] };
  let rev = 3;
  let job = { kind: "flip_render", state: "done", reason: null, started: 1, finished: 10 };
  let renders = 0;
  let songReads = 0;
  let saved = null;
  const api = {
    state: async () => state(answers.serverDoc ?? saved ?? doc("server"), rev),
    saveDraft: async (d, base) => {
      log.push(["draft", base, d.arrangement[0].label]);
      if (answers.draft) await answers.draft(base);
      rev += 1;
      saved = d;
      return rev;
    },
    render: async (base) => {
      log.push(["render", base]);
      if (answers.render) await answers.render(base);
      if (answers.strict && base !== rev) throw err(409, "stale_base");
      renders += 1;
      job = { ...job, state: "running", finished: null };
      return 10 + renders;
    },
    jobs: async () => {
      if (answers.jobs) await answers.jobs();
      const now = job;
      if (job.state === "running") job = { ...job, state: answers.fail ? "failed" : "done", reason: answers.fail ? "boom" : null, finished: 20 + renders };
      return [now];
    },
    rev: async () => 40 + songReads++,
    use: async (n, r) => {
      log.push(["use", n, r]);
      if (answers.use) await answers.use(n, r);
    },
    beacon: (d, base) => log.push(["beacon", base]),
  };
  const timer = clock();
  const draft = new FlipDraft({
    api,
    takes: () => takes,
    confirm: (t) => (seen.confirms.push(t), confirm),
    recording,
    reload: async () => void seen.reloads++,
    changed: () => {},
    say: (t) => seen.says.push(t),
    timer,
    sleep: async () => {},
  });
  draft.adopt(state(doc(), rev), false);
  return { draft, log, seen, timer };
}

test("an edit is saved after a quiet moment over the revision that was read", async () => {
  const { draft, log, timer } = setup();
  assert.equal(draft.apply(rename("b")), true);
  assert.deepEqual(log, []);
  assert.equal(draft.dirty, true);
  timer.fire();
  await settle();
  assert.deepEqual(log, [["draft", 3, "b"]]);
  // the next save is over the revision the first one answered
  draft.apply(rename("c"));
  timer.fire();
  await settle();
  assert.deepEqual(log[1], ["draft", 4, "c"]);
});

test("a loaded draft gets one hat pattern per block", () => {
  const { draft } = setup();
  assert.deepEqual(draft.doc.hats.patterns, ["8ths"]);
});

test("an op that changes nothing or refuses is no edit, and a refusal says why", () => {
  const { draft, seen } = setup();
  assert.equal(draft.apply((d) => d), false);
  assert.equal(draft.apply(() => null), false);
  assert.equal(draft.apply(() => ({ ok: false, reason: "no" })), false);
  assert.deepEqual(seen.says, ["no"]);
  assert.equal(draft.dirty, false);
});

test("undo and redo walk one history", () => {
  const { draft } = setup();
  draft.apply(rename("b"));
  draft.apply(rename("c"));
  assert.equal(draft.undo(false), true);
  assert.equal(draft.doc.arrangement[0].label, "b");
  assert.equal(draft.undo(false), true);
  assert.equal(draft.doc.arrangement[0].label, "a");
  assert.equal(draft.undo(false), false);
  assert.equal(draft.undo(true), true);
  assert.equal(draft.doc.arrangement[0].label, "b");
});

test("a block copy through the model ops lands in the doc with its hat pattern", () => {
  const { draft } = setup();
  draft.apply((d) => withBlocks(d, copyBlockTo(flipLayout(d), 0, 1)));
  assert.equal(draft.doc.arrangement.length, 2);
  assert.equal(draft.doc.hats.patterns.length, 2);
});

test("the first edit of a session asks once when takes exist; a no changes nothing", () => {
  const no = setup({ takes: 2, confirm: false });
  assert.equal(no.draft.apply(rename("b")), false);
  assert.equal(no.draft.doc.arrangement[0].label, "a");
  const yes = setup({ takes: 2 });
  yes.draft.apply(rename("b"));
  yes.draft.apply(rename("c"));
  yes.draft.undo(false);
  assert.equal(yes.seen.confirms.length, 1);
  const none = setup({ takes: 0 });
  none.draft.apply(rename("b"));
  assert.equal(none.seen.confirms.length, 0);
});

test("a stale save reloads, says so and drops the local edit", async () => {
  const { draft, seen, timer } = setup({ answers: { draft: async () => Promise.reject(err(409, "stale_base")) } });
  draft.apply(rename("b"));
  timer.fire();
  await settle();
  assert.deepEqual(seen.says, ["Флип изменён в другой вкладке"]);
  assert.equal(seen.reloads, 1);
  assert.equal(draft.doc, null);
  // the screen reads the server's draft back
  draft.adopt(state(doc("server"), 9), false);
  assert.equal(draft.doc.arrangement[0].label, "server");
  assert.equal(draft.rev, 9);
  assert.equal(draft.dirty, false);
});

test("a read of the server's draft does not replace an edit the server lacks", () => {
  const { draft } = setup();
  draft.apply(rename("b"));
  draft.adopt(state(doc("server"), 9), false);
  assert.equal(draft.doc.arrangement[0].label, "b");
  assert.equal(draft.rev, 3);
});

test("commit renders the saved draft over its revision, then uses the render", async () => {
  const { draft, log, seen } = setup();
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log, [["draft", 3, "b"], ["render", 4], ["use", 11, 40]]);
  assert.equal(seen.reloads, 1);
  assert.equal(draft.dirty, false);
  assert.equal(draft.rendering, false);
  // nothing new to commit
  await draft.commit();
  assert.equal(log.length, 3);
});

test("a use answered 412 reads the song rev again and retries once", async () => {
  let tries = 0;
  const { draft, log } = setup({
    answers: {
      use: async () => {
        if (tries++ === 0) throw err(412, "stale");
      },
    },
  });
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log.filter((l) => l[0] === "use"), [["use", 11, 40], ["use", 11, 41]]);
  assert.equal(draft.dirty, false);
});

test("a use refused twice fails the commit", async () => {
  const { draft } = setup({ answers: { use: async () => { throw err(412, "stale"); } } });
  draft.apply(rename("b"));
  await assert.rejects(draft.commit(), (e) => e.status === 412);
});

test("a render refused as busy waits for the running one, then renders the newest draft", async () => {
  let first = true;
  const { draft, log } = setup({ answers: { render: async () => { if (first) { first = false; throw err(409, "busy"); } } } });
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log.map((l) => l[0]), ["draft", "render", "render", "use"]);
  assert.equal(draft.dirty, false);
});

test("a stale render reloads and ends the commit without a use", async () => {
  const { draft, log, seen } = setup({ answers: { serverDoc: doc("other"), render: async () => { throw err(409, "stale_base"); } } });
  draft.apply(rename("b"));
  await assert.rejects(draft.commit(), /другой вкладке/);
  assert.equal(log.some((l) => l[0] === "use"), false);
  assert.equal(seen.reloads, 1);
});

test("a failed render keeps the beat, is thrown once and is not retried until the next edit", async () => {
  const { draft, log } = setup({ answers: { fail: true } });
  draft.apply(rename("b"));
  await assert.rejects(draft.commit(), /boom/);
  assert.equal(log.some((l) => l[0] === "use"), false);
  assert.equal(draft.dirty, true);
  assert.equal(draft.wantsCommit, false);
  const renders = log.filter((l) => l[0] === "render").length;
  await draft.commit();
  assert.equal(log.filter((l) => l[0] === "render").length, renders);
  draft.apply(rename("c"));
  assert.equal(draft.wantsCommit, true);
});

test("a render is never used while recording; the next commit uses it without rendering again", async () => {
  const held = { recording: true };
  const { draft, log } = setup({ recording: () => held.recording });
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log.map((l) => l[0]), ["draft", "render"]);
  assert.equal(draft.dirty, true);
  held.recording = false;
  await draft.commit();
  assert.deepEqual(log.map((l) => l[0]), ["draft", "render", "use"]);
  assert.equal(draft.dirty, false);
});

test("an edit made while the commit renders is committed in a second round", async () => {
  const box = {};
  const { draft, log } = setup({
    answers: {
      render: async () => {
        if (!box.done) {
          box.done = true;
          box.draft.apply(rename("c"));
        }
      },
    },
  });
  box.draft = draft;
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log.map((l) => l[0]), ["draft", "render", "use", "draft", "render", "use"]);
  assert.equal(draft.dirty, false);
});

test("the page going away sends the unsent draft over its revision", () => {
  const { draft, log } = setup();
  draft.leave();
  assert.deepEqual(log, []);
  draft.apply(rename("b"));
  draft.leave();
  assert.deepEqual(log, [["beacon", 3]]);
});

test("a flip the server calls dirty is committed even with no edit here, then it is clean", async () => {
  const s = setup();
  s.draft.adopt(state(doc("other"), 5), true);
  assert.equal(s.draft.wantsCommit, true);
  await s.draft.commit();
  assert.deepEqual(s.log.map((l) => l[0]), ["render", "use"]);
  assert.equal(s.draft.wantsCommit, false);
});

test("a save made while the commit starts is rendered over its own revision", async () => {
  let ctx;
  let fired = false;
  ctx = setup({
    answers: {
      strict: true,
      jobs: async () => {
        if (fired) return;
        fired = true;
        ctx.draft.apply(rename("c"));
        ctx.timer.fire();
      },
    },
  });
  ctx.draft.apply(rename("b"));
  await ctx.draft.commit();
  assert.deepEqual(ctx.log.filter((l) => l[0] !== "use"), [["draft", 3, "c"], ["render", 4]]);
  assert.deepEqual(ctx.seen.says, []);
  assert.equal(ctx.seen.reloads, 1);
  assert.equal(ctx.draft.doc.arrangement[0].label, "c");
});

test("a render refused as stale while the server holds the page's own draft renders again", async () => {
  let first = true;
  const { draft, log, seen } = setup({
    answers: {
      render: async () => {
        if (!first) return;
        first = false;
        throw err(409, "stale_base");
      },
    },
  });
  draft.apply(rename("b"));
  await draft.commit();
  assert.deepEqual(log.filter((l) => l[0] === "render"), [["render", 4], ["render", 4]]);
  assert.equal(log.some((l) => l[0] === "use"), true);
  assert.deepEqual(seen.says, []);
  assert.equal(draft.doc.arrangement[0].label, "b");
});

test("the same content in another key order is the same doc: a reload keeps the history", async () => {
  const block = (extra) => ({ label: "a", from_bar: 0, to_bar: 4, ...extra });
  const note = { step: 0, len: 1, note: 36, vel: 100 };
  const withBlock = (b) => ({ ...doc(), arrangement: [b] });
  assert.equal(keyOf(withBlock(block({ drums: { kick: "x" }, notes: [note] }))), keyOf(withBlock(block({ notes: [note], drums: { kick: "x" } }))));
  assert.notEqual(keyOf(withBlock(block({ notes: [note] }))), keyOf(withBlock(block({ notes: [{ ...note, vel: 90 }] }))));
  const { draft, timer } = setup();
  draft.apply((d) => ({ ...d, arrangement: [block({ drums: { kick: "x" }, notes: [note] })] }));
  timer.fire();
  await settle();
  assert.equal(draft.canUndo, true);
  draft.adopt(state(withBlock(block({ notes: [note], drums: { kick: "x" } })), draft.rev), false);
  assert.equal(draft.canUndo, true);
  assert.equal(draft.dirty, false);
});

test("an answer read before the page's own save returned does not roll the revision back", async () => {
  const { draft, timer } = setup();
  draft.apply(rename("b"));
  timer.fire();
  await settle();
  assert.equal(draft.rev, 4);
  draft.adopt(state(doc("server"), 3), false);
  assert.equal(draft.rev, 4);
  assert.equal(draft.doc.arrangement[0].label, "b");
});
