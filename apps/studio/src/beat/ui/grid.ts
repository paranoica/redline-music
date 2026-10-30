// The grid under the selected bar: one row per sound its section plays there, all on one time
// axis. A drum row is the bar's 16 steps; a note row is a small piano roll over the same bar
// with the 808's glides drawn as the event table plays them.

import type { BeatKey } from "../keys.ts";
import { instrumentLabel } from "../labels.ts";
import {
  STEPS_PER_BAR,
  addNote,
  addedNoteStep,
  clickStep,
  cycleStep,
  dragOutlived,
  deleteNote,
  drumSteps,
  glidesInBar,
  isNoteRow,
  laneValues,
  moveNote,
  nextRoll,
  noteName,
  notesInBar,
  noteToFocus,
  notesOf,
  resizeNote,
  rollCount,
  setHit,
  setRoll,
  usage,
  usesFlats,
  type Faults,
  type Glide,
  type Hit,
  type InstrumentSpec,
  type NoteView,
  type PlayingRow,
  type Score,
} from "../model.ts";
import { h } from "../../ui/dom.ts";

export interface Selection {
  pattern: string;
  sound: string;
}

export interface GridState {
  score: Score;
  bar: number;
  rate: number;
  rows: PlayingRow[];
  section: number;
  selected: Selection | null;
  // the step of the bar for a drum row, 0..15
  column: number;
  note: number | null;
  muted: ReadonlySet<string>;
  solo: ReadonlySet<string>;
  instruments: ReadonlyMap<string, InstrumentSpec>;
  glides: readonly Glide[];
  faults: Faults;
}

export interface GridHandlers {
  select: (sel: Selection, column?: number, note?: number | null) => void;
  edit: (next: Score, commit: boolean) => void;
  mute: (sound: string) => void;
  solo: (sound: string) => void;
  unique: (pattern: string) => void;
  // a bar of the song, picked on a row's map of its pattern
  bar: (bar: number) => void;
}

const SVG = "http://www.w3.org/2000/svg";
const HIT_NAMES: Record<Hit, string> = { ".": "пусто", x: "удар", X: "акцент", g: "призрак", "2": "дробь 2", "3": "дробь 3", "4": "дробь 4", "6": "дробь 6" };

const same = (a: Selection | null, b: Selection | null): boolean => !!a && !!b && a.pattern === b.pattern && a.sound === b.sound;

interface Drag {
  id: number;
  x: number;
  y: number;
  stepPx: number;
  base: number;
  rowPx: number;
  start: Score;
  sel: Selection;
  note: NoteView;
  mode: "move" | "resize";
  el: HTMLElement;
  next: Score;
  moved: boolean;
  stop: () => void;
}

export class Grid {
  readonly root: HTMLElement;
  readonly #head: HTMLElement;
  readonly #rows: HTMLElement;
  readonly #on: GridHandlers;
  #state: GridState | null = null;
  #drag: Drag | null = null;
  #pending = false;
  #playCol = -1;
  // one playhead over every row, in the steps' column
  readonly #play: HTMLElement;

