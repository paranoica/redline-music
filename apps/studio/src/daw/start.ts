// The start screen: the songs as cards, newest first, and the «Новая песня» dialog. A card opens
// its song on the server; the pure parts above the DOM are what the tests check.

import { ApiError, createSongApi, type NewSong, type SongApi, type SongListItem } from "../song/api.ts";
import { h } from "../ui/dom.ts";
import { plural } from "./words.ts";

// ---------- the name ----------

export const NAME_RULE = "Имя станет папкой песни: без / и \\, не с точки, без пробелов в начале и в конце.";

// the server's folder-name rule, worded for the person
export function nameProblem(name: string, taken: readonly string[]): string | null {
  if (!name.trim()) return "Назови песню.";
  const broken =
    name !== name.trim() ||
    name === "." ||
    name === ".." ||
    name.startsWith(".") ||
    name.includes("/") ||
    name.includes("\\") ||
    [...name].some((ch) => ch.charCodeAt(0) < 32);
  if (broken) return NAME_RULE;
  if (taken.includes(name)) return `Песня «${name}» уже есть.`;
  return null;
}

// ---------- when it changed ----------

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const MINUTE = 60_000;
const DAY = 86_400_000;

const dayStart = (d: Date): number => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

// by the local calendar: hours today, «вчера», days within the week, then the date
export function relativeText(iso: string, now: Date): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const diff = now.getTime() - t;
  if (diff < MINUTE) return "только что";
  if (diff < 60 * MINUTE) {
    const n = Math.floor(diff / MINUTE);
    return `${n} ${plural(n, "минуту", "минуты", "минут")} назад`;
  }
  const then = new Date(t);
  // rounded: a day with a clock change is 23 or 25 hours long
  const days = Math.round((dayStart(now) - dayStart(then)) / DAY);
  if (days <= 0) {
    const n = Math.floor(diff / (60 * MINUTE));
    return `${n} ${plural(n, "час", "часа", "часов")} назад`;
  }
  if (days === 1) return "вчера";
  if (days < 7) return `${days} ${plural(days, "день", "дня", "дней")} назад`;
  if (days < 14) return "неделю назад";
  const year = then.getFullYear() !== now.getFullYear() ? ` ${then.getFullYear()}` : "";
  return `${then.getDate()} ${MONTHS[then.getMonth()]}${year}`;
}

// ---------- which view ----------

// `listed`: the server answered the songs list; `asked`: the person pressed the song's name
export function chooseView(o: { active: string | null; listed: boolean; asked: boolean }): "start" | "song" {
  if (o.asked) return "start";
  if (!o.listed) return "song";
  return o.active ? "song" : "start";
}

// ---------- the new song ----------

export interface NewSongForm {
  name: string;
  beat: "file" | "own";
  file: string | null;
  seed: number | null;
  text: "seed" | "none";
  textSeed: number | null;
}

export type NewSongRequest = { ok: true; body: NewSong } | { ok: false; problem: string };

export function newSongRequest(f: NewSongForm, taken: readonly string[]): NewSongRequest {
  const problem = nameProblem(f.name, taken);
  if (problem) return { ok: false, problem };
  if (f.beat === "file" && f.file === null) return { ok: false, problem: "Выбери тайп-бит." };
  if (f.beat === "own" && f.seed === null) return { ok: false, problem: "Выбери свой бит." };
  if (f.text === "seed" && f.textSeed === null) return { ok: false, problem: "Выбери текст." };
  const beat = f.beat === "own" ? { own: f.seed as number } : { file: f.file as string };
  return { ok: true, body: { name: f.name, beat, text_seed: f.text === "seed" ? f.textSeed : null } };
}

// ---------- words ----------

export function beatFileName(path: string): string {
  return path.split("/").pop() ?? path;
}

export function textLabel(t: { seed: number; title: string }): string {
  return t.title || `без названия ${t.seed}`;
}

const KIND_WORDS: Record<SongListItem["beat"], string> = { own: "свой бит", file: "тайп-бит", flip: "флип" };

export function songMeta(s: SongListItem): [string, string] {
  const takes = s.takes > 0 ? `${s.takes} ${plural(s.takes, "дубль", "дубля", "дублей")}` : "дублей нет";
  return [`${KIND_WORDS[s.beat]} · ${s.beat_label}`, s.text ? takes : `${takes} · без текста`];
}

const OPEN_REFUSALS: Record<string, string> = {
  take_running: "Идёт запись дубля: дождись её конца.",
  engine_busy: "Движок записи открывается или калибруется: подожди немного.",
  busy: "Прошлая песня ещё занята: попробуй через пару секунд.",
};

