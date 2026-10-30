// The user's rack: effect cards in the order the vocal goes through them, added from a
// searchable list, dragged to reorder, bypassed, removed; their params as faders.

import { isLog } from "../core/control-math.ts";
import { paramNumber } from "../core/diff.ts";
import { FAMILIES, addable, effectInfo, paramLabel } from "../core/families.ts";
import type { EffectSpec, ParamSpec, UserEffect } from "../types.ts";
import { Dropdown, Fader, Segmented, Switch, type Handlers } from "./controls.ts";
import { dismissable, h, icon } from "./dom.ts";

const INTEGER_UNITS = new Set(["bits", "samples", "voices", "degrees", "midi", "notes", "bands"]);
const INTEGER_NAMES = new Set(["stages", "steps", "repeats", "seed", "degree", "octave", "voices"]);
export const MAX_USER_EFFECTS = 32;

interface Card {
  root: HTMLElement;
  sw: Switch;
  params: Map<string, { set: (v: number | string) => void }>;
}

function isInteger(p: ParamSpec): boolean {
  return INTEGER_UNITS.has(p.unit) || INTEGER_NAMES.has(p.name) || /^s\d+$/.test(p.name);
}

function shape(rack: readonly UserEffect[]): string {
  return JSON.stringify(rack.map((e) => [e.effect, e.automation.map((l) => l.param)]));
}

export class RackEditor {
  readonly root: HTMLElement;
  readonly #list: HTMLElement;
  readonly #add: HTMLButtonElement;
  readonly #count: HTMLElement;
  readonly #specs: Map<string, EffectSpec>;
  readonly #addable: EffectSpec[];
  readonly #on: Handlers<UserEffect[]>;
  #rack: UserEffect[] = [];
  #cards: Card[] = [];
  #shape = "";
  #dragFrom: number | null = null;

  constructor(effects: readonly EffectSpec[], on: Handlers<UserEffect[]>) {
    this.#specs = new Map(effects.map((e) => [e.effect, e]));
    this.#addable = addable(effects);
    this.#on = on;
    this.#list = h("ol", { class: "rack__list", "aria-label": "Эффекты рэка по порядку" });
    this.#count = h("span", { class: "rack__count num" });
    this.#add = h("button", { class: "btn btn--line", type: "button", "aria-haspopup": "dialog" }, icon("M12 5v14|M5 12h14"), "Добавить эффект");
    this.#add.addEventListener("click", () => this.#openAdder());
    this.root = h(
      "section",
      { class: "rack panel", id: "panel-rack", "aria-labelledby": "rack-title", "data-basic": "true" },
      h(
        "header",
        { class: "panel__head" },
        h("h2", { class: "panel__title", id: "rack-title" }, h("span", { class: "panel__n num" }, "05"), "Рэк"),
        this.#count,
        effects.length ? this.#add : h("span", { class: "panel__note" }, "Рэк недоступен: модуль redline_dsp не собран"),
      ),
      h("p", { class: "panel__lede" }, "Эффекты по порядку, после встроенных и до бита. Тяни за ручку, чтобы переставить."),
      this.#list,
    );
  }

