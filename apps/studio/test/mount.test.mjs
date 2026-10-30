// The mount contract: nothing runs on import, the api prefixes every request with its base,
// and dispose cancels the requests still out.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { createApi } from "../src/api.ts";
import { createApi as createBeatApi } from "../src/beat/api.ts";
import { apiPath, apiRoot } from "../src/core/mount.ts";
import { closeDismissables, dismissable, refsIn } from "../src/ui/dom.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function recordFetch() {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response("{}", { status: 200 });
  };
  return calls;
}

test("importing the apps touches no document and starts nothing", async () => {
  assert.equal(typeof document, "undefined");
  const mix = await import("../src/main.ts");
  const beat = await import("../src/beat/main.ts");
  assert.equal(typeof mix.mount, "function");
  assert.equal(typeof beat.mount, "function");
});

test("api root: the default when the base is empty, the base when given", () => {
  assert.equal(apiRoot(undefined, "/api"), "/api");
  assert.equal(apiRoot("", "/api"), "/api");
  assert.equal(apiRoot("/api/mix", "/api"), "/api/mix");
  assert.equal(apiRoot("/api/mix/", "/api"), "/api/mix");
  assert.equal(apiPath("/api", "/session"), "/api/session");
  assert.equal(apiPath("/api", "session"), "/api/session");
});

test("the mix api keeps /api/... with no base and moves under the base when given", async () => {
  const calls = recordFetch();
  const plain = createApi({ token: "t" });
  await plain.session();
  await plain.stem("vocal");
  await plain.reference(2);
  await plain.save({});
  const song = createApi({ base: "/api/mix", token: "t" });
  await song.session();
  await song.stem("beat");
  await song.analysis({}, new AbortController().signal);
  assert.deepEqual(
    calls.map((c) => c.url),
    ["/api/session", "/api/stem?name=vocal", "/api/reference?i=2", "/api/save", "/api/mix/session", "/api/mix/stem?name=beat", "/api/mix/analysis"],
  );
  assert.equal(calls[0].init.headers["X-Token"], "t");
});

test("the beat api defaults to /api/beat and takes another base", async () => {
  const calls = recordFetch();
  const plain = createBeatApi({ token: "t" });
  await plain.session();
  await plain.renderStatus();
  await plain.renderFile("a b.wav");
  const other = createBeatApi({ base: "/api/song/beat", token: "t" });
  await other.check({});
  await other.zone("lib", "f.wav").catch(() => {});
  assert.deepEqual(
    calls.map((c) => c.url),
    ["/api/beat/session", "/api/beat/render/status", "/api/beat/render?file=a%20b.wav", "/api/song/beat/check", "/api/song/beat/zone?library=lib&file=f.wav"],
  );
});

test("close cancels a request that is still out, and later ones", async () => {
  let seen = null;
  globalThis.fetch = (url, init) =>
    new Promise((_, reject) => {
      seen = init.signal;
      if (init.signal.aborted) return reject(new DOMException("aborted", "AbortError"));
      init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    });
  const api = createApi({ token: "t" });
  const pending = api.session();
  api.close();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(seen.aborted, true);
  await assert.rejects(api.session(), { name: "AbortError" });
});

test("a caller's own signal still cancels its request", async () => {
  globalThis.fetch = (url, init) =>
    new Promise((_, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
  const api = createApi({ token: "t" });
  const own = new AbortController();
  const pending = api.render({}, 1, own.signal);
  own.abort();
  await assert.rejects(pending, { name: "AbortError" });
});

function fakeDoc() {
  const live = new Set();
  return {
    live,
    addEventListener: (type, fn) => live.add(`${type}:${fn.name}`),
    removeEventListener: (type, fn) => live.delete(`${type}:${fn.name}`),
  };
}

function fakeElement(doc, within = []) {
  return { ownerDocument: doc, contains: (n) => n === undefined || within.includes(n), focus() {} };
}

test("closeDismissables closes the panels opened inside the root and frees the document", () => {
  const doc = fakeDoc();
  const inside = fakeElement(doc);
  const outside = fakeElement(doc);
  const root = { contains: (n) => n === inside };
  let closed = 0;
  dismissable(fakeElement(doc), inside, () => closed++);
  assert.equal(doc.live.size, 2);
  closeDismissables(root);
  assert.equal(closed, 1);
  assert.equal(doc.live.size, 0);
  dismissable(fakeElement(doc), outside, () => closed++);
  closeDismissables(root);
  assert.equal(closed, 1);
  assert.equal(doc.live.size, 2);
});

test("refsIn finds data-ref among the inserted nodes only", () => {
  const el = (ref, children = []) => ({
    nodeType: 1,
    matches: (sel) => sel === `[data-ref="${ref}"]`,
    querySelector: (sel) => children.find((c) => c.matches(sel)) ?? null,
  });
  const inner = el("play");
  const ref = refsIn([{ nodeType: 3 }, el("outer", [inner])]);
  assert.equal(ref("play"), inner);
  assert.throws(() => ref("missing"), /no \[data-ref=missing\]/);
});

test("holding an app's audio suspends only a running context and resumes only what it suspended", async () => {
  const { audioHold } = await import("../src/core/mount.ts");
  const calls = [];
  const ctx = {
    state: "running",
    suspend: async () => { calls.push("suspend"); ctx.state = "suspended"; },
    resume: async () => { calls.push("resume"); ctx.state = "running"; },
  };
  const hold = audioHold(() => ctx);
  hold(true);
  hold(true);
  hold(false);
  assert.deepEqual(calls, ["suspend", "resume"]);
  ctx.state = "suspended";
  hold(true);
  hold(false);
  assert.deepEqual(calls, ["suspend", "resume"]);
  audioHold(() => null)(true);
});
