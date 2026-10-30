// The takes station: the ranking as a table, one take auditioned at a time over the beat,
// and the pick that sets the song's voice.

import type { Ranking, RankedTake, SkippedTake } from "./api.ts";
import { pointsWord, takeLabel, takesWord } from "./rail.ts";
import { jobActive, jobBar, jobOf, needCard } from "./steps.ts";
import type { Ctx, StationView } from "./steps.ts";
import { decimal } from "./variants.ts";
import { h } from "../ui/dom.ts";
import { playButton } from "../ui/play.ts";

// why a take was left out, in plain words
export function skipText(s: SkippedTake): string {
  const v = s.values;
  switch (s.code) {
    case "other_rate":
      return `частота ${v["rate"]} Гц, у остальных дублей ${v["others"]} Гц`;
    case "silent":
      return "дубль пустой или почти без звука";
    case "too_long":
      return `дубль ${decimal(Number(v["seconds"]), 1)} с, бит ${decimal(Number(v["beat_seconds"]), 1)} с: дубль длиннее бита`;
    case "incomplete":
      return "запись дубля не завершена или её журнала нет";
    case "other_beat":
      return "записан под другой файл бита";
    case "unreadable":
      return `файл не удалось прочитать (${s.reason})`;
    default:
      return s.reason;
  }
}

export interface TakeRow {
  name: string;
  label: string;
  place: string;
  points: string;
  bar: number;
  timing: string;
  clips: string;
  noise: string;
  dropouts: string;
  bleed: string;
  headphones: boolean;
}

const dash = "—";

export function takeRows(ranking: Ranking): TakeRow[] {
  const top = Math.max(...ranking.takes.map((t) => t.total), 0);
  return ranking.takes.map((t: RankedTake) => {
    const m = t.metrics;
    return {
      // the server's take routes take the file name; the ranking names takes by their stem
      name: t.file.split(/[\\/]/).pop() ?? t.name,
      label: takeLabel(t.name),
      place: t.rank === null ? dash : String(t.rank),
      points: ranking.ranked ? pointsWord(t.total) : dash,
      bar: top > 0 && ranking.ranked ? Math.max(0, Math.min(1, t.total / top)) : 0,
      timing: m.timing_ms === null ? dash : decimal(m.timing_ms, 1),
      clips: String(m.clip_runs),
      noise: m.floor_below_voice_db === null ? dash : decimal(m.floor_below_voice_db, 1),
      dropouts: m.dropouts === null ? "?" : m.dropout_ms ? `${m.dropouts} / ${Math.round(m.dropout_ms)} мс` : String(m.dropouts),
      bleed: decimal(m.bleed, 2),
      headphones: t.headphones,
    };
  });
}

// plays one take at a time from a blob; the previous url is revoked before the next
export class TakePlayer {
  #audio: HTMLAudioElement | null = null;
  #url: string | null = null;
  #name: string | null = null;
  #wait = 0;
  readonly #fetch: (name: string) => Promise<Blob>;
  readonly #changed: () => void;

  constructor(fetchTake: (name: string) => Promise<Blob>, changed: () => void) {
    this.#fetch = fetchTake;
    this.#changed = changed;
  }

  get name(): string | null {
    return this.#name;
  }

