// The text of the song: the column beside the recording and takes, and the text station
// with its versions, the pick and the hand edit.

import { ApiError } from "./api.ts";
import type { TextProblem, TextReply, TextStation } from "./api.ts";
import { commandBox, needCard } from "./steps.ts";
import type { Ctx, StationView } from "./steps.ts";
import { h } from "../ui/dom.ts";

export interface LyricPart {
  part: string | null;
  lines: string[];
}

// "[Куплет 1]" opens a part; a blank line also starts a new block
export function parseLyric(text: string): LyricPart[] {
  const parts: LyricPart[] = [];
  let current: LyricPart | null = null;
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    const heading = /^\[(.+)\]$/.exec(line);
    if (heading) {
      current = { part: heading[1] ?? null, lines: [] };
      parts.push(current);
    } else if (line === "") {
      current = null;
    } else {
      if (!current) {
        current = { part: null, lines: [] };
        parts.push(current);
      }
      current.lines.push(line);
    }
  }
  return parts.filter((p) => p.lines.length > 0);
}

export function lineCount(parts: readonly LyricPart[]): number {
  return parts.reduce((n, p) => n + p.lines.length, 0);
}

export function placeOfProblem(p: TextProblem): string {
  return p.place ? `${p.place}: ${p.message}` : p.message;
}

export function renderLyric(parts: readonly LyricPart[], size: "read" | "ref", head: Node[]): HTMLElement {
  const el = h("article", { class: "lyric", "data-size": size, "aria-label": "Текст песни" }, h("header", { class: "lyric__head" }, ...head));
  let from = 0;
  for (const part of parts) {
    if (part.part) el.append(h("p", { class: "lyric__part" }, part.part));
    const list = h("ol", { class: "lyric__lines", style: `--from: ${from}` }, ...part.lines.map((l) => h("li", {}, l)));
    el.append(list);
    from += part.lines.length;
  }
  return el;
}

function station(ctx: Ctx): TextStation {
  return ctx.state().song.stations.text;
}

// the chosen text, large for singing or small for reference
export function createLyric(ctx: Ctx, size: "read" | "ref"): StationView {
  const el = h("div", { class: "lyric-slot" });
  let shown = "";
  let wait = 0;
  let closed = false;

  const update = (): void => {
    const t = station(ctx);
    const key = `${t.seed}:${t.version}`;
    if (key === shown) return;
    shown = key;
    const mine = ++wait;
    if (t.seed === null || t.version === null || t.version === undefined) {
      el.replaceChildren(h("p", { class: "lede" }, "Текст не выбран."));
      return;
    }
    const { seed, version } = t;
    void ctx.api.text(version).then(
      (r) => {
        if (closed || mine !== wait) return;
        const parts = parseLyric(r.text);
        el.replaceChildren(
          renderLyric(parts, size, [
            h("h3", { class: "lyric__title" }, `Текст v${version}`),
            h("span", { class: "lyric__meta num" }, `seed ${seed} · ${lineCount(parts)} строк`),
            (() => {
              const other = h("button", { class: "link", type: "button" }, "другой текст");
              other.addEventListener("click", () => ctx.go("text"));
              return other;
            })(),
          ]),
        );
      },
      () => {
        if (closed || mine !== wait) return;
        shown = "";
        el.replaceChildren(h("p", { class: "lede" }, "Текст не загрузился."));
      },
    );
  };
  update();
  return {
    el,
    update,
    dispose: () => {
      closed = true;
    },
  };
}

