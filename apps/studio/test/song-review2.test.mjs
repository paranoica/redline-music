// Review fixes of the song page: no older song over a newer one, the take key behind the chooser, unsaved text.
import assert from "node:assert/strict";
import { test } from "node:test";

import { acceptSong } from "../src/song/api.ts";
import { takeKeyAllowed } from "../src/song/record.ts";
import { textDirty } from "../src/song/text.ts";

const song = (rev) => ({ song: { name: "s", rev }, stations: {} });

test("a song with a lower rev never replaces the one held", () => {
  const held = song(5);
  assert.equal(acceptSong(held, song(4)), held);
  assert.equal(acceptSong(held, song(0)), held);
});

test("a song with the same or a higher rev is taken; the first one always", () => {
  const same = song(5);
  const higher = song(6);
  assert.equal(acceptSong(song(5), same), same);
  assert.equal(acceptSong(song(5), higher), higher);
  assert.equal(acceptSong(null, same), same);
});

test("the take key works when ready or live and the chooser is closed", () => {
  assert.equal(takeKeyAllowed("ready", false), true);
  assert.equal(takeKeyAllowed("live", false), true);
});

test("the take key does nothing behind the chooser or in other phases", () => {
  assert.equal(takeKeyAllowed("ready", true), false);
  assert.equal(takeKeyAllowed("live", true), false);
  for (const phase of ["closed", "opening", "calibrating", "stopping", "failed", "out_of_sync"]) {
    assert.equal(takeKeyAllowed(phase, false), false);
  }
});

test("a text draft is dirty only while editing and different from the loaded text", () => {
  assert.equal(textDirty(true, "b", { text: "a" }), true);
  assert.equal(textDirty(true, "a", { text: "a" }), false);
  assert.equal(textDirty(false, "b", { text: "a" }), false);
  assert.equal(textDirty(true, "b", null), false);
  assert.equal(textDirty(true, "", { text: "a" }), true);
});
