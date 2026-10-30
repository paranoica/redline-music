// The built-in chain as stage panels in signal order, each setting as a fader, a switch or
// a choice; a setting under a switch that is off is dimmed with it.

import { isLog } from "../core/control-math.ts";
import { formatValue } from "../core/format.ts";
import type { SettingSpec, SettingValue, Settings } from "../types.ts";
import { Fader, Segmented, Switch } from "./controls.ts";
import { h } from "./dom.ts";

export interface Stage {
  id: string;
  title: string;
  groups: string[];
}

export const STAGES: readonly Stage[] = [
  { id: "voice", title: "Голос", groups: ["chain", "voice"] },
  { id: "eq", title: "Эквалайзер", groups: ["eq"] },
  { id: "layers", title: "Слои", groups: ["layers"] },
  { id: "fx", title: "Эффекты", groups: ["fx"] },
  { id: "beat", title: "Бит", groups: ["beat"] },
  { id: "master", title: "Мастер", groups: ["master"] },
];

// a setting and the switches it waits on: [key, value the switch must have]
const WAITS: Record<string, [string, boolean][]> = {
  comp_threshold_db: [["chain_on", true]],
  comp_ratio: [["chain_on", true]],
  comp_attack_ms: [["chain_on", true]],
  comp_release_ms: [["chain_on", true]],
  drive_db: [["chain_on", true]],
  clip_on: [["chain_on", true]],
  clip_below_peak_db: [["chain_on", true], ["clip_on", true]],
  octave_down_db: [["octave_down_on", true]],
  octave_up_db: [["octave_up_on", true]],
  doubles_db: [["doubles_on", true]],
  doubles_left_ms: [["doubles_on", true]],
  doubles_right_ms: [["doubles_on", true]],
  doubles_wander_ms: [["doubles_on", true]],
  bitcrush_bits: [["bitcrush_on", true]],
  bitcrush_hold: [["bitcrush_on", true]],
  stutter_ms: [["stutter_on", true]],
  stutter_repeats: [["stutter_on", true]],
  throw_db: [["throw_on", true]],
  throw_ms: [["throw_on", true]],
  throw_feedback: [["throw_on", true]],
  throw_tone_hz: [["throw_on", true]],
  reverb_mix_db: [["reverb_on", true]],
  reverb_decay_s: [["reverb_on", true]],
  reverb_predelay_ms: [["reverb_on", true]],
  reverb_tone_hz: [["reverb_on", true]],
  master_auto: [["master_on", true]],
  master_lufs: [["master_on", true], ["master_auto", false]],
};

// what the live racks leave out (redline.engine.live.NOT_EXPRESSED): heard in the exact render only
export const NOT_LIVE = new Set(["stutter_on", "stutter_ms", "stutter_repeats", "doubles_right_ms"]);

interface Row {
  spec: SettingSpec;
  root: HTMLElement;
  set: (v: SettingValue) => void;
  disable: (off: boolean) => void;
}

export interface ChainHandlers {
  input: (key: string, value: SettingValue) => void;
  commit: (key: string, value: SettingValue) => void;
}

function octaveLabel(v: number): string {
  return v === 0 ? "своя" : `${v > 0 ? "+" : "−"}1 окт`;
}

export class ChainPanels {
  readonly panels: HTMLElement[];
  readonly flow: HTMLElement;
  readonly #rows: Row[] = [];
  readonly #flowState = new Map<string, HTMLElement>();

