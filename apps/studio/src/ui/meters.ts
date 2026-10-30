// The meter strip: vocal, beat and master levels, the master's gain reduction hanging
// in red from the top, and the result's loudness.

import { formatNumber, peakReadout } from "../core/format.ts";
import type { Level, Levels } from "../engine/protocol.ts";
import { h } from "./dom.ts";

const FLOOR_DB = -60;
const GR_RANGE_DB = 12;
const HOLD_MS = 1200;

function toDb(x: number): number {
  return x > 0 ? 20 * Math.log10(x) : -Infinity;
}

function share(db: number): number {
  return Math.min(1, Math.max(0, (db - FLOOR_DB) / -FLOOR_DB));
}

class Meter {
  readonly root: HTMLElement;
  readonly #rms: HTMLElement;
  readonly #peak: HTMLElement;
  readonly #readout: HTMLElement;
  #hold = -Infinity;
  #holdAt = 0;

  constructor(label: string, extra?: HTMLElement) {
    this.#rms = h("span", { class: "meter__rms" });
    this.#peak = h("span", { class: "meter__peak" });
    this.#readout = h("span", { class: "meter__db num" }, "—");
    this.root = h(
      "div",
      { class: "meter", role: "img", "aria-label": `${label}: уровень` },
      h("span", { class: "meter__well" }, this.#rms, this.#peak, extra ?? null),
      h("span", { class: "meter__label" }, label),
      this.#readout,
    );
  }

  show(level: Level, now: number, playing: boolean): void {
    const peak = toDb(level.peak);
    const rms = toDb(level.rms);
    if (peak >= this.#hold || now - this.#holdAt > HOLD_MS) {
      this.#hold = peak;
      this.#holdAt = now;
    }
    this.#rms.style.setProperty("--level", String(share(rms)));
    this.#peak.style.setProperty("--level", String(share(this.#hold)));
    this.root.dataset.over = String(this.#hold > -0.1);
    this.#readout.textContent = peakReadout(this.#hold, playing);
    const label = this.root.querySelector(".meter__label")?.textContent ?? "";
    const idle = !playing && !Number.isFinite(this.#hold);
    this.root.setAttribute("aria-label", idle ? `${label}: не играет` : `${label}: пик ${this.#readout.textContent} дБ`);
  }
}

export class Meters {
  readonly root: HTMLElement;
  readonly #vocal = new Meter("Голос");
  readonly #beat = new Meter("Бит");
  readonly #master: Meter;
  readonly #gr: HTMLElement;
  readonly #grText: HTMLElement;
  readonly #lufs: HTMLElement;

  constructor() {
    this.#gr = h("span", { class: "meter__gr" });
    this.#master = new Meter("Мастер", this.#gr);
    this.#grText = h("span", { class: "strip__value num" }, "—");
    this.#lufs = h("span", { class: "strip__value num" }, "—");
    this.root = h(
      "aside",
      { class: "strip", "aria-label": "Уровни" },
      h("div", { class: "strip__meters" }, this.#vocal.root, this.#beat.root, this.#master.root),
      h(
        "dl",
        { class: "strip__facts" },
        h("div", {}, h("dt", {}, "Срезано мастером"), h("dd", {}, this.#grText)),
        h("div", {}, h("dt", {}, "Громкость результата"), h("dd", {}, this.#lufs)),
      ),
    );
  }

  show(levels: Levels): void {
    const now = performance.now();
    this.#vocal.show(levels.vocal, now, levels.playing);
    this.#beat.show(levels.beat, now, levels.playing);
    this.#master.show(levels.master, now, levels.playing);
    const gr = levels.master.gr;
    this.#gr.style.setProperty("--gr", String(gr === null ? 0 : Math.min(1, gr / GR_RANGE_DB)));
    this.#grText.textContent = gr === null ? "мастер выключен" : `${formatNumber(gr, 1)} дБ`;
    this.#lufs.textContent = Number.isFinite(levels.lufs) ? `${formatNumber(levels.lufs, 1)} LUFS` : "—";
  }

  idle(): void {
    const zero = { peak: 0, rms: 0 };
    this.show({ type: "levels", frame: 0, seq: 0, playing: false, vocal: zero, beat: zero, master: { ...zero, gr: null }, lufs: -Infinity, latency: 0 });
    this.#grText.textContent = "—";
  }
}