  get time(): { now: number; total: number } {
    return { now: this.#audio?.currentTime ?? 0, total: this.#audio?.duration || 0 };
  }

  async toggle(name: string): Promise<void> {
    if (this.#name === name) {
      this.stop();
      return;
    }
    this.stop();
    const mine = ++this.#wait;
    this.#name = name;
    this.#changed();
    try {
      const blob = await this.#fetch(name);
      if (mine !== this.#wait) return;
      this.#url = URL.createObjectURL(blob);
      const audio = new Audio(this.#url);
      this.#audio = audio;
      audio.addEventListener("ended", () => {
        if (this.#audio === audio) this.stop();
      });
      audio.addEventListener("timeupdate", this.#changed);
      await audio.play();
    } catch {
      if (mine === this.#wait) this.stop();
    }
  }

  stop(): void {
    this.#wait += 1;
    if (this.#audio) {
      this.#audio.pause();
      this.#audio.removeAttribute("src");
      this.#audio.load();
    }
    if (this.#url) URL.revokeObjectURL(this.#url);
    this.#audio = null;
    this.#url = null;
    const was = this.#name;
    this.#name = null;
    if (was !== null) this.#changed();
  }
}

export function createTakesStation(ctx: Ctx, lyric: StationView): StationView {
  const el = h("div", { class: "view view--side" });
  const main = h("div", {});
  const side = h("div", {}, lyric.el);
  el.append(main, side);
  let ranking: Ranking | null = null;
  let loaded = false;
  let closed = false;
  let lastJob = "";
  let wait = 0;
  const player = new TakePlayer(
    (name) => ctx.api.takeAudio(name, true),
    () => {
      if (!closed) mark();
    },
  );

  function mark(): void {
    const { now, total } = player.time;
    for (const row of main.querySelectorAll<HTMLElement>("tr[data-take]")) {
      const on = row.dataset["take"] === player.name;
      row.dataset["playing"] = String(on);
      row.querySelector(".tplay")?.setAttribute("aria-pressed", String(on));
      const bar = row.querySelector<HTMLElement>(".takes__progress");
      if (bar) bar.style.transform = `scaleX(${total > 0 ? Math.min(1, now / total) : 0})`;
    }
    const status = main.querySelector(".takes__status");
    if (status) {
      status.replaceChildren(
        ...(player.name
          ? ["Слушается один дубль, поверх бита. Сейчас: ", h("b", {}, takeLabel(player.name)), total > 0 ? `, ${Math.floor(now)} из ${Math.floor(total)} с.` : "."]
          : ["Дубль слушается один, поверх бита."]),
      );
    }
  }

  function fetchRanking(): void {
    const mine = ++wait;
    void ctx.api.takes().then(
      (r) => {
        if (closed || mine !== wait) return;
        ranking = r.ranking;
        loaded = true;
        draw();
      },
      () => {
        if (closed || mine !== wait) return;
        loaded = true;
        draw();
      },
    );
  }

  function suno(): void {
    const v = ctx.state().song.stations.voice;
    main.replaceChildren(
      h(
        "section",
        { class: "card", "aria-label": "Голос Suno" },
        h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Голос Suno"), h("p", { class: "card__meta num" }, v.file ?? ""), jobBar(ctx, "separate", "Отделить голос", "Отделить заново")),
        h("div", { class: "card__body" }, h("p", { class: "lede" }, v.separated ? "Голос отделён от бита. Дальше сведение." : "Голос ещё не отделён. Отделение идёт отдельной задачей и занимает несколько минут.")),
      ),
    );
    side.hidden = true;
  }

  let drawn = "";

  function draw(): void {
    const { song, jobs } = ctx.state();
    drawn = JSON.stringify([song.stations.voice, jobs.find((j) => j.kind === "rank")]);
    const voice = song.stations.voice;
    side.hidden = false;
    if (voice.kind === "suno") {
      suno();
      return;
    }
    const rankJob = jobOf(jobs, "rank");
    const rows = ranking ? takeRows(ranking) : [];
    if (!ranking || (rows.length === 0 && !(ranking.skipped.length > 0))) {
      main.replaceChildren(
        needCard({
          title: "Дубли",
          what: (voice.takes?.length ?? 0) === 0 ? "Дублей пока нет." : `${takesWord(voice.takes?.length ?? 0)}, оценки ещё нет.`,
          how: (voice.takes?.length ?? 0) === 0 ? "Запиши дубль на шаге «Запись»: оценка начнётся сама, когда файл закроется." : "Оценка идёт отдельной задачей после записи. Запусти её, если она не началась.",
          actions: [
            (() => {
              const b = h("button", { class: "btn btn--solid", type: "button" }, (voice.takes?.length ?? 0) === 0 ? "К записи" : "Оценить");
              b.addEventListener("click", () => ((voice.takes?.length ?? 0) === 0 ? ctx.go("rec") : void ctx.start("rank")));
              return b;
            })(),
          ],
        }),
      );
      if (!loaded) main.prepend(h("p", { class: "lede busy" }, "Загружаю оценку"));
      return;
    }
    const table = h("table", { class: "takes" });
    table.append(
      h(
        "thead",
        {},
        h(
          "tr",
          {},
          ...["Дубль", "Место", "Баллы", "Тайминг, мс", "Клипов", "Шум, дБ", "Выпадения", "Бит в микрофоне"].map((t) => h("th", { scope: "col" }, t)),
          h("th", { scope: "col" }, h("span", { class: "vh" }, "Действие")),
        ),
      ),
    );
    const body = h("tbody");
    for (const row of rows) {
      const mine = voice.chosen === row.name;
      const pickButton = h("button", { class: mine ? "btn" : "btn btn--solid", type: "button", disabled: mine }, mine ? "Взят" : "Взять этот");
      pickButton.addEventListener("click", () => void ctx.change({ voice_chosen: row.name }));
      body.append(
        h(
          "tr",
          { "data-take": row.name, "data-playing": String(player.name === row.name) },
          h("td", { class: "takes__c-take" }, h("span", { class: "takes__row-take" }, playButton(row.label, player.name === row.name, () => void player.toggle(row.name)), h("span", { class: "takes__name" }, row.label), h("span", { class: "takes__progress", "aria-hidden": "true", style: "transform: scaleX(0)" }))),
          h("td", { class: "takes__c-place" }, h("span", { class: "takes__place" }, row.place)),
          h("td", { class: "takes__c-score" }, h("span", { class: "takes__score" }, h("span", { class: "num" }, row.points), h("span", { class: "distbar", style: `--v: ${row.bar.toFixed(2)}`, "aria-hidden": "true" }))),
          h("td", { class: "num takes__c-m" }, row.timing),
          h("td", { class: "num takes__c-m" }, row.clips),
          h("td", { class: "num takes__c-m" }, row.noise),
          h("td", { class: "num takes__c-m" }, row.dropouts),
          h("td", { class: "num takes__c-m" }, row.bleed, row.headphones ? " · в наушниках" : ""),
          h("td", { class: "takes__end" }, pickButton),
        ),
      );
    }
    for (const skip of ranking.skipped) {
      body.append(
        h(
          "tr",
          { "data-out": "true" },
          h("td", { class: "takes__c-take" }, h("span", { class: "takes__row-take" }, h("span", { class: "takes__name" }, takeLabel(skip.name)))),
          h("td", { class: "takes__c-note", colspan: 8 }, skipText(skip)),
          h("td", { class: "takes__end" }),
        ),
      );
    }
    table.append(body);
    const top = ranking.takes[0];
    main.replaceChildren(
      h(
        "section",
        { class: "card", "aria-label": "Дубли" },
        h(
          "header",
          { class: "card__head" },
          h("h2", { class: "card__title" }, "Дубли"),
          h("p", { class: "card__meta" }, `оценено ${ranking.takes.length}${ranking.skipped.length ? `, пропущено ${ranking.skipped.length}` : ""}${top && ranking.ranked ? ` · первый: ${takeLabel(top.name)}` : ""}`),
          jobBar(ctx, "rank", "Оценка идёт", "Оценить заново"),
        ),
        table,
        h("p", { class: "takes__status" }, "Дубль слушается один, поверх бита."),
        ranking.ranked ? null : h("p", { class: "takes__status" }, "Дубль один: сравнивать не с чем, места нет."),
        h("p", { class: "takes__status" }, "Баллы показывают, какой дубль чище и ровнее по замерам; решают уши."),
        rankJob && jobActive(rankJob) ? h("p", { class: "takes__status busy" }, "Оценка идёт") : null,
      ),
    );
    mark();
  }

  draw();
  fetchRanking();
  return {
    el,
    update() {
      lyric.update();
      const job = ctx.state().jobs.find((j) => j.kind === "rank");
      const sig = `${job?.state}:${job?.finished}`;
      if (sig !== lastJob) {
        const was = lastJob;
        lastJob = sig;
        if (was !== "" || job?.state === "done") fetchRanking();
      }
      const shape = JSON.stringify([ctx.state().song.stations.voice, job]);
      if (shape === drawn) return;
      draw();
    },
    dispose() {
      closed = true;
      player.stop();
      lyric.dispose();
    },
  };
}
