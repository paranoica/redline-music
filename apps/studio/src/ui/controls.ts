// The controls: a fader for numbers, a switch, a segmented choice and a dropdown list.

import { clamp, dragTo, fromNorm, stepBy, toNorm, type Range } from "../core/control-math.ts";
import { formatValue, unitLabel } from "../core/format.ts";
import { dismissable, h, nextId } from "./dom.ts";

export interface FaderSpec extends Range {
  label: string;
  unit: string;
  default: number;
  help?: string;
  integer?: boolean;
}

export interface Handlers<T> {
  // while it moves: heard at once, not yet a step of the history
  input: (value: T) => void;
  // where it was let go: one step of the history
  commit: (value: T) => void;
}

// pixels for the whole range: the bar's width, so a drag follows the pointer
function parseTyped(text: string): number {
  return Number(text.replace(",", ".").replace("−", "-").replace(/[^\d.eE+-]/g, ""));
}

export class Fader {
  readonly root: HTMLElement;
  readonly #bar: HTMLElement;
  readonly #fill: HTMLElement;
  readonly #thumb: HTMLElement;
  readonly #readout: HTMLButtonElement;
  readonly #spec: FaderSpec;
  readonly #on: Handlers<number>;
  #value: number;
  #disabled = false;

  constructor(spec: FaderSpec, value: number, on: Handlers<number>) {
    this.#spec = spec;
    this.#on = on;
    this.#value = value;
    const labelId = nextId("fader");
    const helpId = spec.help ? nextId("help") : undefined;
    this.#fill = h("span", { class: "fader__fill" });
    this.#thumb = h("span", { class: "fader__thumb" });
    this.#bar = h(
      "span",
      {
        class: "fader__bar",
        role: "slider",
        tabindex: 0,
        "aria-labelledby": labelId,
        "aria-describedby": helpId,
        "aria-valuemin": spec.low,
        "aria-valuemax": spec.high,
      },
      h("span", { class: "fader__track" }, this.#fill),
      this.#thumb,
    );
    this.#readout = h("button", { class: "fader__value num", type: "button", title: "Ввести значение" });
    this.root = h(
      "div",
      { class: "fader" },
      h("span", { class: "fader__label", id: labelId }, spec.label),
      this.#bar,
      this.#readout,
      spec.help ? h("span", { class: "fader__help", id: helpId }, spec.help) : null,
    );
    this.#wire();
    this.#show();
  }

  get value(): number {
    return this.#value;
  }

  set(value: number): void {
    this.#value = value;
    this.#show();
  }

  setDisabled(disabled: boolean, why = ""): void {
    this.#disabled = disabled;
    this.root.dataset.disabled = String(disabled);
    this.#bar.setAttribute("aria-disabled", String(disabled));
    this.#bar.tabIndex = disabled ? -1 : 0;
    this.#readout.disabled = disabled;
    this.root.title = disabled ? why : "";
  }

  #fit(v: number): number {
    const c = clamp(v, this.#spec);
    return this.#spec.integer ? Math.round(c) : c;
  }