  constructor(on: GridHandlers) {
    this.#on = on;
    this.#head = h("header", { class: "grid__head" });
    this.#play = h("span", { class: "grid__play", "aria-hidden": "true" });
    this.#rows = h("div", { class: "grid__rows" });
    this.root = h("section", { class: "grid", "aria-label": "Что играет в выбранном такте" }, this.#head, this.#rows);
  }

  get dragging(): boolean {
    return this.#drag !== null;
  }

  show(state: GridState): void {
    const before = this.#state;
    this.#state = state;
    // a drag moves its own note; the grid is drawn again once it is let go
    if (this.#drag) {
      if (!dragOutlived(this.#drag.next, state.score)) {
        this.#pending = true;
        return;
      }
      this.#drag.stop();
      this.#pending = false;
    }
    const content =
      !before ||
      before.score !== state.score ||
      before.bar !== state.bar ||
      before.muted !== state.muted ||
      before.solo !== state.solo ||
      before.glides !== state.glides ||
      before.faults !== state.faults;
    if (!content) {
      this.#showSelection(state);
      return;
    }
    const focused = this.root.contains(document.activeElement);
    const s = state.score.sections[state.section];
    this.#head.replaceChildren(
      h("h2", { class: "grid__title" }, "Такт ", h("span", { class: "num" }, String(state.bar + 1))),
      h("span", { class: "grid__sub" }, s ? `«${s.name}» играет ${(s.play ?? []).join(", ") || "ничего"}${s.fill ? `, сбивка ${s.fill} в последнем такте` : ""}` : ""),
      h("span", { class: "grid__hint" }, "Клик — удар → акцент → призрак → пусто · правый клик или Alt — дробь"),
    );
    if (!state.rows.length) {
      this.#rows.replaceChildren(h("p", { class: "grid__empty" }, "В этом такте ничего не играет."));
      return;
    }
    this.#rows.replaceChildren(...state.rows.map((row) => this.#row(state, row)), this.#play);
    this.#showSelection(state);
    if (focused) this.focusSelected();
  }

  // the selection is marked on the drawn rows, so a click's own cell stays in place
  #showSelection(st: GridState): void {
    const anySelected = st.rows.some((r) => same(r, st.selected));
    for (const trk of this.#rows.querySelectorAll<HTMLElement>(".trk")) {
      const sel = { pattern: trk.dataset.pattern ?? "", sound: trk.dataset.sound ?? "" };
      const on = same(st.selected, sel);
      trk.dataset.selected = String(on);
      for (const cell of trk.querySelectorAll<HTMLElement>(".step")) {
        const here = on && Number(cell.dataset.col) === st.column;
        cell.tabIndex = here ? 0 : -1;
        cell.dataset.cursor = String(here);
      }
      for (const note of trk.querySelectorAll<HTMLElement>(".note")) {
        note.dataset.selected = String(on && Number(note.dataset.index) === st.note);
      }
      const roll = trk.querySelector<HTMLElement>(".roll");
      if (roll) roll.tabIndex = on || !anySelected ? 0 : -1;
    }
    // with nothing selected the first drum row still takes the Tab
    if (!anySelected) {
      const first = this.#rows.querySelector<HTMLElement>(".step");
      if (first) first.tabIndex = 0;
    }
  }

  // the step heard while the selected bar plays; -1 hides it
  setPlayStep(column: number): void {
    if (column === this.#playCol) return;
    this.#playCol = column;
    this.#play.dataset.on = String(column >= 0);
    if (column >= 0) this.#play.style.setProperty("--col", String(column));
  }

  focusSelected(): void {
    const target =
      this.#rows.querySelector<HTMLElement>('.trk[data-selected="true"] .step[tabindex="0"]') ??
      this.#rows.querySelector<HTMLElement>('.trk[data-selected="true"] .roll') ??
      this.#rows.querySelector<HTMLElement>('.step[tabindex="0"], .roll');
    target?.focus({ preventScroll: false });
  }

  // a key for the drum steps or the roll that has the focus; true when it was used
  key(action: BeatKey): boolean {
    const st = this.#state;
    if (!st || !st.selected) return false;
    const row = st.rows.find((r) => same(r, st.selected));
    if (!row) return false;
    const raw = st.score.patterns[row.pattern]?.rows[row.sound];
    if (raw === undefined) return false;
    return isNoteRow(raw) ? this.#rollKey(st, row, action) : this.#drumKey(st, row, action);
  }

  #drumKey(st: GridState, row: PlayingRow, action: BeatKey): boolean {
    const step = row.patternBar * STEPS_PER_BAR + st.column;
    const sel = { pattern: row.pattern, sound: row.sound };
    // a row shorter than the bar has no step under the cursor to change
    const lacks = () => drumSteps(st.score, row.pattern, row.sound)[step] === undefined;
    const edit = (next: Score) => this.#on.edit(next, true);
    switch (action.type) {
      case "move": {
        if (action.dy) {
          const at = st.rows.findIndex((r) => same(r, sel));
          const to = st.rows[at + action.dy];
          if (to) this.#on.select({ pattern: to.pattern, sound: to.sound }, st.column, null);
          return true;
        }
        const by = action.wide ? 4 : 1;
        this.#on.select(sel, Math.min(15, Math.max(0, st.column + action.dx * by)), null);
        return true;
      }
      case "hit":
        if (lacks()) return true;
        edit(setHit(st.score, row.pattern, row.sound, step, action.hit));
        return true;
      case "roll":
        if (lacks()) return true;
        edit(setRoll(st.score, row.pattern, row.sound, step, action.n));
        return true;
      case "cycle":
        if (lacks()) return true;
        edit(cycleStep(st.score, row.pattern, row.sound, step));
        return true;
      default:
        return false;
    }
  }

