// The side panel: the selected sound's instrument params, its level and pan, and its rack.

import { isLog } from "../../core/control-math.ts";
import type { Library } from "../api.ts";
import { instrumentLabel, paramName } from "../labels.ts";
import { MAX_RACK, soundMix, type InstrumentSpec, type ParamValue, type RackSlotJson, type Score } from "../model.ts";
import type { EffectSpec, ParamSpec, UserEffect } from "../../types.ts";
import { Fader, Segmented } from "../../ui/controls.ts";
import { h } from "../../ui/dom.ts";
import { RackEditor, isInteger } from "../../ui/rack.ts";

export interface SideHandlers {
  param: (sound: string, name: string, value: ParamValue, commit: boolean) => void;
  gain: (sound: string, db: number, commit: boolean) => void;
  pan: (sound: string, pan: number, commit: boolean) => void;
  rack: (sound: string, rack: RackSlotJson[], commit: boolean) => void;
}

interface Control {
  set: (v: ParamValue) => void;
}

// replaceChildren takes no nulls, unlike h()
const present = (...nodes: (Node | null)[]): Node[] => nodes.filter((n): n is Node => n !== null);

export function toUser(rack: readonly RackSlotJson[]): UserEffect[] {
  return rack.map((s) => ({ effect: s.effect, on: s.on ?? true, params: { ...(s.params ?? {}) }, automation: [] }));
}

export function fromUser(rack: readonly UserEffect[]): RackSlotJson[] {
  return rack.map((e) => ({ effect: e.effect, on: e.on, params: { ...e.params } }));
}

export class SoundPanel {
  readonly root: HTMLElement;
  readonly #body: HTMLElement;
  readonly #on: SideHandlers;
  readonly #effects: readonly EffectSpec[];
  #key = "";
  #controls = new Map<string, Control>();
  #gain: Fader | null = null;
  #pan: Fader | null = null;
  #rack: RackEditor | null = null;

