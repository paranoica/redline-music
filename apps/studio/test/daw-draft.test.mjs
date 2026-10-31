// An own beat's working copy: which base a draft is saved over, when an unchanged draft is not an
// edit, and how long the render is shown as running.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Refused } from "../src/beat/api.ts";
import { BeatDraft } from "../src/daw/draft.ts";

const tick = () => new Promise((r) => setImmediate(r));
const settle = async () => {
  for (let i = 0; i < 20; i++) await tick();
};

function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

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

const score = (n) => ({ bpm: 90, version: n, patterns: {} });

// the server: `pairs` are the draft and session answers in turn; saves can be held
function server({ pairs, holdSave = false, firstFile = 2 }) {
  const calls = { drafts: [], saves: [], renders: [] };
  let file = firstFile;
  let rendered = null;
  const held = [];
  let pair = 0;
  const api = {
    calls,
    held,
    draft: async () => pairs[Math.min(pair, pairs.length - 1)].draft,
    session: async () => {
      const p = pairs[Math.min(pair, pairs.length - 1)].session;
      pair += 1;
      return p;
    },
    saveDraft: async (s, base) => {
      calls.drafts.push({ score: s, base });
      return { ok: true };
    },
    save: async (s, base) => {
      calls.saves.push({ score: s, base });
      const name = `score-${String(file++).padStart(3, "0")}.json`;
      if (holdSave) {
        const d = deferred();
        held.push(d);
        await d.promise;
      }
      return { file: name, warnings: [] };
    },
    render: async (f) => {
      calls.renders.push(f);
      rendered = f;
      return { state: "running", score_file: f, error: null };
    },
    renderStatus: async () => ({ state: "done", score_file: rendered, error: null }),
  };
  return api;
}

const same = (s, base = "score-001.json") => ({ draft: { score: s, base }, session: { score_file: base, score: s } });

function draftWith(api, over = {}) {
  const seen = { loaded: [], says: [], reloads: 0 };
  const timer = clock();
  const facts = { beatKind: "own", beatDirty: false, hasBeatAudio: true, ...over.facts };
  const d = new BeatDraft({
    api,
    facts: () => facts,
    loaded: (s, edited) => seen.loaded.push({ s, edited }),
    loadEnded: () => {},
    reload: over.reload ?? (async () => void seen.reloads++),
    changed: () => {},
    say: (t) => seen.says.push(t),
    timer,
    sleep: async () => {},
  });
  d.reset(7);
  return { d, seen, timer, facts };
}

test("an edit made while a commit saves goes out over the new score, not the one before it", async () => {
  const api = server({ pairs: [same(score(0))], holdSave: true });
  const { d, timer } = draftWith(api);
  await d.load();
  d.apply(score(1));
  const commit = d.commit();
  await settle();
  assert.deepEqual(api.calls.drafts, [{ score: score(1), base: "score-001.json" }]);
  assert.equal(api.calls.saves.length, 1);
  // the save is out; an edit comes in
  d.apply(score(2));
  api.held[0].resolve();
  await commit;
  timer.fire();
  await settle();
  const last = api.calls.drafts.at(-1);
  assert.deepEqual(last.score, score(2));
  assert.equal(last.base, "score-002.json");
  assert.ok(api.calls.drafts.every((x) => x.score.version !== 2 || x.base === "score-002.json"));
  // the edit is still an edit of the new score
  assert.equal(d.dirty, true);
});

test("a draft and a newest score read across a save are read again; an unchanged draft is no edit", async () => {
  const s = score(5);
  // the draft came after a save, the session before it: the names differ, the contents agree
  const crossed = { draft: { score: s, base: "score-002.json" }, session: { score_file: "score-001.json", score: score(4) } };
  const api = server({ pairs: [crossed, same(s, "score-002.json")] });
  const { d, seen } = draftWith(api);
  await d.load();
  assert.equal(d.dirty, false);
  assert.deepEqual(seen.loaded.map((x) => x.edited), [false]);
  await d.commit();
  assert.deepEqual(api.calls.saves, []);
  assert.deepEqual(api.calls.renders, []);
});

test("a draft that differs from the newest score is an edit, and the commit saves it once", async () => {
  const api = server({ pairs: [{ draft: { score: score(6), base: "score-001.json" }, session: { score_file: "score-001.json", score: score(5) } }] });
  const { d, seen } = draftWith(api);
  await d.load();
  assert.equal(d.dirty, true);
  assert.deepEqual(seen.loaded.map((x) => x.edited), [true]);
  await d.commit();
  assert.equal(api.calls.saves.length, 1);
  assert.equal(d.dirty, false);
});

test("the render shows as running until the song has reloaded with it, and each waited render is counted first", async () => {
  const api = server({ pairs: [same(score(0))] });
  const reloads = [];
  let draft = null;
  const gate = deferred();
  const { d } = draftWith(api, {
    reload: async () => {
      reloads.push({ rendering: draft.rendering, renders: draft.renders });
      await gate.promise;
    },
  });
  draft = d;
  await d.load();
  assert.equal(d.renders, 0);
  d.apply(score(1));
  const commit = d.commit();
  await settle();
  assert.equal(reloads.length, 1);
  assert.deepEqual(reloads[0], { rendering: true, renders: 1 });
  // the reload is still out
  assert.equal(d.rendering, true);
  gate.resolve();
  await commit;
  assert.equal(d.rendering, false);
  assert.equal(d.renders, 1);
  // nothing to commit: no render, no count
  await d.commit();
  assert.equal(d.renders, 1);
});

test("a draft due while a commit's save is out waits for it and goes over the new score, with no false failure", async () => {
  const api = server({ pairs: [same(score(0))], holdSave: true });
  // the server has the new score as soon as the save is asked for, and refuses a draft over an older one
  let newest = "score-001.json";
  const save = api.save;
  api.save = (s, base) => {
    newest = `score-${String(api.calls.saves.length + 2).padStart(3, "0")}.json`;
    return save(s, base);
  };
  const saveDraft = api.saveDraft;
  api.saveDraft = async (s, base) => {
    if (base !== newest) {
      api.calls.drafts.push({ score: s, base, refused: true });
      throw new Refused(409, "stale base", ["stale base"]);
    }
    return saveDraft(s, base);
  };
  const { d, seen, timer } = draftWith(api);
  await d.load();
  d.apply(score(1));
  const commit = d.commit();
  await settle();
  assert.equal(api.calls.saves.length, 1);
  d.apply(score(2));
  // the quiet moment runs out while the save is still out
  timer.fire();
  await settle();
  assert.deepEqual(api.calls.drafts.filter((x) => x.refused), []);
  assert.deepEqual(seen.says, []);
  api.held[0].resolve();
  await commit;
  timer.fire();
  await settle();
  assert.deepEqual(api.calls.drafts.filter((x) => x.refused), []);
  assert.deepEqual(seen.says, []);
  const last = api.calls.drafts.at(-1);
  assert.deepEqual(last.score, score(2));
  assert.equal(last.base, "score-002.json");
  assert.equal(d.saveState, "saved");
});