export function createTextStation(ctx: Ctx): StationView {
  const el = h("div", { class: "view view--one" });
  let viewing: number | null = null;
  let editing = false;
  let loaded: TextReply | null = null;
  let draft = "";
  let problems: TextProblem[] = [];
  let failure: string | null = null;
  let saving = false;
  let closed = false;
  let wait = 0;

  const t = (): TextStation => station(ctx);

  function load(): void {
    const info = t();
    if (viewing === null) viewing = info.version ?? info.versions?.at(-1) ?? null;
    if (viewing === null || (loaded && loaded.version === viewing)) return;
    const mine = ++wait;
    const version = viewing;
    void ctx.api.text(version).then(
      (r) => {
        if (closed || mine !== wait) return;
        loaded = r;
        draw();
      },
      () => {
        if (closed || mine !== wait) return;
        failure = "Текст не загрузился.";
        draw();
      },
    );
  }

  async function save(): Promise<void> {
    const info = t();
    if (info.seed === null || viewing === null || saving) return;
    saving = true;
    problems = [];
    failure = null;
    draw();
    try {
      const made = await ctx.api.saveText(info.seed, viewing, draft);
      editing = false;
      viewing = made.version;
      loaded = null;
      await ctx.reload();
      ctx.notice(`Сохранено как v${made.version}. Чтобы петь по нему, выбери эту версию.`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        problems = ((error.body["errors"] as TextProblem[] | undefined) ?? []).slice();
        if (!problems.length) failure = error.message;
      } else {
        failure = error instanceof Error ? error.message : "Не сохранилось.";
      }
    } finally {
      saving = false;
      if (!closed) draw();
    }
  }

  function missing(info: TextStation): HTMLElement {
    const pending = info.pending
      ? h(
          "div",
          { class: "need__pending" },
          h("h3", { class: "need__sub" }, "Если запрос уже ушёл"),
          h("p", {}, "Запрос ", h("span", { class: "num" }, info.pending), " ждёт агента. Когда агент ответит, запусти команду ещё раз: текст сохранится и появится здесь в списке."),
        )
      : null;
    return needCard({
      title: "Текст",
      what: "Текста для этой песни ещё нет.",
      how: "Текст пишет агент по запросу из терминала. Запусти:",
      command: info.command,
      then: "Команда положит запрос и выйдет с кодом 3: это значит «ждёт агента». Когда агент ответит, запусти её ещё раз.",
      pending,
      actions: [takeSeed()],
      after: "Запись откроется, когда текст выбран: его видно рядом с кнопкой записи.",
    });
  }

  // a text already written for another song, or one the agent has just saved
  function takeSeed(): HTMLElement {
    const field = h("input", { class: "field", inputmode: "numeric", "aria-label": "seed текста", placeholder: "seed" });
    const take = h("button", { class: "btn btn--ghost", type: "button" }, "Взять текст этого seed");
    take.addEventListener("click", () => {
      const seed = Number(field.value.trim());
      if (field.value.trim() === "" || !Number.isInteger(seed) || seed < 0) {
        ctx.notice("seed текста — целое число от нуля.");
        return;
      }
      void ctx.change({ text: { seed } });
    });
    return h("label", { class: "mix__seed" }, "Уже есть текст: ", field, take);
  }

  function draw(): void {
    const info = t();
    el.replaceChildren();
    const versions = info.versions ?? [];
    if (info.seed === null || versions.length === 0) {
      el.append(missing(info));
      return;
    }
    const list = h("div", { class: "seg seg--small", role: "radiogroup", "aria-label": "Версии текста" });
    list.style.setProperty("--count", String(versions.length));
    list.style.setProperty("--at", String(Math.max(0, versions.indexOf(viewing ?? -1))));
    list.append(h("span", { class: "seg__marker", "aria-hidden": "true" }));
    for (const v of versions) {
      const item = h("button", { class: "seg__item", type: "button", role: "radio", "aria-checked": String(v === viewing) }, `v${v}${v === info.version ? " ✓" : ""}`);
      item.addEventListener("click", () => {
        if (editing && !confirm("Правка не сохранена. Перейти к другой версии?")) return;
        editing = false;
        viewing = v;
        loaded = null;
        problems = [];
        failure = null;
        draw();
        load();
      });
      list.append(item);
    }
    const chosen = viewing !== null && viewing === info.version;
    const pick = h("button", { class: "btn btn--solid", type: "button", disabled: chosen || viewing === null }, chosen ? "Выбран" : "Выбрать этот");
    pick.addEventListener("click", () => {
      if (viewing !== null && info.seed !== null) void ctx.change({ text: { seed: info.seed, version: viewing } });
    });
    const edit = h("button", { class: "btn btn--ghost", type: "button", disabled: !loaded || editing }, "Править");
    edit.addEventListener("click", () => {
      if (!loaded) return;
      editing = true;
      draft = loaded.text;
      draw();
    });
    const body = h("div", { class: "card__body" });
    if (editing) {
      const area = h("textarea", { class: "field field--text", rows: 18, "aria-label": "Текст", spellcheck: "false" });
      area.value = draft;
      area.addEventListener("input", () => {
        draft = area.value;
      });
      const go = h("button", { class: "btn btn--solid", type: "button", disabled: saving }, saving ? "Сохраняю…" : "Сохранить как новую версию");
      go.addEventListener("click", () => void save());
      const stop = h("button", { class: "btn btn--ghost", type: "button", disabled: saving }, "Отмена");
      stop.addEventListener("click", () => {
        editing = false;
        problems = [];
        failure = null;
        draw();
      });
      body.append(area);
      if (problems.length) {
        body.append(h("ul", { class: "problems", role: "alert" }, ...problems.map((p) => h("li", {}, placeOfProblem(p)))));
      }
      if (failure) body.append(h("p", { class: "state__why", role: "alert" }, failure));
      body.append(h("div", { class: "state__acts" }, go, stop));
    } else if (loaded) {
      const parts = parseLyric(loaded.text);
      body.append(renderLyric(parts, "read", [h("h3", { class: "lyric__title" }, `Текст v${loaded.version}`), h("span", { class: "lyric__meta num" }, `seed ${loaded.seed} · ${lineCount(parts)} строк`)]));
      if (failure) body.append(h("p", { class: "state__why", role: "alert" }, failure));
    } else {
      body.append(h("p", { class: "lede busy" }, "Загружаю текст"));
    }
    if (info.pending) {
      body.append(h("p", { class: "need__then" }, "Ждёт агента: ", h("span", { class: "num" }, info.pending)));
    }
    body.append(h("details", { class: "newtext" }, h("summary", {}, "Новый текст пишет агент"), h("p", { class: "need__then" }, "Запусти в терминале:"), commandBox(info.command)));
    el.append(
      h(
        "section",
        { class: "card", "aria-label": "Текст" },
        h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Текст"), h("p", { class: "card__meta num" }, `seed ${info.seed}`), h("div", { class: "card__end" }, list, edit, pick)),
        body,
      ),
    );
  }

  let last = JSON.stringify(t());
  draw();
  load();
  return {
    el,
    update() {
      const now = JSON.stringify(t());
      if (now === last || editing || saving) return;
      last = now;
      draw();
      load();
    },
    dispose() {
      closed = true;
    },
  };
}
