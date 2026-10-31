// A round knob over a position 0..1: drag up or down, the wheel, arrows; a double click goes back
// to the value it was made with. The value text under it comes from the caller.

import { h } from "../ui/dom.ts";

export const KNOB_DRAG_PX = 200;
const FINE = 4;
const WHEEL_STEP = 0.05;
const WHEEL_FINE = 0.01;

const unit = (v: number): number => Math.min(1, Math.max(0, Math.round(v * 1e4) / 1e4));

// the position after a drag of `dy` pixels (down is positive) from `start`
export function dragKnob(start: number, dy: number, fine: boolean): number {
  return unit(start - dy / (KNOB_DRAG_PX * (fine ? FINE : 1)));
}

export function wheelKnob(value: number, deltaY: number, fine: boolean): number {
  if (deltaY === 0) return unit(value);
  return unit(value - Math.sign(deltaY) * (fine ? WHEEL_FINE : WHEEL_STEP));
}

export interface KnobHandlers {
  // while it moves: heard at once
  input: (value: number) => void;
  // where it was let go
  commit: (value: number) => void;
}

export class KnobControl {
  readonly root: HTMLElement;
  readonly #dial: HTMLElement;
  readonly #text: HTMLElement;
  readonly #on: KnobHandlers;
  readonly #reset: () => number;
  #value: number;
  #disabled = false;

  constructor(name: string, value: number, reset: () => number, on: KnobHandlers) {
    this.#on = on;
    this.#value = unit(value);
    this.#reset = reset;
    this.#dial = h("div", {
      class: "knob__dial",
      role: "slider",
      tabindex: 0,
      "aria-label": name,
      "aria-valuemin": 0,
      "aria-valuemax": 100,
      title: "Тяни вверх или вниз, колесо; Shift — точнее; двойной клик — по умолчанию",
    });
    this.#text = h("div", { class: "knob__val" });
    this.root = h("div", { class: "knob" }, this.#dial, h("div", { class: "knob__name" }, name), this.#text);
    this.#paint();
    this.#dial.addEventListener("pointerdown", (e) => this.#drag(e));
    this.#dial.addEventListener("wheel", (e) => {
      if (this.#disabled) return;
      e.preventDefault();
      this.#change(wheelKnob(this.#value, e.deltaY, e.shiftKey), true);
    }, { passive: false });
    this.#dial.addEventListener("dblclick", () => {
      if (!this.#disabled) this.#change(unit(this.#reset()), true);
    });
    this.#dial.addEventListener("keydown", (e) => {
      if (this.#disabled) return;
      const by = e.shiftKey ? WHEEL_FINE : WHEEL_STEP;
      let next: number | null = null;
      if (e.key === "ArrowUp" || e.key === "ArrowRight") next = this.#value + by;
      else if (e.key === "ArrowDown" || e.key === "ArrowLeft") next = this.#value - by;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = 1;
      if (next === null) return;
      e.preventDefault();
      e.stopPropagation();
      this.#change(unit(next), true);
    });
  }

  get value(): number {
    return this.#value;
  }

  set(value: number, text: string): void {
    this.#value = unit(value);
    this.#text.textContent = text;
    this.#paint();
  }

  setText(text: string): void {
    this.#text.textContent = text;
  }

  setDisabled(off: boolean): void {
    this.#disabled = off;
    this.root.classList.toggle("knob--off", off);
    this.#dial.setAttribute("aria-disabled", String(off));
    this.#dial.tabIndex = off ? -1 : 0;
  }

  #change(value: number, commit: boolean): void {
    if (value === this.#value && !commit) return;
    this.#value = value;
    this.#paint();
    this.#on.input(value);
    if (commit) this.#on.commit(value);
  }

  #paint(): void {
    this.#dial.style.setProperty("--v", String(this.#value));
    this.#dial.setAttribute("aria-valuenow", String(Math.round(this.#value * 100)));
  }

  #drag(e: PointerEvent): void {
    if (this.#disabled || e.button !== 0) return;
    e.preventDefault();
    this.#dial.focus({ preventScroll: true });
    const start = this.#value;
    const y0 = e.clientY;
    this.#dial.setPointerCapture(e.pointerId);
    const move = (ev: PointerEvent): void => this.#change(dragKnob(start, ev.clientY - y0, ev.shiftKey), false);
    const up = (): void => {
      this.#dial.removeEventListener("pointermove", move);
      this.#dial.removeEventListener("pointerup", up);
      this.#dial.removeEventListener("pointercancel", up);
      if (this.#value !== start) this.#on.commit(this.#value);
    };
    this.#dial.addEventListener("pointermove", move);
    this.#dial.addEventListener("pointerup", up);
    this.#dial.addEventListener("pointercancel", up);
  }
}