export function openRefusal(error: unknown): string {
  if (error instanceof ApiError && OPEN_REFUSALS[error.code]) return OPEN_REFUSALS[error.code] as string;
  return `Не открылась: ${error instanceof Error ? error.message : String(error)}`;
}

// ---------- the screen ----------

export interface StartOptions {
  base?: string | undefined;
  token: string;
  // the server switched to the song; the page shows it
  opened: () => void;
}

export interface StartView {
  dispose(): void;
}

type Sources = Awaited<ReturnType<SongApi["sources"]>>;

export function mountStart(root: HTMLElement, options: StartOptions): StartView {
  const api = createSongApi({ base: options.base, token: options.token });
  const list = h("div", { class: "songs" });
  const note = h("p", { class: "start__note", role: "status" });
  const section = h("section", { class: "start" }, h("div", { class: "start__top" }, h("span", { class: "brand__mark", "aria-hidden": "true" }), h("h1", {}, "Мои песни")), list, note);
  root.append(section);
  let songs: SongListItem[] = [];
  let busy = false;
  let dialog: HTMLElement | null = null;

  async function open(name: string): Promise<void> {
    if (busy) return;
    busy = true;
    note.textContent = "";
    try {
      await api.openSong(name);
      options.opened();
    } catch (error) {
      note.textContent = openRefusal(error);
    } finally {
      busy = false;
    }
  }

  function card(s: SongListItem, active: boolean): HTMLElement {
    const [beat, takes] = songMeta(s);
    const when = relativeText(s.modified, new Date());
    const el = h(
      "button",
      { class: "song", type: "button" },
      h("h3", {}, s.name, active ? h("span", { class: "song__open" }, "открыта") : null),
      h("p", {}, beat),
      h("p", {}, takes),
      when ? h("p", { class: "song__when" }, `изменена ${when}`) : null,
    );
    el.addEventListener("click", () => void open(s.name));
    return el;
  }

  async function load(): Promise<void> {
    const add = h("button", { class: "song song--new", type: "button" }, h("b", { "aria-hidden": "true" }, "+"), "Новая песня");
    add.addEventListener("click", () => void showDialog());
    try {
      const got = await api.songs();
      songs = [...got.songs].sort((a, b) => Date.parse(b.modified) - Date.parse(a.modified) || a.name.localeCompare(b.name));
      list.replaceChildren(...songs.map((s) => card(s, s.name === got.active)), add);
    } catch (error) {
      list.replaceChildren(add);
      note.textContent = `Список песен не загрузился: ${error instanceof Error ? error.message : String(error)}`;
    }
  }

  async function showDialog(): Promise<void> {
    if (dialog) return;
    let sources: Sources;
    try {
      sources = await api.sources();
    } catch (error) {
      note.textContent = `Не узнал, из чего делать песню: ${error instanceof Error ? error.message : String(error)}`;
      return;
    }
    dialog = newSongDialog(sources, songs.map((s) => s.name), async (body) => {
      await api.createSong(body);
      closeDialog();
      try {
        await api.openSong(body.name);
        options.opened();
      } catch (error) {
        await load();
        note.textContent = `Песня создана. ${openRefusal(error)}`;
      }
    }, closeDialog);
    root.append(dialog);
    root.ownerDocument.addEventListener("keydown", escape);
    dialog.querySelector<HTMLInputElement>("input")?.focus();
  }

  function escape(e: KeyboardEvent): void {
    if (e.key === "Escape") closeDialog();
  }

  function closeDialog(): void {
    root.ownerDocument.removeEventListener("keydown", escape);
    dialog?.remove();
    dialog = null;
  }

  void load();
  return {
    dispose() {
      closeDialog();
      api.close();
      section.remove();
    },
  };
}

// ---------- the dialog ----------

function pick<T extends string>(label: string, choices: { value: T; name: string; sub: string; disabled: boolean }[], chosen: T, changed: (v: T) => void): HTMLElement {
  const box = h("div", { class: "pick", role: "radiogroup", "aria-label": label });
  for (const c of choices) {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(c.value === chosen), disabled: c.disabled }, c.name, h("small", {}, c.sub));
    b.addEventListener("click", () => {
      for (const x of box.querySelectorAll("button")) x.setAttribute("aria-checked", String(x === b));
      changed(c.value);
    });
    box.append(b);
  }
  return box;
}

