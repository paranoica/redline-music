// The page token: taken from the address once, kept for the tab so a reload still works.
import assert from "node:assert/strict";
import { test } from "node:test";

import { pageToken } from "../src/core/token.ts";

function store(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    data,
  };
}

test("the token in the address is kept for the tab and dropped from the address", () => {
  const tab = store();
  let cleared = 0;
  assert.equal(pageToken("?t=abc", tab, () => cleared++), "abc");
  assert.equal(tab.data.get("redline-token"), "abc");
  assert.equal(cleared, 1);
});

test("a reload without the token in the address uses the tab's", () => {
  const tab = store({ "redline-token": "abc" });
  assert.equal(pageToken("", tab, () => {}), "abc");
});

test("a new token in the address replaces the tab's", () => {
  const tab = store({ "redline-token": "old" });
  assert.equal(pageToken("?t=new", tab, () => {}), "new");
  assert.equal(tab.data.get("redline-token"), "new");
});

test("storage that throws or is missing still gives the address token", () => {
  const broken = {
    getItem() {
      throw new Error("blocked");
    },
    setItem() {
      throw new Error("blocked");
    },
  };
  assert.equal(pageToken("?t=abc", broken, () => {}), "abc");
  assert.equal(pageToken("", broken, () => {}), "");
  assert.equal(pageToken("", null, () => {}), "");
});
