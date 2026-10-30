// The rail: the song as a signal path through six stations. The state of each station comes
// from the server's stations json; nothing here touches the document except the renderer.

import type { Job, Ranking, RecSnapshot, Stations } from "./api.ts";
import { h } from "../ui/dom.ts";

export type StationId = "beat" | "text" | "rec" | "takes" | "mix" | "result";
export type StationState = "done" | "todo" | "missing" | "bypass" | "live";

export const STATION_IDS: readonly StationId[] = ["beat", "text", "rec", "takes", "mix", "result"];

export const STATION_NAMES: Record<StationId, string> = {
  beat: "Бит",
  text: "Текст",
  rec: "Запись",
  takes: "Дубли",
  mix: "Сведение",
  result: "Итог",
};

export interface RailItem {
  id: StationId;
  name: string;
  state: StationState;
  value: string;
  // on the line: done, live or passed through
  lit: boolean;
  // the line runs straight through, no jack
  folded: boolean;
  current: boolean;
}

export interface RailContext {
  stations: Stations;
  jobs?: readonly Job[];
  ranking?: Ranking | null;
  rec?: Pick<RecSnapshot, "stage" | "recording" | "take" | "seconds"> | null;
  current?: StationId | null;
}

export function takeLabel(name: string): string {
  const m = /^take-(\d+)\.wav$/.exec(name);
  return m ? `дубль ${m[1]}` : name;
}

export function clock(seconds: number, tenths = true): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  const text = tenths ? rest.toFixed(1).padStart(4, "0") : String(Math.floor(rest)).padStart(2, "0");
  return `${String(m).padStart(2, "0")}:${text}`;
}

export function points(value: number): string {
  return value.toFixed(1).replace(".", ",").replace(/,0$/, "");
}

export function pointsWord(value: number): string {
  const n = Math.round(value);
  const whole = Math.abs(value - n) < 0.05;
  if (!whole) return `${points(value)} балла`;
  const last = n % 10;
  if (n % 100 >= 11 && n % 100 <= 14) return `${n} баллов`;
  if (last === 1) return `${n} балл`;
  if (last >= 2 && last <= 4) return `${n} балла`;
  return `${n} баллов`;
}

function active(jobs: readonly Job[] | undefined, kind: Job["kind"]): Job | undefined {
  return jobs?.find((j) => j.kind === kind && (j.state === "queued" || j.state === "running" || j.state === "cancelling"));
}

function textsWord(n: number): string {
  const last = n % 10;
  if (n % 100 >= 11 && n % 100 <= 14) return `${n} текстов`;
  if (last === 1) return `${n} текст`;
  if (last >= 2 && last <= 4) return `${n} текста`;
  return `${n} текстов`;
}

export function takesWord(n: number): string {
  const last = n % 10;
  if (n % 100 >= 11 && n % 100 <= 14) return `${n} дублей`;
  if (last === 1) return `${n} дубль`;
  if (last >= 2 && last <= 4) return `${n} дубля`;
  return `${n} дублей`;
}

type Part = Omit<RailItem, "lit" | "folded" | "current" | "name" | "id">;

function beatPart(s: Stations): Part {
  const b = s.beat;
  const bpm = b.bpm === null ? "" : ` · ${Math.round(b.bpm)} BPM`;
  if (b.kind === "own") {
    if (b.rendered) return { state: "done", value: `seed ${b.seed}${bpm}` };
    return { state: "todo", value: `seed ${b.seed} · не отрендерен` };
  }
  return { state: "done", value: `файл${bpm}` };
}

function textPart(s: Stations): Part {
  const t = s.text;
  if (t.version !== null && t.version !== undefined) return { state: "done", value: `v${t.version} · seed ${t.seed}` };
  const n = t.versions?.length ?? 0;
  if (t.seed === null || n === 0) return { state: "missing", value: "нет текста" };
  return { state: "todo", value: `${textsWord(n)} · не выбран` };
}

function recPart(s: Stations, ctx: RailContext, textDone: boolean): Part {
  const v = s.voice;
  if (v.kind === "suno") return { state: "bypass", value: "не нужна" };
  const rec = ctx.rec;
  if (rec?.recording) {
    const n = rec.take ? takeLabel(rec.take) : "дубль";
    return { state: "live", value: `${n} · ${clock(rec.seconds, false)}` };
  }
  if (rec && rec.stage !== "closed" && rec.stage !== "ready" && rec.stage !== "failed") {
    return { state: "todo", value: "открывается" };
  }
  if (rec?.stage === "ready") return { state: "todo", value: "готов к записи" };
  const n = v.takes?.length ?? 0;
  if (n > 0) return { state: "done", value: takesWord(n) };
  return { state: "todo", value: textDone ? "дублей нет" : "после текста" };
}

function takesPart(s: Stations, ctx: RailContext): Part {
  const v = s.voice;
  if (v.kind === "suno") {
    if (active(ctx.jobs, "separate")) return { state: "todo", value: "голос отделяется" };
    return v.separated ? { state: "done", value: "голос Suno · отделён" } : { state: "todo", value: "голос Suno · не отделён" };
  }
  if (v.chosen) return { state: "done", value: `взят ${takeLabel(v.chosen)}` };
  if (active(ctx.jobs, "rank")) return { state: "todo", value: "дубли оцениваются" };
  const top = ctx.ranking?.takes[0];
  if (top) return { state: "done", value: `${takeLabel(top.name)} · ${pointsWord(top.total)}` };
  const n = v.takes?.length ?? 0;
  return { state: "todo", value: n > 0 ? "не оценены" : "дублей нет" };
}