  constructor(effects: readonly EffectSpec[], on: SideHandlers) {
    this.#effects = effects;
    this.#on = on;
    this.#body = h("div", { class: "side__body" });
    this.root = h("aside", { class: "side", "aria-label": "Звук" }, this.#body);
  }

  show(score: Score, sound: string | null, instruments: ReadonlyMap<string, InstrumentSpec>, libraries: readonly Library[]): void {
    const s = sound ? score.sounds[sound] : undefined;
    if (!sound || !s) {
      this.#key = "";
      this.#body.replaceChildren(h("p", { class: "side__empty" }, "Выбери звук: клик по его имени в сетке."));
      return;
    }
    const inst = instruments.get(s.instrument);
    const key = `${sound}/${s.instrument}`;
    if (key !== this.#key) {
      this.#key = key;
      this.#build(score, sound, inst, libraries);
    }
    for (const p of inst?.params ?? []) this.#controls.get(p.name)?.set(s.params?.[p.name] ?? p.default);
    const mix = soundMix(score, sound);
    this.#gain?.set(mix.gain_db);
    this.#pan?.set(mix.pan);
    this.#rack?.show(toUser(mix.rack));
  }

  #build(score: Score, sound: string, inst: InstrumentSpec | undefined, libraries: readonly Library[]): void {
    const s = score.sounds[sound];
    this.#controls = new Map();
    const params = h("div", { class: "side__params" });
    for (const p of inst?.params ?? []) {
      const control = this.#param(sound, p);
      params.append(control.root);
      this.#controls.set(p.name, control);
    }
    const mix = soundMix(score, sound);
    this.#gain = new Fader({ label: "Громкость", unit: "dB", low: -60, high: 12, default: 0, step: 0.5 }, mix.gain_db, {
      input: (v) => this.#on.gain(sound, v, false),
      commit: (v) => this.#on.gain(sound, v, true),
    });
    this.#pan = new Fader({ label: "Панорама", unit: "", low: -1, high: 1, default: 0, step: 0.01, help: "−1 слева, 1 справа" }, mix.pan, {
      input: (v) => this.#on.pan(sound, v, false),
      commit: (v) => this.#on.pan(sound, v, true),
    });
    this.#rack = new RackEditor(
      this.#effects,
      {
        input: (rack) => this.#on.rack(sound, fromUser(rack), false),
        commit: (rack) => this.#on.rack(sound, fromUser(rack), true),
      },
      { max: MAX_RACK, n: "", title: "Рэк звука", lede: `До ${MAX_RACK} эффектов по порядку. Меняется вживую с плавным переходом.`, empty: "Пусто: звук идёт без эффектов." },
    );
    const library = s?.library ? libraries.find((l) => l.name === s.library) : undefined;
    this.#body.replaceChildren(
      ...present(
      h(
        "header",
        { class: "side__head" },
        h("h2", { class: "side__title" }, sound),
        h("span", { class: "side__inst" }, instrumentLabel(s?.instrument ?? "?")),
      ),
      s?.library ? this.#library(s.library, library) : null,
      inst ? null : h("p", { class: "side__warn" }, `Инструмента ${s?.instrument ?? ""} нет в движке.`),
      h("h3", { class: "side__sub" }, "Инструмент"),
      params,
      h("h3", { class: "side__sub" }, "Микс"),
      this.#gain.root,
      this.#pan.root,
      mix.sidechain ? h("p", { class: "side__note" }, "Сайдчейн от ", h("span", { class: "num" }, mix.sidechain), " (меняется в JSON)") : null,
      this.#rack.root,
      ),
    );
  }

  #library(name: string, lib: Library | undefined): HTMLElement {
    const lines: (HTMLElement | null)[] = [h("p", { class: "side__lib" }, "Библиотека ", h("span", { class: "num" }, name), lib?.licence ? ` · ${lib.licence}` : "")];
    if (lib && !lib.commercial) lines.push(h("p", { class: "side__warn" }, "Не для коммерции: бит с ней не продать."));
    if (lib && !lib.fetched) {
      lines.push(
        h("p", { class: "side__warn" }, lib.kind === "local_only" ? "Только локальная: страница её не играет." : "Не скачана, звука нет. Скачай:"),
        lib.kind === "local_only" ? null : h("code", { class: "side__cmd num" }, `redline beat sounds fetch ${name}`),
      );
    }
    if (!lib) lines.push(h("p", { class: "side__warn" }, "Эта библиотека не нужна ни одной ноте или её нет в списке."));
    return h("div", { class: "side__libs" }, ...lines);
  }

  #param(sound: string, p: ParamSpec): Control & { root: HTMLElement } {
    const label = paramName(p.name);
    const rebuild = p.fixed_while_playing
      ? h("span", { class: "row__badge side__rebuild", title: "Меняется пересборкой звука: короткий провал вживую" }, "пересборка")
      : null;
    if (p.choices && p.choices.length) {
      const choices = p.choices;
      const pick = (v: ParamValue): string => (typeof v === "string" ? v : (choices[Math.round(v)] ?? choices[0] ?? ""));
      const seg: Segmented<string> = new Segmented(label, choices.map((c) => ({ value: c, label: c })), pick(p.default), (v) => {
        seg.set(v);
        this.#on.param(sound, p.name, v, true);
      }, "seg seg--small");
      return {
        root: h("div", { class: "row row--choice" }, h("span", { class: "row__label" }, label, rebuild), seg.root),
        set: (v) => seg.set(pick(v)),
      };
    }
    const integer = isInteger(p) || p.name === "voices";
    const fader = new Fader(
      { label, unit: p.unit, low: p.low, high: p.high, default: p.default, log: isLog(p.low, p.high, p.unit), integer, step: integer ? 1 : undefined },
      p.default,
      {
        // a rebuild-only param would rebuild the player at every pixel of a drag
        input: (v) => (rebuild ? undefined : this.#on.param(sound, p.name, v, false)),
        commit: (v) => this.#on.param(sound, p.name, v, true),
      },
    );
    if (rebuild) fader.root.querySelector(".fader__label")?.append(rebuild);
    return { root: fader.root, set: (v) => typeof v === "number" && fader.set(v) };
  }
}
