// The flip client: the draft and the render carry the revision they are over.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { createFlipApi, isBusy, isStale } from "../src/flip/api.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function fake(status, body) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}

test("a draft is saved over its revision and answers the new one", async () => {
  const calls = fake(200, { ok: true, draft_rev: 5 });
  const api = createFlipApi({ token: "t" });
  assert.equal(await api.saveDraft({ format: 1 }, 4), 5);
  assert.deepEqual(calls, [{ url: "/api/flip/draft", method: "POST", body: { flip: { format: 1 }, base: 4 } }]);
});

test("a render names the revision it is of", async () => {
  const calls = fake(202, { n: 3 });
  const api = createFlipApi({ token: "t" });
  assert.equal(await api.render(7), 3);
  assert.deepEqual(calls[0].body, { base: 7 });
});

test("409 stale_base and 409 busy are told apart", async () => {
  const api = createFlipApi({ token: "t" });
  fake(409, { error: "x", code: "stale_base" });
  const stale = await api.saveDraft({}, 1).catch((e) => e);
  assert.equal(isStale(stale), true);
  assert.equal(isBusy(stale), false);
  fake(409, { error: "x", code: "busy" });
  const busy = await api.render(1).catch((e) => e);
  assert.equal(isBusy(busy), true);
  assert.equal(isStale(busy), false);
});

test("a refused draft keeps its messages", async () => {
  fake(422, { error: "bad", code: "invalid_flip", errors: ["a", "b"] });
  const error = await createFlipApi({ token: "t" }).saveDraft({}, 1).catch((e) => e);
  assert.deepEqual(error.errors, ["a", "b"]);
});
