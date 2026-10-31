// Review fixes of the song page: the stale body, refusal words, finished jobs.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, createSongApi, staleSong } from "../src/song/api.ts";
import { recPhase } from "../src/song/record.ts";
import { finishedKinds, refusalText } from "../src/song/jobs.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

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

test("the engine opening or calibrating reads as the engine, not as jobs", () => {
  const text = refusalText(new ApiError(409, { code: "engine_busy", error: "the engine is opening" }), []);
  assert.match(text, /[Дд]вижок/);
  assert.doesNotMatch(text, /ничего не идёт/);
});