  constructor(specs: readonly SettingSpec[], groups: Record<string, string>, on: ChainHandlers) {
    this.panels = STAGES.map((stage, i) => {
      const stageSpecs = specs.filter((s) => stage.groups.includes(s.group));
      const body = h("div", { class: "panel__rows" });
      let lastGroup = "";
      for (const spec of stageSpecs) {
        if (stage.groups.length > 1 && spec.group !== lastGroup) {
          body.append(h("h3", { class: "panel__sub" }, groups[spec.group] ?? spec.group));
          lastGroup = spec.group;
        }
        const row = this.#row(spec, on);
        this.#rows.push(row);
        body.append(row.root);
      }
      return h(
        "section",
        {
          class: "panel",
          id: `panel-${stage.id}`,
          "aria-labelledby": `title-${stage.id}`,
          "data-basic": String(stageSpecs.some((s) => s.basic)),
        },
        h(
          "header",
          { class: "panel__head" },
          h("h2", { class: "panel__title", id: `title-${stage.id}` }, h("span", { class: "panel__n num" }, String(i < 4 ? i + 1 : i + 2).padStart(2, "0")), stage.title),
        ),
        body,
      );
    });
    const order = [...STAGES.slice(0, 4).map((s) => [s.id, s.title]), ["rack", "Рэк"], ...STAGES.slice(4).map((s) => [s.id, s.title])];
    this.flow = h(
      "nav",
      { class: "flow", "aria-label": "Цепочка по порядку сигнала" },
      h(
        "ol",
        { class: "flow__list" },
        ...order.map(([id, title], i) => {
          const state = h("span", { class: "flow__state" });
          this.#flowState.set(id as string, state);
          return h(
            "li",
            { class: "flow__item" },
            h("a", { class: "flow__link", href: `#panel-${id}` }, h("span", { class: "flow__n num" }, String(i + 1).padStart(2, "0")), h("span", { class: "flow__title" }, title as string), state),
          );
        }),
      ),
    );
  }

  #row(spec: SettingSpec, on: ChainHandlers): Row {
    const live = NOT_LIVE.has(spec.key) ? h("span", { class: "row__badge" }, "только в рендере") : null;
    const detail = spec.basic ? "" : " is-detail";
    if (spec.kind === "bool") {
      const sw = new Switch(spec.label, spec.default === true, (v) => on.commit(spec.key, v));
      const root = h(
        "div",
        { class: `row row--switch${detail}` },
        h("span", { class: "row__label" }, spec.label, live),
        sw.root,
        h("span", { class: "row__help" }, spec.help),
      );
      return {
        spec,
        root,
        set: (v) => sw.set(v === true),
        disable: (off) => {
          root.dataset.disabled = String(off);
          sw.root.disabled = off;
        },
      };
    }
    if (spec.choices.length > 1 && spec.choices.length <= 4) {
      const seg = new Segmented(
        spec.label,
        spec.choices.map((c) => ({ value: c, label: spec.key === "voice_octave" ? octaveLabel(c) : formatValue(c, spec.unit, spec.step) })),
        Number(spec.default),
        (v) => on.commit(spec.key, v),
        "seg seg--small",
      );
      const root = h(
        "div",
        { class: `row row--choice${detail}` },
        h("span", { class: "row__label" }, spec.label, live),
        seg.root,
        h("span", { class: "row__help" }, spec.help),
      );
      return { spec, root, set: (v) => seg.set(Number(v)), disable: (off) => (root.dataset.disabled = String(off)) };
    }
    const fader = new Fader(
      {
        label: spec.label,
        unit: spec.unit,
        low: spec.low,
        high: spec.high,
        step: spec.step,
        default: Number(spec.default),
        help: spec.help,
        log: isLog(spec.low, spec.high, spec.unit),
        integer: spec.kind === "int",
      },
      Number(spec.default),
      { input: (v) => on.input(spec.key, v), commit: (v) => on.commit(spec.key, v) },
    );
    if (detail) fader.root.classList.add("is-detail");
    if (live) fader.root.querySelector(".fader__label")?.append(live);
    return {
      spec,
      root: fader.root,
      set: (v) => fader.set(Number(v)),
      disable: (off) => fader.setDisabled(off, "Сначала включи переключатель выше"),
    };
  }

  show(settings: Settings): void {
    for (const row of this.#rows) {
      const v = settings[row.spec.key];
      if (typeof v === "number" || typeof v === "boolean") row.set(v);
      const waits = WAITS[row.spec.key] ?? [];
      row.disable(waits.some(([key, want]) => (settings[key] === true) !== want));
    }
    const off = (key: string) => settings[key] === false;
    const rack = Array.isArray(settings.rack) ? settings.rack : [];
    const states: Record<string, string> = {
      voice: off("chain_on") ? "без цепочки" : "",
      eq: settings.eq_fit === true ? "подгонка" : "",
      layers: "",
      fx: "",
      rack: rack.length ? `${rack.filter((e) => e.on).length}/${rack.length}` : "пусто",
      beat: "",
      master: off("master_on") ? "выкл" : "",
    };
    for (const [id, el] of this.#flowState) el.textContent = states[id] ?? "";
  }
}