function mixPart(s: Stations, ctx: RailContext): Part {
  const m = s.mix;
  if (active(ctx.jobs, "auto")) return { state: "todo", value: "авто считается" };
  if (m.variant !== null) return { state: "done", value: m.variant === 0 ? "оставлен A · авто" : `оставлен ${"ABCD"[m.variant] ?? m.variant}` };
  if (!m.ready) {
    const voice = s.voice;
    if (voice.kind === "takes" && !voice.chosen) return { state: "todo", value: "ждёт выбор дубля" };
    return { state: "todo", value: m.reason === "stems not ready" ? "ждёт отделения голоса" : "ждёт голос" };
  }
  return { state: "todo", value: "авто не запускалось" };
}

function resultPart(s: Stations, ctx: RailContext): Part {
  if (active(ctx.jobs, "result")) return { state: "todo", value: "собирается" };
  const folder = s.result.folder;
  return folder ? { state: "done", value: folder } : { state: "todo", value: "не собран" };
}

// the six stations with their state; "lit" runs up to the last done one, bypassed stations
// in between stay on the line
export function railItems(ctx: RailContext): RailItem[] {
  const s = ctx.stations;
  const text = textPart(s);
  const parts: Record<StationId, Part> = {
    beat: beatPart(s),
    text,
    rec: recPart(s, ctx, text.state === "done"),
    takes: takesPart(s, ctx),
    mix: mixPart(s, ctx),
    result: resultPart(s, ctx),
  };
  let last = -1;
  STATION_IDS.forEach((id, i) => {
    const state = parts[id].state;
    if (state === "done" || state === "live") last = i;
  });
  return STATION_IDS.map((id, i) => ({
    id,
    name: STATION_NAMES[id],
    ...parts[id],
    lit: i <= last,
    folded: parts[id].state === "bypass",
    current: ctx.current === id,
  }));
}

// the station to open first: a live one, else the first that is not done, else the result
export function defaultStation(items: readonly RailItem[]): StationId {
  const live = items.find((i) => i.state === "live");
  if (live) return live.id;
  const next = items.find((i) => i.state !== "done" && i.state !== "bypass");
  return next ? next.id : "result";
}

// how far the line is lit, as the share of the way from the first station to the last
export function reachOf(items: readonly RailItem[]): number {
  let last = -1;
  items.forEach((item, i) => {
    if (item.lit) last = i;
  });
  return items.length < 2 || last < 0 ? 0 : last / (items.length - 1);
}

const STATE_WORD: Record<StationState, string> = {
  done: ", готово",
  todo: ", дальше",
  missing: ", не хватает входа",
  bypass: ", не нужна",
  live: ", идёт запись",
};

export interface RailView {
  el: HTMLElement;
  update(items: readonly RailItem[]): void;
  place(): void;
}

export function createRail(onPick: (id: StationId) => void): RailView {
  const nav = h("nav", { class: "rail", "aria-label": "Песня по шагам" });
  const list = h("ol", { class: "rail__list" });
  const lit = h("span", { class: "rail__lit" });
  const line = h("li", { class: "rail__line", "aria-hidden": "true" }, lit);
  const marker = h("li", { class: "rail__marker", "aria-hidden": "true" });
  const now = h("p", { class: "rail__now" });
  nav.append(list, now);
  let fallback = 0;

  const place = (): void => {
    const current = list.querySelector<HTMLElement>('[aria-current="step"]');
    const box = list.getBoundingClientRect();
    if (current) {
      marker.style.setProperty("--mx", `${current.getBoundingClientRect().left - box.left}px`);
      marker.style.setProperty("--mw", String(current.getBoundingClientRect().width));
    }
    const jacks = [...list.querySelectorAll<HTMLElement>(".station__jack")].filter((j) => j.getClientRects().length > 0);
    const reached = [...list.querySelectorAll<HTMLElement>('.station:not([data-lit="false"]) .station__jack')].filter(
      (j) => j.getClientRects().length > 0,
    );
    const first = jacks[0];
    const end = jacks[jacks.length - 1];
    const stop = reached[reached.length - 1];
    if (!first || !end || !stop || first === end) {
      nav.style.setProperty("--reach", String(fallback));
      return;
    }
    const mid = (j: HTMLElement): number => j.getBoundingClientRect().left + j.getBoundingClientRect().width / 2;
    const span = mid(end) - mid(first);
    nav.style.setProperty("--reach", span > 0 ? ((mid(stop) - mid(first)) / span).toFixed(4) : String(fallback));
  };

  const update = (items: readonly RailItem[]): void => {
    fallback = reachOf(items);
    if (items.some((i) => i.folded)) nav.dataset["fold"] = "rec";
    else delete nav.dataset["fold"];
    list.replaceChildren(line);
    for (const item of items) {
      const link = h(
        "a",
        { class: "station__link", href: `#${item.id}`, "aria-current": item.current ? "step" : undefined },
        h("span", { class: "station__jack", "aria-hidden": "true" }),
        h("span", { class: "station__name" }, item.name, h("span", { class: "vh" }, STATE_WORD[item.state])),
        h("span", { class: "station__value num" }, item.value),
      );
      link.addEventListener("click", (e) => {
        e.preventDefault();
        onPick(item.id);
      });
      list.append(h("li", { class: "station", "data-state": item.state, "data-lit": String(item.lit) }, link));
    }
    list.append(marker);
    const cur = items.find((i) => i.current);
    now.replaceChildren(...(cur ? [h("b", {}, cur.name), " · ", h("span", {}, cur.value)] : []));
    if (cur?.state === "live") now.dataset["live"] = "true";
    else delete now.dataset["live"];
    nav.style.setProperty("--reach", String(fallback));
    place();
  };

  return { el: nav, update, place };
}