// a select with a «выбери» line unless there is only one choice
function chooser(label: string, options: { value: string; name: string }[]): HTMLSelectElement {
  const select = h("select", { "aria-label": label });
  if (options.length !== 1) select.append(h("option", { value: "" }, "— выбери —"));
  for (const o of options) select.append(h("option", { value: o.value }, o.name));
  return select;
}

function newSongDialog(sources: Sources, taken: readonly string[], create: (body: NewSong) => Promise<void>, close: () => void): HTMLElement {
  const name = h("input", { type: "text", maxlength: "120", placeholder: "например, ночная", "aria-label": "Название", spellcheck: "false" });
  const nameSay = h("p", { class: "field__say" });
  const beatFiles = chooser("Тайп-бит", sources.beats.map((b) => ({ value: b, name: beatFileName(b) })));
  const seeds = chooser("Свой бит", sources.seeds.map((s) => ({ value: String(s), name: `seed ${s}` })));
  const ownSay = h("p", { class: "field__say" }, "Новый свой бит пишет агент в чате, здесь он не создаётся: тут только биты с готовой партитурой.");
  const texts = chooser("Текст", sources.texts.map((t) => ({ value: String(t.seed), name: textLabel(t) })));
  const error = h("p", { class: "dialog__error", role: "alert" });
  const cancel = h("button", { class: "btn btn--ghost", type: "button" }, "Отмена");
  const go = h("button", { class: "btn btn--main", type: "button" }, "Создать");

  let beat: NewSongForm["beat"] = sources.beats.length || !sources.seeds.length ? "file" : "own";
  let text: NewSongForm["text"] = sources.texts.length ? "seed" : "none";
  let touched = false;

  const form = (): NewSongForm => ({
    name: name.value,
    beat,
    file: beatFiles.value || null,
    seed: seeds.value === "" ? null : Number(seeds.value),
    text,
    textSeed: texts.value === "" ? null : Number(texts.value),
  });

  function paint(): void {
    beatFiles.hidden = beat !== "file";
    seeds.hidden = beat !== "own";
    ownSay.hidden = beat !== "own";
    texts.hidden = text !== "seed";
    const problem = nameProblem(name.value, taken);
    nameSay.textContent = touched && problem ? problem : "";
  }

  const beatPick = pick<NewSongForm["beat"]>(
    "Бит",
    [
      { value: "file", name: "Тайп-бит", sub: sources.beats.length ? "файл из input/beats" : "в input/beats пусто", disabled: !sources.beats.length },
      { value: "own", name: "Свой бит", sub: sources.seeds.length ? "seed с партитурой" : "партитур пока нет", disabled: !sources.seeds.length },
    ],
    beat,
    (v) => {
      beat = v;
      paint();
    },
  );
  const textPick = pick<NewSongForm["text"]>(
    "Текст",
    [
      { value: "seed", name: "Из написанных", sub: sources.texts.length ? "выбрать готовый" : "написанных нет", disabled: !sources.texts.length },
      { value: "none", name: "Без текста", sub: "добавлю потом", disabled: false },
    ],
    text,
    (v) => {
      text = v;
      paint();
    },
  );

  name.addEventListener("input", () => {
    touched = true;
    error.textContent = "";
    paint();
  });

  go.addEventListener("click", async () => {
    touched = true;
    paint();
    const asked = newSongRequest(form(), taken);
    if (!asked.ok) {
      error.textContent = asked.problem;
      return;
    }
    go.disabled = true;
    error.textContent = "";
    try {
      await create(asked.body);
    } catch (e) {
      go.disabled = false;
      if (e instanceof ApiError && e.code === "exists") error.textContent = `Песня «${asked.body.name}» уже есть.`;
      else error.textContent = `Сервер не принял: ${e instanceof Error ? e.message : String(e)}`;
    }
  });
  cancel.addEventListener("click", close);

  const box = h(
    "div",
    { class: "dialog__box", role: "dialog", "aria-modal": "true", "aria-label": "Новая песня" },
    h("h2", {}, "Новая песня"),
    h("div", { class: "field" }, h("label", {}, "Название"), name, nameSay),
    h("div", { class: "field" }, h("label", {}, "Бит"), beatPick, beatFiles, seeds, ownSay),
    h("div", { class: "field" }, h("label", {}, "Текст"), textPick, texts),
    error,
    h("div", { class: "dialog__row" }, cancel, go),
  );
  const shade = h("div", { class: "dialog" }, box);
  shade.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target === name) go.click();
  });
  shade.addEventListener("click", (e) => {
    if (e.target === shade) close();
  });
  paint();
  return shade;
}
