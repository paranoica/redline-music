// The beat's level in the deck: left and right peaks with a short hold, in dB.

import { peakReadout } from "../../core/format.ts";
import { h } from "../../ui/dom.ts";

const FLOOR_DB = -60;
const HOLD_MS = 1200;

const toDb = (x: number): number => (x > 0 ? 20 * Math.log10(x) : -Infinity);
const share = (db: number): number => Math.min(1, Math.max(0, (db - FLOOR_DB) / -FLOOR_DB));

export class BeatMeter {
  readonly root: HTMLElement;
  readonly #left: HTMLElement;
  readonly #right: HTMLElement;
  readonly #readout: HTMLElement;
  #hold = -Infinity;
  #holdAt = 0;

  constructor() {
    this.#left = h("span", { class: "bmeter__fill" });
    this.#right = h("span", { class: "bmeter__fill" });
    this.#readout = h("span", { class: "bmeter__db num" }, "—");
    this.root = h(
      "div",
      { class: "bmeter", role: "img", "aria-label": "Уровень бита: не играет" },
      h("span", { class: "bmeter__bars" }, h("span", { class: "bmeter__well" }, this.#left), h("span", { class: "bmeter__well" }, this.#right)),
      this.#readout,
    );
  }

  show(left: number, right: number, playing: boolean): void {
    const now = performance.now();
    const l = toDb(left);
    const r = toDb(right);
    const peak = Math.max(l, r);
    if (peak >= this.#hold || now - this.#holdAt > HOLD_MS) {
      this.#hold = peak;
      this.#holdAt = now;
    }
    this.#left.style.setProperty("--level", String(share(l)));
    this.#right.style.setProperty("--level", String(share(r)));
    this.root.dataset.over = String(this.#hold > -0.1);
    this.#readout.textContent = peakReadout(this.#hold, playing);
    this.root.setAttribute("aria-label", playing ? `Уровень бита: пик ${this.#readout.textContent} дБ` : "Уровень бита: не играет");
  }
}