  #rollKey(st: GridState, row: PlayingRow, action: BeatKey): boolean {
    const notes = notesOf(st.score, row.pattern, row.sound);
    const chosen = notes.find((n) => n.index === st.note) ?? null;
    const sel = { pattern: row.pattern, sound: row.sound };
    switch (action.type) {
      case "move": {
        if (!chosen) {
          const first = noteToFocus(notes, row.patternBar);
          if (first !== null) this.#on.select(sel, st.column, first);
          return true;
        }
        let next: Score;
        if (action.wide && action.dx) next = resizeNote(st.score, row.pattern, row.sound, chosen.index, chosen.len + action.dx);
        else if (action.dx) next = moveNote(st.score, row.pattern, row.sound, chosen.index, { step: chosen.step + action.dx });
        else next = moveNote(st.score, row.pattern, row.sound, chosen.index, { midi: chosen.midi - action.dy * (action.wide ? 12 : 1) });
        this.#on.edit(next, true);
        return true;
      }
      case "remove":
        if (!chosen) return true;
        this.#on.edit(deleteNote(st.score, row.pattern, row.sound, chosen.index), true);
        this.#on.select(sel, st.column, null);
        return true;
      case "add": {
        const step = addedNoteStep(chosen, row.patternBar, st.score.patterns[row.pattern]?.bars ?? 1);
        const midi = chosen?.midi ?? this.#centre(st, row);
        const made = addNote(st.score, row.pattern, row.sound, { step, len: 2, midi, vel: 0.8 });
        this.#on.edit(made.score, true);
        this.#on.select(sel, st.column, made.index);
        return true;
      }
      default:
        return false;
    }
  }

  #centre(st: GridState, row: PlayingRow): number {
    const inst = st.score.sounds[row.sound]?.instrument ?? "";
    return inst === "bass808" ? 36 : 60;
  }

  #row(st: GridState, row: PlayingRow): HTMLElement {
    const sel = { pattern: row.pattern, sound: row.sound };
    const sound = st.score.sounds[row.sound];
    const inst = sound?.instrument ?? "?";
    const raw = st.score.patterns[row.pattern]?.rows[row.sound];
    const selected = same(st.selected, sel);
    const heard = !row.muted && !row.replaced && !st.muted.has(row.sound) && (st.solo.size === 0 || st.solo.has(row.sound));
    const key = `${row.pattern}/${row.sound}`;
    const range = raw !== undefined && isNoteRow(raw) ? this.#range(st, row) : null;
    const head = this.#rowHead(st, row, inst, range);
    let body: HTMLElement;
    if (raw === undefined) body = h("p", { class: "trk__bad" }, "строки нет");
    else if (isNoteRow(raw) && range) body = this.#roll(st, row, selected, range);
    else {
      try {
        body = this.#steps(st, row, selected);
      } catch {
        body = h("p", { class: "trk__bad" }, "строка не читается — поправь её в JSON");
      }
    }
    const el = h(
      "div",
      { class: "trk", "data-pattern": row.pattern, "data-sound": row.sound, "data-selected": String(selected), "data-heard": String(heard), "data-fault": String(st.faults.rows.has(key)), "data-kind": raw !== undefined && isNoteRow(raw) ? "notes" : "drum" },
      head,
      body,
    );
    return el;
  }

  // the pitches a note row shows: the pattern's notes with room around them, so the rows do not
  // jump from bar to bar
  #range(st: GridState, row: PlayingRow): { lo: number; hi: number } {
    const midis = notesOf(st.score, row.pattern, row.sound).map((n) => n.midi);
    const centre = this.#centre(st, row);
    let lo = midis.length ? Math.min(...midis) - 3 : centre - 6;
    let hi = midis.length ? Math.max(...midis) + 3 : centre + 6;
    if (hi - lo < 12) {
      const grow = 12 - (hi - lo);
      lo -= Math.floor(grow / 2);
      hi += Math.ceil(grow / 2);
    }
    return { lo: Math.max(0, lo), hi: Math.min(127, hi) };
  }

  #map(st: GridState, row: PlayingRow, bars: number): HTMLElement | null {
    if (bars < 2) return null;
    const first = st.bar - row.patternBar;
    const segments = Array.from({ length: bars }, (_, k) => {
      const b = h("button", {
        class: "trk__seg",
        type: "button",
        "aria-pressed": String(k === row.patternBar),
        "aria-label": `Такт ${k + 1} паттерна ${row.pattern} (такт песни ${first + k + 1})`,
        title: `Такт песни ${first + k + 1}`,
      });
      b.addEventListener("click", () => this.#on.bar(first + k));
      return b;
    });
    return h("span", { class: "trk__map", role: "group", "aria-label": `Такты паттерна ${row.pattern}` }, ...segments);
  }

  #rowHead(st: GridState, row: PlayingRow, inst: string, range: { lo: number; hi: number } | null): HTMLElement {
    const sel = { pattern: row.pattern, sound: row.sound };
    const name = h("button", { class: "trk__name", type: "button", title: "Выбрать звук: его настройки справа" }, row.sound, h("span", { class: "trk__inst" }, instrumentLabel(inst)));
    name.addEventListener("click", () => this.#on.select(sel, st.column, null));
    const bars = st.score.patterns[row.pattern]?.bars ?? 1;
    const users = usage(st.score, row.pattern);
    const shared = users.length > 1;
    const mute = h("button", { class: "trk__ms", type: "button", "aria-pressed": String(st.muted.has(row.sound)), "aria-label": `Заглушить ${row.sound} (только вживую)`, title: "Заглушить вживую; в партитуру не идёт" }, "M");
    const solo = h("button", { class: "trk__ms", type: "button", "aria-pressed": String(st.solo.has(row.sound)), "aria-label": `Соло ${row.sound} (только вживую)`, title: "Соло вживую; в партитуру не идёт" }, "S");
    mute.addEventListener("click", () => this.#on.mute(row.sound));
    solo.addEventListener("click", () => this.#on.solo(row.sound));
    let unique: HTMLButtonElement | null = null;
    if (shared) {
      unique = h("button", { class: "trk__unique", type: "button", title: `Скопировать ${row.pattern} в новый паттерн только для этой секции` }, "сделать своим");
      unique.addEventListener("click", () => this.#on.unique(row.pattern));
    }
    const state = row.replaced ? "заменён сбивкой" : row.muted ? "заглушён в секции" : row.fill ? "сбивка" : "";
    const flats = usesFlats(st.score);
    return h(
      "div",
      { class: "trk__head" },
      range ? h("span", { class: "trk__range num", "aria-hidden": "true" }, h("span", {}, noteName(range.hi, flats)), h("span", {}, noteName(range.lo, flats))) : null,
      h("div", { class: "trk__line" }, name, h("span", { class: "trk__buttons" }, mute, solo)),
      h(
        "div",
        { class: "trk__line trk__line--meta" },
        h("span", { class: "trk__pattern num", title: `Паттерн ${row.pattern}, такт ${row.patternBar + 1} из ${bars}` }, `${row.pattern} ${row.patternBar + 1}/${bars}`),
        this.#map(st, row, bars),
        shared ? h("span", { class: "trk__shared", title: `Этот паттерн играют ${users.length} секции: правка слышна во всех` }, `общий ×${users.length}`) : null,
        unique,
        state ? h("span", { class: "trk__state" }, state) : null,
      ),
    );
  }

  #steps(st: GridState, row: PlayingRow, selected: boolean): HTMLElement {
    const sel = { pattern: row.pattern, sound: row.sound };
    const hits = drumSteps(st.score, row.pattern, row.sound);
    const chance = laneValues(st.score, row.pattern, row.sound, "chance").values;
    const velocity = laneValues(st.score, row.pattern, row.sound, "velocity");
    const base = row.patternBar * STEPS_PER_BAR;
    const faulty = st.faults.steps.get(`${row.pattern}/${row.sound}`);
    const cells: HTMLElement[] = [];
    for (let c = 0; c < STEPS_PER_BAR; c++) {
      const i = base + c;
      const hit = hits[i] ?? ".";
      const rolls = rollCount(hit);
      const p = chance[i] ?? 1;
      const v = velocity.values[i] ?? 0;
      // styles go through the style object: the page's policy refuses style attributes
      const fill = h("span", { class: "step__fill" });
      if (velocity.explicit && hit !== ".") fill.style.setProperty("--v", String(Math.max(0.12, v)));
      const label = `${row.sound}, шаг ${c + 1}: ${HIT_NAMES[hit]}${hit !== "." && p < 1 ? `, вероятность ${Math.round(p * 100)}%` : ""}`;
      const cell = h(
        "button",
        {
          class: "step",
          type: "button",
          tabindex: selected && c === st.column ? 0 : -1,
          "data-hit": hit === "." ? "rest" : hit === "X" ? "accent" : hit === "g" ? "ghost" : rolls ? "roll" : "hit",
          "data-chance": String(hit !== "." && p < 1),
          "data-cursor": String(selected && c === st.column),
          "data-col": c,
          "data-fault": String(faulty?.has(i) ?? false),
          "aria-label": label,
          title: label,
        },
        fill,
        rolls ? h("span", { class: "step__roll", "aria-hidden": "true" }, ...Array.from({ length: rolls }, () => h("i", {}))) : null,
      );
      cell.addEventListener("click", (e) => {
        this.#on.select(sel, c, null);
        const next = clickStep(st.score, row.pattern, row.sound, i, e.altKey);
        if (next) this.#on.edit(next, true);
      });
      cell.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        this.#on.select(sel, c, null);
        this.#on.edit(setHit(st.score, row.pattern, row.sound, i, nextRoll(hit)), true);
      });
      cell.addEventListener("focus", () => {
        const now = this.#state;
        if (now && (!same(now.selected, sel) || now.column !== c)) this.#on.select(sel, c, null);
      });
      cells.push(cell);
    }
    return h("div", { class: "steps", role: "group", "aria-label": `Шаги ${row.sound}: стрелки, X/A/G, 2/3/4/6, Delete` }, ...cells);
  }

  #roll(st: GridState, row: PlayingRow, selected: boolean, range: { lo: number; hi: number }): HTMLElement {
    const sel = { pattern: row.pattern, sound: row.sound };
    const base = row.patternBar * STEPS_PER_BAR;
    const notes = notesOf(st.score, row.pattern, row.sound);
    const { lo, hi } = range;
    const span = hi - lo + 1;
    const rowPx = span > 30 ? 5 : span > 20 ? 6 : 8;
    const flats = usesFlats(st.score);
    const faulty = st.faults.notes.get(`${row.pattern}/${row.sound}`);
    const area = h("div", { class: "roll__area" });
    area.style.setProperty("--row-px", `${rowPx}px`);
    area.style.height = `${span * rowPx}px`;
    const chosen = selected ? st.note : null;
    const byIndex = new Map(notes.map((n) => [n.index, n]));
    for (const shown of notesInBar(notes, row.patternBar)) {
      const n = byIndex.get(shown.index) as NoteView;
      const el = h("span", {
        class: "note",
        "data-index": n.index,
        "data-selected": String(n.index === chosen),
        "data-passing": String(n.passing),
        "data-cut-start": String(shown.cutStart),
        "data-cut-end": String(shown.cutEnd),
        "data-fault": String(faulty?.has(n.index) ?? false),
        title: `${noteName(n.midi, flats)}, шаг ${n.step - base + 1}, длина ${n.len}${shown.cutStart ? ", начата раньше" : ""}${shown.cutEnd ? ", идёт дальше" : ""}${n.passing ? ", проходящая" : ""}`,
      });
      this.#place(el, shown.start, shown.len, (hi - n.midi) * rowPx, rowPx);
      el.addEventListener("pointerdown", (e) => this.#startDrag(e, st, sel, n, el, area, base, rowPx));
      el.addEventListener("dblclick", (e) => {
        e.stopPropagation();
        this.#on.edit(deleteNote(st.score, row.pattern, row.sound, n.index), true);
      });
      area.append(el);
    }
    area.append(this.#glideLayer(st, row, hi, span));
    area.addEventListener("dblclick", (e) => {
      if (e.target !== area) return;
      const col = this.#column(area, e.clientX);
      const midi = hi - Math.floor((e.clientY - area.getBoundingClientRect().top) / rowPx);
      const made = addNote(st.score, row.pattern, row.sound, { step: base + col, len: 2, midi, vel: 0.8 });
      this.#on.edit(made.score, true);
      this.#on.select(sel, st.column, made.index);
    });
    const current = notes.find((n) => n.index === chosen);
    const roll = h(
      "div",
      {
        class: "roll",
        tabindex: selected || !st.selected ? 0 : -1,
        role: "application",
        "aria-roledescription": "пианоролл",
        "aria-label": `Ноты ${row.sound} в этом такте. ${current ? `Выбрана ${noteName(current.midi, flats)}, длина ${current.len}. ` : ""}Стрелки двигают ноту, Shift+стрелки — длина, Enter — новая, Delete — убрать`,
      },
      area,
    );
    roll.addEventListener("focus", () => {
      const now = this.#state;
      if (now && !same(now.selected, sel)) this.#on.select(sel, now.column, noteToFocus(notes, row.patternBar));
    });
    area.addEventListener("pointerdown", () => roll.focus({ preventScroll: true }));
    return roll;
  }

  // a step's width is the steps' grid cell plus its gap, so notes line up with the drum steps
  #place(el: HTMLElement, start: number, len: number, top: number, height: number): void {
    el.style.setProperty("--s", String(start));
    el.style.setProperty("--l", String(len));
    el.style.top = `${top}px`;
    el.style.height = `${height}px`;
  }

  #column(area: HTMLElement, clientX: number): number {
    const rect = area.getBoundingClientRect();
    const pitch = (rect.width + 3) / STEPS_PER_BAR;
    return Math.min(STEPS_PER_BAR - 1, Math.max(0, Math.floor((clientX - rect.left) / pitch)));
  }

  #glideLayer(st: GridState, row: PlayingRow, hi: number, span: number): SVGSVGElement {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "roll__glides");
    svg.setAttribute("viewBox", `0 0 ${STEPS_PER_BAR} ${span}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    const y = (m: number) => hi - m + 0.5;
    for (const g of glidesInBar(st.glides, row.sound, st.bar, st.score.bpm, st.rate)) {
      const w = Math.max(0.25, g.w);
      const points: string[] = [];
      for (let k = 0; k <= 12; k++) {
        const t = k / 12;
        // the pitch closes in on the target exponentially
        const m = g.to + (g.from - g.to) * Math.exp(-4 * t) * (1 - t);
        points.push(`${(g.x + t * w).toFixed(3)},${y(m).toFixed(3)}`);
      }
      const path = document.createElementNS(SVG, "polyline");
      path.setAttribute("points", points.join(" "));
      path.setAttribute("class", "roll__glide");
      svg.append(path);
    }
    return svg;
  }

  #startDrag(e: PointerEvent, st: GridState, sel: Selection, n: NoteView, el: HTMLElement, area: HTMLElement, base: number, rowPx: number): void {
    if (e.button !== 0) return;
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    const mode = e.clientX > rect.right - 6 && rect.width > 10 ? "resize" : "move";
    el.setPointerCapture(e.pointerId);
    const width = area.getBoundingClientRect().width || 1;
    this.#on.select(sel, st.column, n.index);
    (area.parentElement as HTMLElement | null)?.focus({ preventScroll: true });
    const stop = (): void => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      this.#drag = null;
      el.dataset.dragging = "false";
    };
    this.#drag = { id: e.pointerId, x: e.clientX, y: e.clientY, stepPx: (width + 3) / STEPS_PER_BAR, base, rowPx, start: st.score, sel, note: n, mode, el, next: st.score, moved: false, stop };
    el.dataset.dragging = "true";
    const move = (ev: PointerEvent) => {
      const d = this.#drag;
      if (!d || ev.pointerId !== d.id) return;
      const ds = Math.round((ev.clientX - d.x) / d.stepPx);
      const dm = -Math.round((ev.clientY - d.y) / d.rowPx);
      const { pattern, sound } = d.sel;
      const next =
        d.mode === "resize"
          ? resizeNote(d.start, pattern, sound, d.note.index, d.note.len + ds)
          : moveNote(d.start, pattern, sound, d.note.index, { step: d.note.step + ds, midi: d.note.midi + dm });
      if (next === d.next) return;
      const now = notesOf(next, pattern, sound).find((x) => x.index === d.note.index);
      const was = notesOf(d.next, pattern, sound).find((x) => x.index === d.note.index);
      if (!now || (was && was.step === now.step && was.len === now.len && was.midi === now.midi)) return;
      d.next = next;
      d.moved = true;
      // a note dragged out of the bar shown is drawn again once it is let go
      const shown = notesInBar([now], d.base / STEPS_PER_BAR)[0];
      d.el.hidden = !shown;
      if (shown) {
        d.el.style.setProperty("--s", String(shown.start));
        d.el.style.setProperty("--l", String(shown.len));
      }
      d.el.style.top = `${parseFloat(d.el.style.top) - (now.midi - (was?.midi ?? now.midi)) * d.rowPx}px`;
      this.#on.edit(next, false);
    };
    const up = (ev: PointerEvent) => {
      const d = this.#drag;
      if (!d || ev.pointerId !== d.id) return;
      d.stop();
      if (d.moved) this.#on.edit(d.next, true);
      if (this.#pending || d.moved) {
        this.#pending = false;
        if (this.#state) this.show(this.#state);
      }
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  }
}
