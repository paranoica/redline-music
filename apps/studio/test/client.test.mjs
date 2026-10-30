// The page's client after the engine stopped for good: the transport no longer reaches it.
import assert from "node:assert/strict";
import { test } from "node:test";

import { EngineClient } from "../src/engine/client.ts";

function client() {
  const sent = [];
  const port = { onmessage: null, postMessage: (m) => sent.push(m) };
  const c = new EngineClient(port, new Map());
  return { c, port, sent };
}

test("a live client sends play, seek and loop", () => {
  const { c, sent } = client();
  assert.equal(c.dead, false);
  c.play(true);
  c.seek(10);
  c.loop(true, 0, 100);
  assert.deepEqual(sent.map((m) => m.type), ["play", "seek", "loop"]);
});

test("after a fatal error the client is dead and sends no play, seek or loop", () => {
  const { c, port, sent } = client();
  const fatal = [];
  c.onFatal = (m) => fatal.push(m);
  port.onmessage({ data: { type: "error", fatal: true, message: "boom" } });
  assert.equal(c.dead, true);
  c.play(true);
  c.seek(10);
  c.loop(true, 0, 100);
  assert.deepEqual(sent, []);
  assert.deepEqual(fatal, ["boom"]);
});
