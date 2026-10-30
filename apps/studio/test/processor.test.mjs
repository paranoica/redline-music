// The audio callback of the worklet must not allocate: a closure made per call is garbage
// the audio thread's collector has to clear.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../src/worklet/processor.ts", import.meta.url), "utf8");

function body(name) {
  const start = source.indexOf(`\n  ${name}(`);
  assert.ok(start >= 0, `no ${name}`);
  const open = source.indexOf("{", source.indexOf(")", start));
  let depth = 0;
  for (let i = open; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(open, i + 1);
  }
  throw new Error(`${name} does not close`);
}

test("process() makes no closure per audio callback", () => {
  const code = body("process");
  assert.doesNotMatch(code, /=>|function\s*\(/, code);
});
