// The song api: base paths, the token on every call, the shape of the bodies.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { ApiError, createSongApi } from "../src/song/api.ts";
import { recPhase, stageRows } from "../src/song/record.ts";

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

function fake(status = 200, body = {}) {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}

test("paths, methods, token and bodies", async () => {
  const calls = fake();
  const api = createSongApi({ token: "tok" });
  assert.equal(api.mixBase, "/api/mix");
  assert.equal(api.beatBase, "/api/beat");
  await api.song();
  await api.setSong(4, { mix_variant: 1 });
  await api.text(3);
  await api.saveText(12, 3, "x");
  await api.recState();
  await api.recOpen();
  await api.recToggle();
  await api.recDiscard();
  await api.recCalibrate("cup");
  await api.recClose();
  await api.takes();
  await api.takeAudio("take-04.wav");
  await api.jobs();
  await api.startJob("auto", 7);
  await api.cancelJob("auto");
  assert.deepEqual(
    calls.map((c) => `${c.init.method} ${c.url}`),
    [
      "GET /api/song",
      "POST /api/song",
      "GET /api/text?version=3",
      "POST /api/text/save",
      "GET /api/rec/state",
      "POST /api/rec/open",
      "POST /api/rec/toggle",
      "POST /api/rec/discard",
      "POST /api/rec/calibrate",
      "POST /api/rec/close",
      "GET /api/takes",
      "GET /api/takes/audio?name=take-04.wav",
      "GET /api/jobs",
      "POST /api/jobs/start",
      "POST /api/jobs/cancel",
    ],
  );
  for (const c of calls) assert.equal(c.init.headers["X-Token"], "tok");
  assert.deepEqual(JSON.parse(calls[1].init.body), { rev: 4, set: { mix_variant: 1 } });
  assert.deepEqual(JSON.parse(calls[13].init.body), { kind: "auto", seed: 7 });
  assert.deepEqual(JSON.parse(calls[14].init.body), { kind: "auto" });
});

test("a custom base prefixes every path", async () => {
  const calls = fake();
  const api = createSongApi({ base: "/x/api/", token: "t" });
  await api.song();
  assert.equal(calls[0].url, "/x/api/song");
  assert.equal(api.mixBase, "/x/api/mix");
});

test("errors keep the status, code and body; 412 carries the song", async () => {
  fake(412, { error: "stale", code: "stale", song: { name: "s", rev: 9 }, stations: {} });
  const api = createSongApi({ token: "t" });
  await assert.rejects(api.setSong(1, { mix_variant: null }), (e) => e instanceof ApiError && e.status === 412 && e.code === "stale" && e.body.song.rev === 9);
});

test("variants: 404 before the first run is null; mix probe reads 409 no_voice", async () => {
  fake(404, { error: "none" });
  assert.equal(await createSongApi({ token: "t" }).variants(), null);
  fake(409, { error: "no voice chosen", code: "no_voice" });
  assert.equal(await createSongApi({ token: "t" }).mixReady(), "no_voice");
  fake(200, {});
  assert.equal(await createSongApi({ token: "t" }).mixReady(), "ready");
  fake(409, { code: "busy" });
  await assert.rejects(createSongApi({ token: "t" }).mixReady());
});

test("recording phases and the opening stages", () => {
  const snap = (over) => ({ stage: "ready", error: null, out_of_sync: false, recording: false, stopping: false, take: null, seconds: 0, peak_db: null, clip: false, dropouts: 0, monitor_ms: null, round_trip: null, rate: 48000, ...over });
  assert.equal(recPhase(null), "closed");
  assert.equal(recPhase(snap({ stage: "analysing" })), "opening");
  assert.equal(recPhase(snap({ recording: true })), "live");
  assert.equal(recPhase(snap({ recording: true, stopping: true })), "stopping");
  assert.equal(recPhase(snap({ out_of_sync: true })), "out_of_sync");
  assert.equal(recPhase(snap({ stage: "failed" })), "failed");
  const rows = stageRows(snap({ stage: "analysing" }), null);
  assert.deepEqual(rows.map((r) => r.state), ["done", "done", "done", "now", "wait", "wait"]);
  const failed = stageRows(snap({ stage: "failed", error: "x" }), "relaunching");
  assert.deepEqual(failed.map((r) => r.state), ["done", "done", "fail", "wait", "wait", "wait"]);
});

test("variants made for another voice or beat read as stale, other refusals still throw", async () => {
  fake(409, { error: "variants of another voice", code: "stale_variants" });
  assert.equal(await createSongApi({ token: "t" }).variants(), "stale");
  fake(409, { code: "busy" });
  await assert.rejects(createSongApi({ token: "t" }).variants());
});

test("a new song is posted to the songs list with the server's body", async () => {
  const calls = fake(201, { name: "n" });
  const api = createSongApi({ token: "tok" });
  const body = { name: "n", beat: { own: 3 }, text_seed: null };
  assert.deepEqual(await api.createSong(body), { name: "n" });
  assert.equal(`${calls[0].init.method} ${calls[0].url}`, "POST /api/songs");
  assert.equal(calls[0].init.headers["X-Token"], "tok");
  assert.deepEqual(JSON.parse(calls[0].init.body), body);
});
