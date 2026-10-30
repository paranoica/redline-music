// The flip is the third kind of the surface host: parked it stays alive, the other kinds replace it.
import assert from "node:assert/strict";
import { test } from "node:test";

import { SurfaceHost } from "../src/song/steps.ts";

function fakeDocument() {
  const make = (tag) => ({
    tag,
    children: [],
    parent: null,
    className: "",
    style: { setProperty() {} },
    setAttribute() {},
    append(...kids) {
      for (const k of kids) this.adopt(k);
    },
    replaceChildren(...kids) {
      for (const k of this.children) k.parent = null;
      this.children = [];
      this.append(...kids);
    },
    adopt(k) {
      if (typeof k !== "object") return;
      if (k.parent) k.parent.children = k.parent.children.filter((c) => c !== k);
      k.parent = this;
      this.children.push(k);
    },
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
      this.parent = null;
    },
  });
  return { createElement: make };
}

function surface(log, name) {
  return async (_root, options) => {
    log.push(`mount ${name} ${options.base}`);
    return {
      dispose() {
        log.push(`dispose ${name}`);
      },
      hold(on) {
        log.push(`hold ${name} ${on}`);
      },
      setActive(on) {
        log.push(`active ${name} ${on}`);
      },
    };
  };
}

test("a parked flip stays mounted and comes back by place; another kind disposes it", async () => {
  globalThis.document = fakeDocument();
  try {
    const log = [];
    const host = new SurfaceHost();
    const slot = document.createElement("div");
    await host.show("flip", surface(log, "flip"), slot, "/api/flip", "t");
    assert.equal(host.kind, "flip");
    host.park();
    const again = document.createElement("div");
    assert.equal(host.place("flip", again), true);
    assert.equal(again.children.length, 1);
    assert.equal(host.place("beat", again), false);
    assert.ok(!log.includes("dispose flip"));
    assert.deepEqual(log.filter((l) => l.startsWith("mount")), ["mount flip /api/flip"]);
    assert.ok(log.includes("hold flip true") && log.includes("hold flip false"));
    await host.show("beat", surface(log, "beat"), again, "/api/beat", "t");
    assert.ok(log.indexOf("dispose flip") < log.indexOf("mount beat /api/beat"));
    assert.equal(host.kind, "beat");
  } finally {
    delete globalThis.document;
  }
});

test("a dirty flip is named when another kind would replace it", async () => {
  globalThis.document = fakeDocument();
  try {
    const host = new SurfaceHost();
    let unsaved = true;
    const mount = async () => ({ dispose() {}, dirty: () => unsaved });
    await host.show("flip", mount, document.createElement("div"), "/api/flip", "t");
    assert.equal(host.unsavedFor("mix"), "flip");
    assert.equal(host.unsavedFor("flip"), null);
    unsaved = false;
    assert.equal(host.unsavedFor("mix"), null);
  } finally {
    delete globalThis.document;
  }
});

test("disposing the host forgets which source the flip was mounted for", async () => {
  globalThis.document = fakeDocument();
  try {
    const host = new SurfaceHost();
    const slot = document.createElement("div");
    await host.show("flip", surface([], "flip"), slot, "/api/flip", "t");
    host.tag = "input/beats/x.mp3";
    await host.dispose();
    assert.equal(host.tag, null);
    assert.equal(host.kind, null);
  } finally {
    delete globalThis.document;
  }
});
