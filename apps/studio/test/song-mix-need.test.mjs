// What the mix station says when it cannot build the mix: one reason, one way out.
import assert from "node:assert/strict";
import { test } from "node:test";

import { mixNeed } from "../src/song/stations.ts";

test("each reason gets its own words and way out", () => {
  const voice = mixNeed("no voice chosen", "takes", "demo");
  assert.match(voice.what, /нет голоса/);
  assert.equal(voice.go, "takes");
  const suno = mixNeed("stems not ready", "suno", "demo");
  assert.match(suno.what, /Suno/);
  assert.equal(suno.job, "separate");
  const own = mixNeed("stems not ready", "takes", "demo");
  assert.match(own.what, /[Рр]еференсы/);
  assert.doesNotMatch(own.what + own.how, /Suno/);
  assert.equal(own.job, "separate");
  assert.equal(voice.job, null);
  const refs = mixNeed("the song has no references", "takes", "demo");
  assert.match(refs.what, /референс/);
  assert.equal(refs.command, "uv run redline song refs demo --ref <файлы референсов>");
  assert.equal(refs.go, null);
  const missing = mixNeed("take-02.wav: no such file", "takes", "demo");
  assert.match(missing.what, /take-02\.wav/);
  assert.doesNotMatch(missing.what, /нет голоса/);
});
