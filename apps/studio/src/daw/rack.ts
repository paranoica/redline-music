// The channel rack of an own beat: every sound of the score as a row over one pattern, FL style.
// A drum row is the 16 steps of the bar shown, a note row a small picture of its notes that opens
// the piano roll. An edit here changes the pattern wherever it plays, so nothing is asked.

import { instrumentLabel } from "../beat/labels.ts";
import {
  clonePattern,
  drumSteps,
  ensureRow,
  isNoteRow,
  newPattern,
  notesInBar,
  notesOf,
  patternLabel,
  rackRows,
  setGain,
  setHit,
  setPan,
  setPatternBars,
  setSoundMuted,
  soundMix,
  STEPS_PER_BAR,
  type Hit,
  type InstrumentSpec,
  type RackRow,
  type Score,
  type ScoreEdit,
} from "../beat/model.ts";
import { h } from "../ui/dom.ts";
import { ROLL_NAMES, toggleHit } from "./drums.ts";
import { KnobControl } from "./knob.ts";

// ---------- the pattern shown ----------

const own = <T>(o: Record<string, T>, key: string): boolean => Object.hasOwn(o, key);

function blockPattern(score: Score, section: number | null): string | null {
  if (section === null || !Number.isInteger(section)) return null;
  const s = score.sections[section];
  return (s?.play ?? []).find((p) => own(score.patterns, p)) ?? null;
}

const firstPattern = (score: Score): string | null => Object.keys(score.patterns)[0] ?? null;

// The pattern the rack shows: the one picked in it, else the selected block's first. A pick that
// is gone (an undo, a delete) gives way to the score's first pattern; another seed drops it.
// the kind of row the lane the rack was opened from shows: the drums lane wants drum rows
export type RowWish = "drum" | "note" | null;

export class RackPick {
  #seed: number | null | undefined = undefined;
  #pick: string | null = null;

