// What the page does when an analysis it asked for does not come back.
import assert from "node:assert/strict";
import { test } from "node:test";

import { Superseded, analysisFailure, retryDelay } from "../src/core/analysis.ts";

test("a 409 the page did not abort itself means the server is busy: ask again", () => {
  assert.equal(analysisFailure(new Superseded("a newer analysis was asked for"), false), "again");
});

test("an abort by the page is quiet, whatever came with it", () => {
  assert.equal(analysisFailure(new DOMException("aborted", "AbortError"), false), "quiet");
  assert.equal(analysisFailure(new Superseded("x"), true), "quiet");
  assert.equal(analysisFailure(new Error("500"), true), "quiet");
});

test("any other failure is shown as one", () => {
  assert.equal(analysisFailure(new Error("the engine cannot measure these settings"), false), "failed");
  assert.equal(analysisFailure("text", false), "failed");
});

test("asking again backs off, so two pages do not cancel each other forever, and stops growing", () => {
  assert.equal(retryDelay(0), 300);
  assert.equal(retryDelay(1), 600);
  assert.equal(retryDelay(2), 1200);
  assert.equal(retryDelay(20), 10000);
});
