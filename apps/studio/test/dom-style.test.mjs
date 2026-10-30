// h() keeps inline styles off the style attribute, which the page's policy blocks.
import assert from "node:assert/strict";
import { test } from "node:test";

import { h, styleDeclarations } from "../src/ui/dom.ts";

test("a style string splits into properties, custom ones and values with colons kept", () => {
  assert.deepEqual(styleDeclarations("--v: 0.40; margin-top: var(--s4);"), [
    ["--v", "0.40"],
    ["margin-top", "var(--s4)"],
  ]);
  assert.deepEqual(styleDeclarations("background: url(a:b)"), [["background", "url(a:b)"]]);
  assert.deepEqual(styleDeclarations(" ; "), []);
});

test("h sets a style through the element's style object, never the attribute", () => {
  const set = [];
  const attrs = [];
  globalThis.document = {
    createElement: () => ({
      style: { setProperty: (k, v) => set.push([k, v]) },
      setAttribute: (k, v) => attrs.push([k, v]),
      append: () => {},
    }),
  };
  try {
    h("span", { style: "--v: 0.5", "aria-hidden": "true" });
  } finally {
    delete globalThis.document;
  }
  assert.deepEqual(set, [["--v", "0.5"]]);
  assert.deepEqual(attrs, [["aria-hidden", "true"]]);
});