  #show(): void {
    const s = this.#spec;
    const n = toNorm(this.#value, s);
    // a range across zero fills from zero, so a cut and a boost read apart
    const zero = s.low < 0 && s.high > 0 && !s.log ? toNorm(0, s) : 0;
    const from = Math.min(n, zero);
    this.#fill.style.setProperty("--from", String(from));
    this.#fill.style.setProperty("--to", String(Math.max(n, zero)));
    this.#thumb.style.setProperty("--at", String(n));
    const text = formatValue(this.#value, s.unit, s.step);
    this.#readout.textContent = text;
    this.#bar.setAttribute("aria-valuenow", String(this.#value));
    this.#bar.setAttribute("aria-valuetext", text);
    this.root.dataset.moved = String(Math.abs(this.#value - s.default) > 1e-9);
  }

  #change(v: number, commit: boolean): void {
    const next = this.#fit(v);
    const moved = next !== this.#value;
    this.#value = next;
    this.#show();
    if (commit) this.#on.commit(next);
    else if (moved) this.#on.input(next);
  }

  #wire(): void {
    const s = this.#spec;
    let drag: { x: number; value: number; moved: boolean; id: number } | null = null;
    this.#bar.addEventListener("pointerdown", (e) => {
      if (this.#disabled || e.button !== 0) return;
      e.preventDefault();
      this.#bar.focus();
      this.#bar.setPointerCapture(e.pointerId);
      const rect = this.#bar.getBoundingClientRect();
      const onThumb = Math.abs(e.clientX - (rect.left + toNorm(this.#value, s) * rect.width)) < 10;
      // a press beside the thumb puts it there; a press on it only grabs it
      if (!onThumb) this.#change(fromNorm((e.clientX - rect.left) / rect.width, s), false);
      drag = { x: e.clientX, value: this.#value, moved: !onThumb, id: e.pointerId };
      this.root.dataset.dragging = "true";
    });
    this.#bar.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.id) return;
      const width = this.#bar.getBoundingClientRect().width || 1;
      // shift re-anchors so switching to fine mid-drag does not jump
      const v = dragTo(drag.value, e.clientX - drag.x, width, s, e.shiftKey);
      if (e.shiftKey !== (this.root.dataset.fine === "true")) {
        this.root.dataset.fine = String(e.shiftKey);
        drag = { ...drag, x: e.clientX, value: this.#value };
        return;
      }
      drag.moved = true;
      this.#change(v, false);
    });
    const end = (e: PointerEvent) => {
      if (!drag || e.pointerId !== drag.id) return;
      const moved = drag.moved;
      drag = null;
      this.root.dataset.dragging = "false";
      this.root.dataset.fine = "false";
      if (moved) this.#on.commit(this.#value);
    };
    this.#bar.addEventListener("pointerup", end);
    this.#bar.addEventListener("pointercancel", end);
    this.#bar.addEventListener("dblclick", () => {
      if (!this.#disabled) this.#change(s.default, true);
    });
    this.#bar.addEventListener("keydown", (e) => {
      if (this.#disabled) return;
      const keys: Record<string, () => number> = {
        ArrowRight: () => stepBy(this.#value, s, 1, e.shiftKey),
        ArrowUp: () => stepBy(this.#value, s, 1, e.shiftKey),
        ArrowLeft: () => stepBy(this.#value, s, -1, e.shiftKey),
        ArrowDown: () => stepBy(this.#value, s, -1, e.shiftKey),
        PageUp: () => stepBy(this.#value, s, 1, true),
        PageDown: () => stepBy(this.#value, s, -1, true),
        Home: () => s.low,
        End: () => s.high,
        Delete: () => s.default,
        Backspace: () => s.default,
      };
      const f = keys[e.key];
      if (!f) return;
      e.preventDefault();
      this.#change(f(), true);
    });
    this.#readout.addEventListener("click", () => this.#type());
  }

  // the readout turns into a field; Enter takes the number, Escape leaves it
  #type(): void {
    const s = this.#spec;
    const input = h("input", {
      class: "fader__input num",
      type: "text",
      inputmode: "decimal",
      value: String(Number(this.#value.toPrecision(6))),
      "aria-label": `${s.label}, ${unitLabel(s.unit) || "значение"}`,
    });
    this.#readout.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = (take: boolean) => {
      if (done) return;
      done = true;
      const v = parseTyped(input.value);
      input.replaceWith(this.#readout);
      if (take && Number.isFinite(v)) this.#change(v, true);
      this.#readout.focus();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") {
        e.stopPropagation();
        finish(false);
      }
    });
    input.addEventListener("blur", () => finish(true));
  }
}

export class Switch {
  readonly root: HTMLButtonElement;
  #on: boolean;

  constructor(label: string, on: boolean, change: (on: boolean) => void, className = "switch") {
    this.#on = on;
    this.root = h(
      "button",
      { class: className, type: "button", role: "switch", "aria-label": label },
      h("span", { class: "switch__knob", "aria-hidden": "true" }),
    );
    this.root.addEventListener("click", () => change(!this.#on));
    this.set(on);
  }

  set(on: boolean): void {
    this.#on = on;
    this.root.setAttribute("aria-checked", String(on));
  }
}

export interface Option<T> {
  value: T;
  label: string;
}

// a few choices side by side; the marker slides to the chosen one
export class Segmented<T> {
  readonly root: HTMLElement;
  readonly #buttons: HTMLButtonElement[];
  readonly #marker: HTMLElement;
  readonly #options: Option<T>[];

  constructor(label: string, options: Option<T>[], value: T, change: (value: T) => void, className = "seg") {
    this.#options = options;
    this.#marker = h("span", { class: "seg__marker", "aria-hidden": "true" });
    this.#buttons = options.map((o) => {
      const b = h("button", { class: "seg__item", type: "button", role: "radio" }, o.label);
      b.addEventListener("click", () => change(o.value));
      return b;
    });
    this.root = h("div", { class: className, role: "radiogroup", "aria-label": label }, this.#marker, ...this.#buttons);
    this.root.style.setProperty("--count", String(options.length));
    this.root.addEventListener("keydown", (e) => {
      const at = this.#buttons.findIndex((b) => b === document.activeElement);
      if (at < 0) return;
      const by = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!by) return;
      e.preventDefault();
      const next = (at + by + options.length) % options.length;
      this.#buttons[next]?.focus();
      const o = options[next];
      if (o) change(o.value);
    });
    this.set(value);
  }

  button(value: T): HTMLButtonElement | undefined {
    return this.#buttons[this.#options.findIndex((o) => o.value === value)];
  }

  set(value: T): void {
    const at = Math.max(0, this.#options.findIndex((o) => o.value === value));
    this.#buttons.forEach((b, i) => {
      b.setAttribute("aria-checked", String(i === at));
      b.tabIndex = i === at ? 0 : -1;
    });
    this.root.style.setProperty("--at", String(at));
  }
}

// a button that opens a list; for more choices than a segmented control holds
export class Dropdown<T> {
  readonly root: HTMLElement;
  readonly #button: HTMLButtonElement;
  readonly #text: HTMLElement;
  #options: Option<T>[];
  #value: T | null;
  readonly #change: (value: T) => void;
  readonly #placeholder: string;

  constructor(label: string, options: Option<T>[], value: T | null, change: (value: T) => void, placeholder = "выбрать") {
    this.#options = options;
    this.#value = value;
    this.#change = change;
    this.#placeholder = placeholder;
    this.#text = h("span", { class: "drop__text" });
    this.#button = h(
      "button",
      { class: "drop__button", type: "button", "aria-haspopup": "listbox", "aria-expanded": "false", "aria-label": label },
      this.#text,
      h("span", { class: "drop__chevron", "aria-hidden": "true" }),
    );
    this.root = h("div", { class: "drop" }, this.#button);
    this.#button.addEventListener("click", () => this.#open());
    this.#show();
  }

  setOptions(options: Option<T>[]): void {
    this.#options = options;
    this.#show();
  }

  set(value: T | null): void {
    this.#value = value;
    this.#show();
  }

  setDisabled(disabled: boolean): void {
    this.#button.disabled = disabled;
  }

  #show(): void {
    const chosen = this.#options.find((o) => o.value === this.#value);
    this.#text.textContent = chosen ? chosen.label : this.#placeholder;
    this.root.dataset.empty = String(!chosen);
  }

  #open(): void {
    if (this.#button.getAttribute("aria-expanded") === "true") return;
    const list = h("ul", { class: "drop__list", role: "listbox", tabindex: -1 });
    const items = this.#options.map((o) => {
      const li = h("li", { class: "drop__item", role: "option", tabindex: -1, "aria-selected": String(o.value === this.#value) }, o.label);
      li.addEventListener("click", () => {
        close();
        this.#value = o.value;
        this.#show();
        this.#change(o.value);
        this.#button.focus();
      });
      return li;
    });
    if (!items.length) list.append(h("li", { class: "drop__none" }, "пусто"));
    list.append(...items);
    this.root.append(list);
    this.#button.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => list.dataset.open = "true");
    const close = dismissable(list, this.#button, () => {
      list.remove();
      this.#button.setAttribute("aria-expanded", "false");
    });
    const start = Math.max(0, this.#options.findIndex((o) => o.value === this.#value));
    items[start]?.focus();
    list.addEventListener("keydown", (e) => {
      const at = items.findIndex((i) => i === document.activeElement);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const next = Math.min(items.length - 1, Math.max(0, at + (e.key === "ArrowDown" ? 1 : -1)));
        items[next]?.focus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        items[at]?.click();
      } else if (e.key === "Tab") {
        close();
      }
    });
  }
}