  current(score: Score, seed: number | null, section: number | null, wants: RowWish = null): string | null {
    if (seed !== this.#seed) {
      if (this.#seed !== undefined) this.#pick = null;
      this.#seed = seed;
    }
    if (this.#pick !== null) {
      if (own(score.patterns, this.#pick)) return this.#pick;
      this.#pick = firstPattern(score);
      return this.#pick;
    }
    if (wants !== null) {
      const fits = (p: string) => Object.values(score.patterns[p]?.rows ?? {}).some((r) => isNoteRow(r) === (wants === "note"));
      const played = section !== null && Number.isInteger(section) ? (score.sections[section]?.play ?? []) : [];
      const found = played.find((p) => own(score.patterns, p) && fits(p)) ?? Object.keys(score.patterns).find(fits);
      if (found !== undefined) return found;
    }
    return blockPattern(score, section) ?? firstPattern(score);
  }

  choose(pattern: string): void {
    this.#pick = pattern;
  }

  // a block picked on the timeline: the rack shows its pattern again
  follow(): void {
    this.#pick = null;
  }
}

// ---------- a press on a step ----------

// "click": a rest gets a hit or the roll picked, a hit goes; "accent": Shift, x → X → g → x;
// "clear": a right click
export type StepGesture = "click" | "accent" | "clear";

const ACCENT: Record<Hit, Hit> = { ".": "X", x: "X", X: "g", g: "x", "2": "X", "3": "X", "4": "X", "6": "X" };

const refuse = (reason: string): ScoreEdit => ({ ok: false, reason });

export function rackStep(score: Score, specs: readonly InstrumentSpec[], pattern: string, sound: string, step: number, gesture: StepGesture, roll: number): ScoreEdit {
  const p = own(score.patterns, pattern) ? score.patterns[pattern] : undefined;
  if (!p) return refuse(`Паттерна ${pattern} нет.`);
  let s = score;
  if (!own(p.rows ?? {}, sound)) {
    // clearing a step of a row that is not there leaves nothing to clear
    if (gesture === "clear") return { ok: true, score };
    const made = ensureRow(score, specs, pattern, sound);
    if (!made.ok) return made;
    s = made.score;
  }
  const row = s.patterns[pattern]?.rows[sound];
  if (row === undefined || isNoteRow(row)) return refuse(`У звука ${sound} ноты, а не удары.`);
  const hits = drumSteps(s, pattern, sound);
  const hit = Number.isInteger(step) && step >= 0 ? hits[step] : undefined;
  if (hit === undefined) return refuse(`Такого шага в паттерне ${pattern} нет.`);
  const next = gesture === "clear" ? "." : gesture === "accent" ? ACCENT[hit] : toggleHit(hit, roll);
  return { ok: true, score: next === hit ? s : setHit(s, pattern, sound, step, next) };
}

// a sound's kind by its instrument, else by the row it has in the pattern
export function rowKind(score: Score, specs: readonly InstrumentSpec[], pattern: string, sound: string): "drum" | "note" | null {
  const instrument = score.sounds[sound]?.instrument;
  const kind = specs.find((i) => i.instrument === instrument)?.kind;
  if (kind) return kind;
  const rows = own(score.patterns, pattern) ? (score.patterns[pattern]?.rows ?? {}) : {};
  const row = own(rows, sound) ? rows[sound] : undefined;
  if (row === undefined) return null;
  return isNoteRow(row) ? "note" : "drum";
}

// the words on a row another pattern of the same section holds
export function lockedText(score: Score, pattern: string): string {
  const label = patternLabel(score, pattern, Object.keys(score.patterns).indexOf(pattern));
  return `в паттерне ${label.replace(/^Паттерн /, "")}`;
}

// ---------- the knobs ----------

const GAIN_LO = -60;
const GAIN_HI = 12;
const unit = (v: number): number => Math.min(1, Math.max(0, v));
const onStep = (v: number, step: number): number => {
  const r = Number((Math.round(v / step) * step).toFixed(6));
  return r === 0 ? 0 : r;
};

export const gainAt = (position: number): number => onStep(GAIN_LO + (GAIN_HI - GAIN_LO) * unit(position), 0.5);
export const gainPosition = (db: number): number => unit((db - GAIN_LO) / (GAIN_HI - GAIN_LO));
export const panAt = (position: number): number => onStep(-1 + 2 * unit(position), 0.01);
export const panPosition = (pan: number): number => unit((pan + 1) / 2);

const gainText = (db: number): string => `${db > 0 ? "+" : db < 0 ? "−" : ""}${Math.abs(db)} дБ`;
const panText = (pan: number): string => (pan === 0 ? "центр" : `${pan < 0 ? "Л" : "П"} ${Math.round(Math.abs(pan) * 100)}`);

// ---------- the view ----------

export interface RackHost {
  score(): Score | null;
  specs(): readonly InstrumentSpec[];
  seed(): number | null;
  // the selected block's section, for the pattern shown first
  section(): number | null;
  // the kind of row the lane the rack was opened from shows, for the pattern shown first
  wants?(): RowWish;
  // an edit of the whole pattern; `live`: a step of a drag, heard but not yet an undo step
  edit(next: Score, live?: boolean): boolean;
  // the note row the piano roll edits; `open` switches to the roll
  notes(pattern: string, sound: string, open: boolean): void;
  say(text: string): void;
  // hearing one sound on its own; not offered yet
  preview?(sound: string): void;
}

const COLOURS: Record<string, string> = {
  kick: "--c-kick",
  snare: "--c-snare",
  rim: "--c-snare",
  clap: "--c-clap",
  snap: "--c-clap",
  hat_closed: "--c-hat",
  oneshot_hat: "--c-hat",
  shaker: "--c-hat",
  hat_open: "--c-ohat",
  oneshot_open_hat: "--c-ohat",
  crash: "--c-ohat",
  bass808: "--c-808",
  reese: "--c-808",
  sample808: "--c-808",
};

const colourOf = (instrument: string, kind: "drum" | "note" | null): string => COLOURS[instrument] ?? (kind === "note" ? "--c-keys" : "--c-perc");

const barWord = (n: number): string => (n === 1 ? "такт" : n < 5 ? "такта" : "тактов");

export class RackView {
  readonly root: HTMLElement;
  readonly #head: HTMLElement;
  readonly #rows: HTMLElement;
  readonly #host: RackHost;
  readonly #pick = new RackPick();
  #shown: string | null = null;
  #bar = 0;
  #roll = 0;
  #selected: string | null = null;
  // a knob is held: the rows are not drawn again under it
  #dragging = false;
  #stale = false;

  constructor(host: RackHost) {
    this.#host = host;
    this.#head = h("div", { class: "rhead" });
    this.#rows = h("div", { class: "rack" });
    this.root = h("div", { class: "kpane" }, this.#head, this.#rows);
  }

  // another beat: nothing picked
  reset(): void {
    this.#selected = null;
    this.#pick.follow();
  }

  follow(): void {
    this.#pick.follow();
  }

  show(): void {
    if (this.#dragging) {
      this.#stale = true;
      return;
    }
    const score = this.#host.score();
    if (!score) {
      this.#head.replaceChildren();
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, "Загружаю партитуру…"));
      return;
    }
    const pattern = this.#pick.current(score, this.#host.seed(), this.#host.section(), this.#host.wants?.() ?? null);
    if (pattern !== this.#shown) {
      this.#shown = pattern;
      this.#bar = 0;
    }
    const bars = pattern === null ? 1 : Math.max(1, score.patterns[pattern]?.bars ?? 1);
    this.#bar = Math.min(this.#bar, bars - 1);
    this.#paintHead(score, pattern, bars);
    if (pattern === null) {
      this.#rows.replaceChildren(h("p", { class: "epane__empty" }, "В бите нет паттернов: ＋ сделает новый."));
      return;
    }
    const specs = this.#host.specs();
    this.#rows.replaceChildren(...rackRows(score, specs, pattern).map((row) => this.#row(score, specs, pattern, row)));
  }

  // ---------- the head ----------

  #paintHead(score: Score, pattern: string | null, bars: number): void {
    const names = Object.keys(score.patterns);
    const select = h("select", { class: "rhead__pat", "aria-label": "Паттерн" });
    names.forEach((name, i) => select.append(h("option", { value: name, selected: name === pattern }, patternLabel(score, name, i))));
    select.addEventListener("change", () => {
      this.#pick.choose(select.value);
      this.show();
    });
    const add = h("button", { class: "rhead__btn", type: "button", title: "Новый паттерн", "aria-label": "Новый паттерн" }, "＋");
    add.addEventListener("click", () => {
      const current = this.#host.score();
      if (!current) return;
      const made = newPattern(current, 1);
      if (!made.ok) return this.#host.say(made.reason);
      this.#switchTo(made.score, made.name);
    });
    const copy = h("button", { class: "rhead__btn", type: "button", title: "Копия паттерна", "aria-label": "Копия паттерна", disabled: pattern === null }, "⧉");
    copy.addEventListener("click", () => {
      const current = this.#host.score();
      if (!current || pattern === null) return;
      const made = clonePattern(current, pattern);
      if (!made.ok) return this.#host.say(made.reason);
      this.#switchTo(made.score, made.name);
    });
    const length = h("select", { class: "rhead__len", "aria-label": "Длина паттерна", title: "Длина паттерна", disabled: pattern === null });
    for (const n of [1, 2, 4, 8]) length.append(h("option", { value: String(n), selected: n === bars }, `${n} ${barWord(n)}`));
    length.addEventListener("change", () => {
      const current = this.#host.score();
      if (!current || pattern === null) return;
      const made = setPatternBars(current, pattern, Number(length.value));
      if (!made.ok) this.#host.say(made.reason);
      else this.#host.edit(made.score);
      this.show();
    });
    const parts: (Node | string)[] = [h("h2", { class: "rhead__title" }, "Каналы"), h("div", { class: "rhead__pick" }, select, add, copy), length];
    if (bars > 1) {
      const back = h("button", { class: "rhead__btn", type: "button", "aria-label": "Предыдущий такт", disabled: this.#bar === 0 }, "‹");
      const next = h("button", { class: "rhead__btn", type: "button", "aria-label": "Следующий такт", disabled: this.#bar >= bars - 1 }, "›");
      back.addEventListener("click", () => this.#turn(-1));
      next.addEventListener("click", () => this.#turn(1));
      parts.push(h("div", { class: "rhead__pager", title: "Такт паттерна" }, back, h("span", {}, `${this.#bar + 1}/${bars}`), next));
    }
    const roll = h("select", { class: "rollpick", "aria-label": "Что ставит клик", title: "Что ставит клик по пустому шагу" });
    for (const [n, label] of ROLL_NAMES) roll.append(h("option", { value: String(n), selected: n === this.#roll }, label));
    roll.addEventListener("change", () => {
      this.#roll = Number(roll.value);
    });
    parts.push(roll, h("span", { class: "rhead__hint" }, "клик — удар · Shift — акцент, тихий · правый клик — стереть"));
    this.#head.replaceChildren(...parts);
  }

  #switchTo(next: Score, pattern: string): void {
    // picked first: the edit draws the rack again at once
    this.#pick.choose(pattern);
    if (!this.#host.edit(next)) this.#pick.follow();
    this.show();
  }

  #turn(by: number): void {
    this.#bar += by;
    this.show();
  }

  // ---------- a row ----------

  #row(score: Score, specs: readonly InstrumentSpec[], pattern: string, row: RackRow): HTMLElement {
    const kind = rowKind(score, specs, pattern, row.sound);
    const label = instrumentLabel(row.instrument);
    const locked = !row.inPattern && row.lockedBy !== null;
    const el = h("div", { class: `ch${row.inPattern ? "" : " ch--out"}${locked ? " ch--locked" : ""}`, "data-sound": row.sound });
    el.style.setProperty("--c", `var(${colourOf(row.instrument, kind)})`);

    const led = h("button", {
      class: "led",
      type: "button",
      "aria-pressed": String(!row.muted),
      "aria-label": `${label}: ${row.muted ? "выключен" : "включён"}`,
      title: row.muted ? "Выключен во всех секциях: клик включит" : "Клик выключит во всех секциях",
    });
    led.addEventListener("click", () => {
      const current = this.#host.score();
      if (!current) return;
      const made = setSoundMuted(current, row.sound, !row.muted);
      if (!made.ok) this.#host.say(made.reason);
      else this.#host.edit(made.score);
    });

    const mix = soundMix(score, row.sound);
    const volume = this.#knob(`Громкость · ${label}`, gainPosition(mix.gain_db), gainPosition(0), gainText(mix.gain_db), (s, v) => setGain(s, row.sound, gainAt(v)), (v) => gainText(gainAt(v)));
    const pan = this.#knob(`Панорама · ${label}`, panPosition(mix.pan), panPosition(0), panText(mix.pan), (s, v) => setPan(s, row.sound, panAt(v)), (v) => panText(panAt(v)));

    const name = h("button", { class: `ch__name${this.#selected === row.sound ? " sel" : ""}`, type: "button", title: `${label} · ${row.sound}` }, label, h("small", {}, row.sound));
    name.addEventListener("click", () => {
      this.#selected = row.sound;
      if (kind === "note" && row.inPattern) this.#host.notes(pattern, row.sound, false);
      this.#host.preview?.(row.sound);
      this.show();
    });

    el.append(led, volume, pan, name, this.#body(score, specs, pattern, row, kind, label, locked));
    return el;
  }

  #body(score: Score, specs: readonly InstrumentSpec[], pattern: string, row: RackRow, kind: "drum" | "note" | null, label: string, locked: boolean): HTMLElement {
    if (locked && row.lockedBy !== null) {
      const lock = h("button", { class: "ch__locked", type: "button" }, lockedText(score, row.lockedBy));
      lock.addEventListener("click", () => {
        const current = this.#host.score();
        const made = current ? ensureRow(current, specs, pattern, row.sound) : null;
        if (made && !made.ok) this.#host.say(made.reason);
      });
      return lock;
    }
    if (kind === "note") return this.#mini(score, specs, pattern, row, label);
    if (kind === null) return h("span", { class: "ch__none" }, "инструмент не найден");
    return this.#steps(score, specs, pattern, row, label);
  }

  #steps(score: Score, specs: readonly InstrumentSpec[], pattern: string, row: RackRow, label: string): HTMLElement {
    const base = this.#bar * STEPS_PER_BAR;
    let hits: Hit[] = [];
    if (row.inPattern) {
      try {
        hits = drumSteps(score, pattern, row.sound).slice(base, base + STEPS_PER_BAR);
      } catch {
        hits = [];
      }
    }
    while (hits.length < STEPS_PER_BAR) hits.push(".");
    const box = h("div", { class: "rsteps" });
    hits.forEach((hit, col) => {
      const rolls = hit === "2" || hit === "3" || hit === "4" || hit === "6";
      const cell = h("button", {
        class: `st${hit !== "." ? " on" : ""}${hit === "X" ? " accent" : ""}${hit === "g" ? " ghost" : ""}${rolls ? " roll" : ""}`,
        type: "button",
        "aria-pressed": String(hit !== "."),
        "aria-label": `${label}, шаг ${base + col + 1}`,
        "data-roll": rolls ? hit : undefined,
      });
      const press = (gesture: StepGesture): void => {
        const current = this.#host.score();
        if (!current) return;
        const made = rackStep(current, specs, pattern, row.sound, base + col, gesture, this.#roll);
        if (!made.ok) this.#host.say(made.reason);
        else if (made.score !== current) this.#host.edit(made.score);
      };
      cell.addEventListener("click", (e) => press(e.shiftKey ? "accent" : "click"));
      cell.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        press("clear");
      });
      box.append(cell);
    });
    return box;
  }

  #mini(score: Score, specs: readonly InstrumentSpec[], pattern: string, row: RackRow, label: string): HTMLElement {
    const box = h("button", { class: "mini", type: "button", "aria-label": `${label}: открыть ноты`, title: "Открыть пианоролл" });
    if (row.inPattern) {
      let notes: ReturnType<typeof notesOf> = [];
      try {
        notes = notesOf(score, pattern, row.sound);
      } catch {
        notes = [];
      }
      const midis = notes.map((n) => n.midi);
      const lo = Math.min(...midis);
      const span = Math.max(1, Math.max(...midis) - lo);
      for (const n of notesInBar(notes, this.#bar)) {
        const bar = h("i", {});
        bar.style.setProperty("left", `${(n.start / STEPS_PER_BAR) * 100}%`);
        bar.style.setProperty("width", `calc(${(n.len / STEPS_PER_BAR) * 100}% - 1px)`);
        bar.style.setProperty("top", `${(1 - (n.midi - lo) / span) * 70 + 10}%`);
        box.append(bar);
      }
    }
    box.addEventListener("click", () => {
      const current = this.#host.score();
      if (!current) return;
      if (!row.inPattern) {
        const made = ensureRow(current, specs, pattern, row.sound);
        if (!made.ok) return this.#host.say(made.reason);
        if (!this.#host.edit(made.score)) return;
      }
      this.#selected = row.sound;
      this.#host.notes(pattern, row.sound, true);
    });
    return box;
  }

  // a knob over the score: heard while it turns, one undo step where it is let go
  #knob(name: string, value: number, reset: number, text: string, change: (score: Score, v: number) => Score, words: (v: number) => string): HTMLElement {
    const knob = new KnobControl(name, value, () => reset, {
      input: (v) => {
        knob.setText(words(v));
        const base = this.#host.score();
        if (base) this.#host.edit(change(base, v), true);
      },
      commit: (v) => {
        this.#dragging = false;
        this.#stale = false;
        const base = this.#host.score();
        if (base) this.#host.edit(change(base, v));
      },
    });
    knob.setText(text);
    knob.root.classList.add("knob--mini");
    knob.root.addEventListener("pointerdown", (e) => {
      if (e.button === 0) this.#dragging = true;
    });
    // let go without a change, or the capture lost: the rows may be drawn again
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) knob.root.addEventListener(type, () => this.#endDrag());
    return knob.root;
  }

  #endDrag(): void {
    if (!this.#dragging) return;
    this.#dragging = false;
    if (this.#stale) {
      this.#stale = false;
      this.show();
    }
  }
}