  show(rack: readonly UserEffect[]): void {
    this.#rack = structuredClone([...rack]);
    const s = shape(this.#rack);
    if (s !== this.#shape) {
      this.#shape = s;
      this.#build();
    }
    this.#rack.forEach((e, i) => {
      const card = this.#cards[i];
      if (!card) return;
      card.sw.set(e.on);
      card.root.dataset.on = String(e.on);
      const spec = this.#specs.get(e.effect);
      for (const p of spec?.params ?? []) card.params.get(p.name)?.set(e.params[p.name] ?? p.default);
    });
    this.#count.textContent = `${this.#rack.length} из ${MAX_USER_EFFECTS}`;
    this.#add.disabled = this.#rack.length >= MAX_USER_EFFECTS;
  }

  #emit(rack: UserEffect[], commit: boolean): void {
    this.#rack = rack;
    if (commit) this.#on.commit(structuredClone(rack));
    else this.#on.input(structuredClone(rack));
  }

  #edit(index: number, f: (e: UserEffect) => void, commit = true): void {
    const rack = structuredClone(this.#rack);
    const e = rack[index];
    if (!e) return;
    f(e);
    this.#emit(rack, commit);
  }

  #move(from: number, to: number): void {
    if (to < 0 || to >= this.#rack.length || from === to) return;
    const rack = structuredClone(this.#rack);
    const [e] = rack.splice(from, 1);
    if (!e) return;
    rack.splice(to, 0, e);
    this.#emit(rack, true);
    requestAnimationFrame(() => this.#list.querySelectorAll<HTMLElement>(".fx__grip")[to]?.focus());
  }

  #build(): void {
    this.#cards = this.#rack.map((e, i) => this.#card(e, i));
    this.#list.replaceChildren(...this.#cards.map((c) => c.root));
    if (!this.#rack.length) {
      this.#list.append(h("li", { class: "rack__empty" }, "Пусто. Добавь эффект — он встанет после встроенной цепочки."));
    }
  }

  #card(e: UserEffect, index: number): Card {
    const spec = this.#specs.get(e.effect);
    const info = effectInfo(e.effect);
    const grip = h("button", { class: "fx__grip", type: "button", "aria-label": `Переставить: ${info.label}. Стрелки вверх и вниз` }, icon("M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01", "icon icon--grip"));
    const sw = new Switch(`Включён: ${info.label}`, e.on, (on) => this.#edit(index, (x) => (x.on = on)));
    const remove = h("button", { class: "fx__remove", type: "button", "aria-label": `Убрать: ${info.label}` }, icon("M6 6l12 12|M18 6L6 18"));
    remove.addEventListener("click", () => {
      const rack = structuredClone(this.#rack);
      rack.splice(index, 1);
      this.#emit(rack, true);
    });
    const params = new Map<string, { set: (v: number | string) => void }>();
    const body = h("div", { class: "fx__params" });
    const automated = new Set(e.automation.map((l) => l.param));
    for (const p of spec?.params ?? []) {
      const control = this.#param(index, p, e.params[p.name], automated.has(p.name));
      params.set(p.name, control);
      body.append(control.root);
    }
    const root = h(
      "li",
      { class: "fx", "data-on": String(e.on), "data-family": info.family },
      h(
        "header",
        { class: "fx__head" },
        grip,
        h("span", { class: "fx__n num" }, String(index + 1).padStart(2, "0")),
        h("span", { class: "fx__name" }, info.label, h("span", { class: "fx__id" }, e.effect)),
        sw.root,
        remove,
      ),
      spec?.offline_only ? h("p", { class: "fx__note" }, "Вживую — с большой задержкой; точно звучит в точном рендере") : null,
      spec ? null : h("p", { class: "fx__note" }, "Этого эффекта нет в движке страницы"),
      automated.size ? h("p", { class: "fx__note" }, `Автоматизация: ${[...automated].map(paramLabel).join(", ")}`) : null,
      body,
    );
    this.#dragAndKeys(root, grip, index);
    return { root, sw, params };
  }

  #param(index: number, p: ParamSpec, raw: number | string | undefined, automated: boolean) {
    const label = paramLabel(p.name);
    if (p.choices) {
      const choices = p.choices;
      const options = choices.map((c) => ({ value: c, label: c }));
      const current = choices[paramNumber(p, raw)] ?? choices[0] ?? "";
      const set = (v: string) => this.#edit(index, (x) => (x.params = { ...x.params, [p.name]: v }));
      if (choices.length <= 4) {
        const seg = new Segmented(label, options, current, set, "seg seg--small");
        return {
          root: h("div", { class: "row row--choice" }, h("span", { class: "row__label" }, label), seg.root),
          set: (v: number | string) => seg.set(choices[paramNumber(p, v)] ?? ""),
        };
      }
      const drop = new Dropdown(label, options, current, set);
      return {
        root: h("div", { class: "row row--choice" }, h("span", { class: "row__label" }, label), drop.root),
        set: (v: number | string) => drop.set(choices[paramNumber(p, v)] ?? null),
      };
    }
    const value = typeof raw === "number" ? raw : p.default;
    const fader = new Fader(
      { label, unit: p.unit, low: p.low, high: p.high, default: p.default, log: isLog(p.low, p.high, p.unit), integer: isInteger(p), step: isInteger(p) ? 1 : undefined },
      value,
      {
        input: (v) => this.#edit(index, (x) => (x.params = { ...x.params, [p.name]: v }), false),
        commit: (v) => this.#edit(index, (x) => (x.params = { ...x.params, [p.name]: v })),
      },
    );
    if (automated) fader.setDisabled(true, "Этот параметр ведёт автоматизация");
    return { root: fader.root, set: (v: number | string) => typeof v === "number" && fader.set(v) };
  }

  #dragAndKeys(card: HTMLElement, grip: HTMLButtonElement, index: number): void {
    grip.addEventListener("keydown", (e) => {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        this.#move(index, index - 1);
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        this.#move(index, index + 1);
      }
    });
    // only the grip starts a drag, so faders inside the card keep their own pointer
    grip.addEventListener("pointerdown", () => (card.draggable = true));
    card.addEventListener("dragstart", (e) => {
      this.#dragFrom = index;
      card.dataset.dragging = "true";
      e.dataTransfer?.setData("text/plain", String(index));
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    });
    card.addEventListener("dragend", () => {
      card.draggable = false;
      card.dataset.dragging = "false";
      this.#dragFrom = null;
      for (const c of this.#list.children) (c as HTMLElement).dataset.drop = "";
    });
    card.addEventListener("dragover", (e) => {
      if (this.#dragFrom === null) return;
      e.preventDefault();
      const rect = card.getBoundingClientRect();
      card.dataset.drop = e.clientY < rect.top + rect.height / 2 ? "before" : "after";
    });
    card.addEventListener("dragleave", () => (card.dataset.drop = ""));
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      const from = this.#dragFrom;
      const where = card.dataset.drop;
      card.dataset.drop = "";
      if (from === null) return;
      let to = where === "after" ? index + 1 : index;
      if (from < to) to -= 1;
      this.#move(from, to);
    });
  }

  #openAdder(): void {
    if (this.#add.getAttribute("aria-expanded") === "true") return;
    const search = h("input", { class: "adder__search", type: "search", placeholder: "Найти: дилей, хорус, pitch…", "aria-label": "Найти эффект" });
    const list = h("div", { class: "adder__list" });
    const panel = h("div", { class: "adder", role: "dialog", "aria-label": "Добавить эффект" }, search, list);
    const render = () => {
      const q = search.value.trim().toLowerCase();
      list.replaceChildren();
      for (const family of FAMILIES) {
        const items = this.#addable.filter((e) => {
          const info = effectInfo(e.effect);
          return info.family === family && (!q || info.label.toLowerCase().includes(q) || e.effect.includes(q));
        });
        if (!items.length) continue;
        list.append(
          h("h3", { class: "adder__family" }, family),
          ...items.map((e) => {
            const b = h("button", { class: "adder__item", type: "button" }, effectInfo(e.effect).label, h("span", { class: "adder__id" }, e.effect));
            b.addEventListener("click", () => {
              close();
              this.#emit([...structuredClone(this.#rack), { effect: e.effect, on: true, params: {}, automation: [] }], true);
              this.#add.focus();
            });
            return b;
          }),
        );
      }
      if (!list.children.length) list.append(h("p", { class: "adder__none" }, "Ничего не нашлось"));
    };
    search.addEventListener("input", render);
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") list.querySelector<HTMLButtonElement>(".adder__item")?.click();
      if (e.key === "ArrowDown") {
        e.preventDefault();
        list.querySelector<HTMLButtonElement>(".adder__item")?.focus();
      }
    });
    list.addEventListener("keydown", (e) => {
      const items = [...list.querySelectorAll<HTMLButtonElement>(".adder__item")];
      const at = items.findIndex((i) => i === document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = at + (e.key === "ArrowDown" ? 1 : -1);
        if (next < 0) search.focus();
        else items[Math.min(items.length - 1, next)]?.focus();
      }
    });
    render();
    this.#add.after(panel);
    this.#add.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => (panel.dataset.open = "true"));
    search.focus();
    const close = dismissable(panel, this.#add, () => {
      panel.remove();
      this.#add.setAttribute("aria-expanded", "false");
    });
  }
}
