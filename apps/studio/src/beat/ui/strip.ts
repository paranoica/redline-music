// The song strip: sections as labelled spans over a row of bar cells. A bar cell carries its
// mark and is where playing can start; the selected bar is what the grid below edits.

import type { Mark } from "../api.ts";
import { sectionStarts, type Score } from "../model.ts";
import { h } from "../../ui/dom.ts";

export interface StripHandlers {
  select: (bar: number) => void;
  playFrom: (bar: number) => void;
  mark: (bar: number, verdict: "good" | "bad", note: string) => Promise<boolean>;
}

const VERDICT = { good: "хорошо", bad: "плохо" } as const;

// the newest mark of each bar, any saved score it was made on
export function marksByBar(marks: readonly Mark[]): Map<number, Mark> {
  const out = new Map<number, Mark>();
  for (const m of marks) if (typeof m.bar === "number") out.set(m.bar - 1, m);
  return out;
}

export class SongStrip {
  readonly root: HTMLElement;
  readonly #track: HTMLElement;
  readonly #sections: HTMLElement;
  readonly #cells: HTMLElement;
  readonly #playhead: HTMLElement;
  readonly #where: HTMLElement;
  readonly #form: HTMLElement;
  readonly #on: StripHandlers;
  #bars: HTMLButtonElement[] = [];
  #shape = "";
  #selected = 0;
  #now = -1;
  #dirty = false;
  #base = "";
  #names: string[] = [];
  #sectionOf: number[] = [];

