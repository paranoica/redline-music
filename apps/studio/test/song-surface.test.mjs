// One mounted surface at a time; a redrawn station gets the same mounted surface, not a new one.
import assert from "node:assert/strict";
import { test } from "node:test";

import { SurfaceHost } from "../src/song/steps.ts";

function fakeDocument() {
  const make = (tag) => {
    const node = {
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
    };
    return node;
  };
  return { createElement: make };
}

test("a redrawn slot takes the mounted surface over instead of mounting again", async () => {
  globalThis.document = fakeDocument();
  try {
    let mounts = 0;
    let disposed = 0;
    const mount = async () => {
      mounts += 1;
      return { dispose() { disposed += 1; } };
    };
    const host = new SurfaceHost();
    const first = document.createElement("div");
    await host.show("mix", mount, first, "/api/mix", "t");
    const second = document.createElement("div");
    assert.equal(host.place("mix", second), true);
    assert.equal(second.children.length, 1);
    assert.equal(first.children.length, 0);
    assert.equal(host.place("beat", second), false);
    assert.equal(mounts, 1);
    assert.equal(disposed, 0);
  } finally {
    delete globalThis.document;
  }
});

test("a slot redrawn while the surface is still mounting gets it when it lands", async () => {
  globalThis.document = fakeDocument();
  try {
    let release;
    const mount = () => new Promise((r) => (release = () => r({ dispose() {} })));
    const host = new SurfaceHost();
    const first = document.createElement("div");
    const shown = host.show("mix", mount, first, "/api/mix", "t");
    const second = document.createElement("div");
    assert.equal(host.place("mix", second), true);
    release();
    await shown;
    assert.equal(second.children.length, 1);
    assert.equal(host.kind, "mix");
  } finally {
    delete globalThis.document;
  }
});