  constructor(on: StripHandlers) {
    this.#on = on;
    this.#where = h("p", { class: "song__where" });
    const good = h("button", { class: "btn btn--small", type: "button", title: "Клавиша G, когда фокус на такте" }, "Хорошо", h("kbd", {}, "G"));
    const bad = h("button", { class: "btn btn--small", type: "button", title: "Клавиша B, когда фокус на такте" }, "Плохо", h("kbd", {}, "B"));
    good.addEventListener("click", () => this.openMark("good"));
    bad.addEventListener("click", () => this.openMark("bad"));
    this.#form = h("div", { class: "markform", hidden: true });
    this.#sections = h("div", { class: "song__sections", "aria-hidden": "true" });
    this.#cells = h("div", { class: "song__bars", role: "toolbar", "aria-label": "Такты: стрелки выбирают, Enter играет с такта, G и B ставят отметку" });
    this.#playhead = h("span", { class: "song__playhead", "aria-hidden": "true" });
    this.#track = h("div", { class: "song__track" }, this.#sections, this.#cells);
    this.root = h(
      "section",
      { class: "song", "aria-labelledby": "song-title" },
      h(
        "header",
        { class: "song__head" },
        h("h2", { class: "song__title", id: "song-title" }, "Песня"),
        this.#where,
        h("div", { class: "song__marks" }, good, bad),
      ),
      this.#form,
      h("div", { class: "song__scroll" }, this.#track),
    );
  }

  show(score: Score, marks: Map<number, Mark>, selected: number, dirty: boolean, base: string): void {
    this.#dirty = dirty;
    this.#base = base;
    const starts = sectionStarts(score);
    const total = score.sections.reduce((n, s) => n + s.bars, 0);
    const shape = JSON.stringify(score.sections.map((s) => [s.name, s.bars]));
    if (shape !== this.#shape) {
      this.#shape = shape;
      this.#build(score, starts, total);
    }
    this.#selected = Math.min(Math.max(0, selected), total - 1);
    this.#bars.forEach((cell, bar) => {
      const mark = marks.get(bar);
      const on = bar === this.#selected;
      cell.tabIndex = on ? 0 : -1;
      cell.setAttribute("aria-pressed", String(on));
      cell.dataset.mark = mark ? mark.verdict : "";
      const name = this.#names[this.#sectionOf[bar] ?? 0] ?? "";
      const markText = mark ? `, отмечен: ${VERDICT[mark.verdict]}${mark.note ? ` — ${mark.note}` : ""} (${mark.score})` : "";
      cell.setAttribute("aria-label", `Такт ${bar + 1}, ${name}${markText}`);
      cell.title = mark ? `${VERDICT[mark.verdict]}${mark.note ? `: ${mark.note}` : ""} · ${mark.score}` : `Такт ${bar + 1}: клик — выбрать, двойной — играть отсюда`;
    });
    const section = this.#sectionOf[this.#selected] ?? 0;
    const inSection = this.#selected - (starts[section] ?? 0) + 1;
    const s = score.sections[section];
    this.#where.replaceChildren(
      h("span", { class: "num" }, `такт ${this.#selected + 1}`),
      " · ",
      h("span", {}, s ? `«${s.name}»` : ""),
      " ",
      h("span", { class: "num song__dim" }, s ? `${inSection} из ${s.bars}` : ""),
    );
  }

  // the bar heard, as a fraction of the song, or null when stopped at the start
  setPlayhead(at: number | null, total: number): void {
    if (at === null || total <= 0) {
      this.#playhead.dataset.on = "false";
      this.#setNow(-1);
      return;
    }
    this.#playhead.dataset.on = "true";
    this.#playhead.style.setProperty("--at", String(Math.min(1, at / total)));
    this.#setNow(Math.floor(at));
  }

  focusBar(bar: number): void {
    this.#bars[bar]?.focus();
    this.#bars[bar]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  openMark(verdict: "good" | "bad"): void {
    const bar = this.#selected;
    const input = h("input", { class: "text markform__note", type: "text", maxlength: 2000, placeholder: "почему (можно пусто)", "aria-label": "Заметка к отметке" });
    const ok = h("button", { class: "btn btn--signal btn--small", type: "submit" }, "Отметить");
    const cancel = h("button", { class: "btn btn--ghost btn--small", type: "button" }, "Отмена");
    const where = this.#dirty
      ? `Отметка ляжет на сохранённый ${this.#base}: несохранённых правок в ней не будет.`
      : `Отметка ляжет на ${this.#base}.`;
    const form = h(
      "form",
      { class: "markform__body" },
      h("span", { class: "markform__what", "data-verdict": verdict }, h("span", { class: "num" }, `Такт ${bar + 1}`), ` — ${VERDICT[verdict]}`),
      input,
      ok,
      cancel,
      h("span", { class: "markform__hint", "data-dirty": String(this.#dirty) }, where),
    );
    const close = () => {
      this.#form.hidden = true;
      this.#form.replaceChildren();
      this.focusBar(bar);
    };
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      ok.disabled = true;
      void this.#on.mark(bar, verdict, input.value.trim()).then((done) => {
        if (done) close();
        else ok.disabled = false;
      });
    });
    cancel.addEventListener("click", close);
    form.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close();
      }
    });
    this.#form.replaceChildren(form);
    this.#form.hidden = false;
    input.focus();
  }

  #setNow(bar: number): void {
    if (bar === this.#now) return;
    if (this.#now >= 0) this.#bars[this.#now]?.removeAttribute("data-now");
    this.#now = bar;
    if (bar >= 0) this.#bars[bar]?.setAttribute("data-now", "true");
  }

  #build(score: Score, starts: number[], total: number): void {
    this.#track.style.setProperty("--bars", String(total));
    this.#names = score.sections.map((s) => s.name);
    this.#sectionOf = [];
    this.#sections.replaceChildren(
      ...score.sections.map((s, i) => {
        for (let b = 0; b < s.bars; b++) this.#sectionOf.push(i);
        const start = (starts[i] ?? 0) + 1;
        const span = h("span", { class: "song__section", title: `${s.name}: ${s.bars} тактов` }, h("span", { class: "song__name" }, s.name));
        // through the style object: the page's policy refuses style attributes
        span.style.gridColumn = `${start} / span ${s.bars}`;
        return span;
      }),
    );
    this.#bars = [];
    for (let bar = 0; bar < total; bar++) {
      const first = this.#sectionOf[bar] !== this.#sectionOf[bar - 1];
      const cell = h(
        "button",
        { class: "bar", type: "button", tabindex: -1, "data-first": String(first), "data-beat4": String(bar % 4 === 0) },
        h("span", { class: "bar__n num" }, bar % 4 === 0 || first ? String(bar + 1) : ""),
        h("span", { class: "bar__tick", "aria-hidden": "true" }, "✓"),
        h("span", { class: "bar__notch", "aria-hidden": "true" }),
      );
      cell.addEventListener("click", () => this.#on.select(bar));
      cell.addEventListener("dblclick", () => this.#on.playFrom(bar));
      this.#bars.push(cell);
    }
    // the playhead runs over the bars only, under the section names
    this.#cells.replaceChildren(...this.#bars, this.#playhead);
    this.#now = -1;
  }
}
