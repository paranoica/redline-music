// The workspaces of the beat, mix, recording and result stations.

import { mount as mountMix } from "../main.ts";
import { mount as mountBeat } from "../beat/main.ts";
import { ApiError } from "./api.ts";
import type { RecSnapshot, VariantsReply } from "./api.ts";
import { Poller } from "./poll.ts";
import { createRecView, recPhase } from "./record.ts";
import { takeLabel } from "./rail.ts";
import { copyButton, deskNote, desktopQuery, jobActive, jobBar, jobOf, needCard, SurfaceHost } from "./steps.ts";
import type { Ctx, StationView } from "./steps.ts";
import { createPads, padModel, padSettings } from "./variants.ts";
import { h } from "../ui/dom.ts";

export interface KeyedView extends StationView {
  // true when the key was taken
  key?(e: KeyboardEvent): boolean;
}

function goButton(label: string, click: () => void, solid = true): HTMLButtonElement {
  const b = h("button", { class: solid ? "btn btn--solid" : "btn btn--ghost", type: "button" }, label);
  b.addEventListener("click", click);
  return b;
}

// a surface that lives in a slot, only on a wide screen
function surfaceSlot(
  ctx: Ctx,
  host: SurfaceHost,
  kind: string,
  what: string,
  tail: string,
): { el: HTMLElement; mountNow(): void; dispose(): void } {
  const slot = h("div", { class: "surface-slot" });
  const note = deskNote(what, tail);
  const el = h("div", { class: "surface-wrap" }, slot, note);
  const query = desktopQuery();
  const sync = (): void => {
    const wide = query ? query.matches : true;
    if (!wide) {
      void host.dispose();
      return;
    }
    if (host.place(kind, slot)) return;
    const mount = kind === "beat" ? mountBeat : mountMix;
    const base = kind === "beat" ? ctx.api.beatBase : ctx.api.mixBase;
    void host.show(kind, mount, slot, base, ctx.token).catch(() => {
      slot.replaceChildren(h("p", { class: "lede" }, "Поверхность не загрузилась."));
    });
  };
  query?.addEventListener("change", sync);
  return {
    el,
    mountNow: sync,
    dispose: () => query?.removeEventListener("change", sync),
  };
}

export function createBeatStation(ctx: Ctx, host: SurfaceHost): KeyedView {
  const el = h("div", { class: "view view--one" });
  let seen = "";
  let slot: ReturnType<typeof surfaceSlot> | null = null;

  function draw(): void {
    const b = ctx.state().song.stations.beat;
    seen = JSON.stringify(b);
    slot?.dispose();
    slot = null;
    const bpm = b.bpm === null ? "" : ` · ${Math.round(b.bpm)} BPM`;
    if (b.kind === "file") {
      void host.dispose();
      el.replaceChildren(
        h(
          "section",
          { class: "card", "aria-label": "Бит" },
          h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Бит"), h("p", { class: "card__meta num" }, `файл${bpm}`)),
          h("div", { class: "card__body" }, h("p", { class: "result__where" }, b.file ?? ""), h("p", { class: "lede" }, "Готовый бит из файла. Темп взят из сетки бита; тональность не определяется.")),
        ),
      );
      return;
    }
    const seed = h("input", { class: "field", value: String(b.seed ?? ""), inputmode: "numeric", "aria-label": "seed бита" });
    const swap = goButton("Сменить", () => {
      const n = Number(seed.value);
      if (!Number.isInteger(n) || n < 0) {
        ctx.notice("seed бита — целое число от нуля.");
        return;
      }
      if (n === b.seed) return;
      void ctx.change({ beat: { own: n } }).then((ok) => {
        if (ok) void host.dispose().then(() => slot?.mountNow());
      });
    }, false);
    slot = surfaceSlot(ctx, host, "beat", "Редактор бита", "Бит уже выбран, дальше — текст.");
    el.replaceChildren(
      h(
        "section",
        { class: "card", "aria-label": "Бит" },
        h(
          "header",
          { class: "card__head" },
          h("h2", { class: "card__title" }, "Бит"),
          h("p", { class: "card__meta num" }, `свой · seed ${b.seed}${bpm}${b.file ? ` · ${b.file}` : ""}${b.rendered ? "" : " · не отрендерен"}`),
          h("div", { class: "card__end" }, h("label", { class: "mix__seed" }, "seed бита ", seed), swap),
        ),
        h("div", { class: "card__body" }, slot.el),
      ),
    );
    slot.mountNow();
  }

  draw();
  return {
    el,
    update() {
      if (JSON.stringify(ctx.state().song.stations.beat) !== seen) draw();
    },
    dispose() {
      slot?.dispose();
      void host.dispose();
    },
  };
}

export function createMixStation(ctx: Ctx, host: SurfaceHost): KeyedView {
  const el = h("div", { class: "view view--one" });
  let variants: VariantsReply | null = null;
  let selected = 0;
  let seedText = "";
  let seen = "";
  let closed = false;
  let lastAuto = "";
  let slot: ReturnType<typeof surfaceSlot> | null = null;
  let blocked = false;
  let wait = 0;
  const pads = createPads((i) => {
    selected = i;
    paintPads();
    const settings = padSettings(variants, i);
    if (settings) host.load("mix", settings, `вариант ${"ABCD"[i] ?? i}`);
  });

  function paintPads(): void {
    const kept = ctx.state().song.stations.mix.variant;
    pads.update(padModel(variants, kept), selected);
    const keep = el.querySelector<HTMLButtonElement>("[data-ref=keep]");
    if (keep) keep.textContent = `Оставить ${"ABCD"[selected] ?? selected}`;
  }

  function loadVariants(): void {
    const mine = ++wait;
    void ctx.api.variants().then(
      (v) => {
        if (closed || mine !== wait) return;
        variants = v;
        if (seedText === "" && v) seedText = String(v.seed);
        const kept = ctx.state().song.stations.mix.variant;
        selected = kept ?? 0;
        draw();
      },
      () => {},
    );
  }

  function draw(): void {
    const { song, jobs } = ctx.state();
    const m = song.stations.mix;
    seen = signature();
    slot?.dispose();
    slot = null;
    if (!m.ready || blocked) {
      void host.dispose();
      const need = mixNeed(m.reason ?? "no voice chosen", song.stations.voice.kind, song.song.name);
      el.replaceChildren(
        needCard({
          title: "Сведение",
          what: need.what,
          how: need.how,
          ...(need.command ? { command: need.command } : {}),
          actions: [
            ...(need.job ? [jobBar(ctx, need.job, "Разделить")] : []),
            ...(need.go ? [goButton(need.go === "takes" ? "К дублям" : "К биту", () => ctx.go(need.go ?? "takes"))] : []),
          ],
        }),
      );
      return;
    }
    const auto = jobOf(jobs, "auto");
    const seed = h("input", { class: "field", value: seedText, inputmode: "numeric", "aria-label": "seed вариантов", placeholder: "0" });
    seed.addEventListener("input", () => {
      seedText = seed.value;
    });
    const run = goButton(jobActive(auto) ? "Авто считается" : variants ? "Авто заново" : "Авто", () => {
      const n = seedText.trim() === "" ? undefined : Number(seedText);
      if (n !== undefined && (!Number.isInteger(n) || n < 0)) {
        ctx.notice("seed вариантов — целое число от нуля.");
        return;
      }
      void ctx.start("auto", n);
    }, false);
    run.disabled = jobActive(auto);
    const keep = goButton(`Оставить ${"ABCD"[selected] ?? selected}`, () => void ctx.change({ mix_variant: selected }));
    keep.dataset["ref"] = "keep";
    const none = goButton("Ни один", () => void ctx.change({ mix_variant: null }), false);
    none.disabled = m.variant === null;
    const jobEnd = jobActive(auto) ? jobBar(ctx, "auto", "Авто считается") : null;
    slot = surfaceSlot(ctx, host, "mix", "Сведение", "Варианты выше выбираются и здесь.");
    paintPads();
    const body = h("div", { class: "card__body" });
    if (variants) {
      body.append(
        h("p", { class: "lede" }, "A — ближайшее к референсам, что нашёл поиск. B, C и D сдвигают одну настройку на шаг от A, поэтому по числам обычно дальше. Их стоит послушать: числа говорят, что ближе, а не что звучит приятнее."),
        pads.el,
        h("div", { class: "mix__under" }, h("span", { class: "mix__keep" }, keep, none)),
      );
    } else {
      body.append(h("p", { class: "lede" }, jobActive(auto) ? "Авто-сведение считается." : "Авто-сведение ещё не запускалось. Оно подберёт настройки и четыре варианта для прослушивания."));
    }
    body.append(h("div", { class: "mix__under" }, h("label", { class: "mix__seed" }, "seed вариантов ", seed), run, jobEnd), slot.el);
    el.replaceChildren(
      h(
        "section",
        { class: "card", "aria-label": "Сведение" },
        h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Сведение"), h("p", { class: "card__meta" }, `голос: ${song.stations.voice.chosen ? takeLabel(song.stations.voice.chosen) : "Suno"}${m.variant !== null ? ` · оставлен ${"ABCD"[m.variant] ?? m.variant}` : ""}`)),
        body,
      ),
    );
    slot.mountNow();
  }

  function signature(): string {
    const { song, jobs } = ctx.state();
    const watched = jobs.filter((j) => j.kind === "auto" || j.kind === "separate");
    return JSON.stringify([song.stations.mix, song.stations.voice.chosen, watched, blocked]);
  }

  // the server says when the mix can be built; the probe is asked again whenever it says so
  let probed = false;
  function probe(): void {
    probed = ctx.state().song.stations.mix.ready;
    void ctx.api.mixReady().then(
      (r) => {
        if (closed) return;
        blocked = r === "no_voice";
        draw();
      },
      (error: unknown) => {
        if (closed) return;
        if (!(error instanceof ApiError)) return;
        draw();
      },
    );
  }

  probe();
  draw();
  loadVariants();
  return {
    el,
    update() {
      const auto = ctx.state().jobs.find((j) => j.kind === "auto");
      const done = auto?.state === "done" ? String(auto.finished) : "";
      if (done !== lastAuto) {
        lastAuto = done;
        if (done) loadVariants();
      }
      if (ctx.state().song.stations.mix.ready !== probed) probe();
      else if (signature() !== seen) draw();
      else paintPads();
    },
    dispose() {
      closed = true;
      slot?.dispose();
      void host.dispose();
    },
  };
}

export function createResultStation(ctx: Ctx): StationView {
  const el = h("div", { class: "view view--one" });
  let seen = "";
  const signature = (): string => JSON.stringify([ctx.state().song.stations, ctx.state().jobs.find((j) => j.kind === "result")]);
  function draw(): void {
    seen = signature();
    const { song, jobs } = ctx.state();
    const folder = song.stations.result.folder;
    const job = jobOf(jobs, "result");
    const ready = song.stations.mix.variant !== null;
    const body = h("div", { class: "card__body" });
    if (folder) {
      body.append(
        h("div", { class: "result__path" }, h("span", { class: "result__where" }, folder), copyButton(() => folder, "Скопировать путь")),
        h(
          "dl",
          { class: "files" },
          ...(
            [
              ["final.wav", "готовый трек, 24 бит"],
              ["original.wav", "как было до обработки, на той же громкости"],
              ["stems/", "голос и бит отдельно"],
              ["compare/", "пары для сравнения на равной громкости"],
              ["chain.json", "все настройки цепи"],
              ["report.md", "отчёт: насколько ближе к референсам"],
            ] as const
          ).flatMap(([name, what]) => [h("dt", {}, name), h("dd", {}, what)]),
        ),
      );
    } else {
      body.append(h("p", { class: "lede" }, ready ? "Итог ещё не собран. Он ляжет в папку output/ рядом с настройками цепи и отчётом." : "Итог собирается из выбранного дубля и оставленного варианта сведения. Оставь вариант на шаге «Сведение»."));
    }
    const start = goButton(folder ? "Собрать заново" : "Собрать итог", () => void ctx.start("result"), !folder);
    start.disabled = !ready || jobActive(job);
    const end = jobActive(job) ? jobBar(ctx, "result", "Собирается") : h("div", { class: "card__end" }, start);
    if (!jobActive(job) && job?.state === "failed") end.append(h("span", { class: "card__meta" }, `не вышло: ${job.reason ?? "причина не названа"}`));
    el.replaceChildren(h("section", { class: "card", "aria-label": "Итог" }, h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Итог"), end), body));
  }
  draw();
  return {
    el,
    update() {
      if (signature() !== seen) draw();
    },
    dispose() {},
  };
}

export interface RecHooks {
  snapshot(s: RecSnapshot | null): void;
}

export function createRecStation(ctx: Ctx, lyric: StationView, hooks: RecHooks): KeyedView {
  const el = h("div", { class: "view view--rec" });
  const { song } = ctx.state();
  const beat = song.stations.beat;
  const beatLine = beat.kind === "own" ? `seed ${beat.seed}${beat.bpm ? ` · ${Math.round(beat.bpm)} BPM` : ""}` : "файл";
  let latest: RecSnapshot | null = null;
  let busy = false;
  let closed = false;

  const poller = new Poller({
    ms: 50,
    tick: async () => {
      const s = await ctx.api.recState();
      if (closed) return;
      latest = s;
      view.update(s, performance.now());
      hooks.snapshot(s);
    },
  });

  const run = async (call: () => Promise<{ code: string }>, note?: (code: string) => string | null): Promise<void> => {
    if (busy) return;
    busy = true;
    try {
      const reply = await call();
      view.note(note ? note(reply.code) : null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        view.note(error.code === "out_of_sync" ? "Движок не отвечает: перезапусти его." : error.code === "not_ready" ? "Движок ещё не готов." : error.message);
      } else {
        view.note(error instanceof Error ? error.message : "Не вышло.");
      }
    } finally {
      busy = false;
    }
    try {
      latest = await ctx.api.recState();
      view.update(latest, performance.now());
      hooks.snapshot(latest);
      void ctx.reload();
    } catch {
      // the poll tries again
    }
  };

  const view = createRecView({
    beatLine,
    actions: {
      open: () => void run(() => ctx.api.recOpen(), () => null),
      toggle: () =>
        void run(() => ctx.api.recToggle(), (code) => (code === "kept" ? "Дубль сохранён." : code === "recording" ? null : code === "discarded" ? "Дубль сброшен." : `Ответ движка: ${code}`)),
      discard: () => void run(() => ctx.api.recDiscard(), () => "Дубль сброшен."),
      calibrate: (mode) => void run(() => ctx.api.recCalibrate(mode), () => "Калибровка запущена; движок откроется сам."),
      close: () => void run(() => ctx.api.recClose(), () => null),
    },
  });
  el.append(view.el, lyric.el);
  view.update(null, performance.now());

  const onVisible = (): void => poller.setVisible(document.visibilityState !== "hidden");
  document.addEventListener("visibilitychange", onVisible);
  onVisible();

  return {
    el,
    update: () => lyric.update(),
    shown: (on) => poller.setWanted(on),
    key(e) {
      if (e.code !== "KeyR" || e.ctrlKey || e.metaKey || e.altKey) return false;
      const phase = recPhase(latest);
      if (phase !== "ready" && phase !== "live") return false;
      void run(() => ctx.api.recToggle(), (code) => (code === "kept" ? "Дубль сохранён." : null));
      return true;
    },
    dispose() {
      closed = true;
      poller.dispose();
      document.removeEventListener("visibilitychange", onVisible);
      lyric.dispose();
    },
  };
}

export interface MixNeed {
  what: string;
  how: string;
  command: string | null;
  go: "takes" | "beat" | null;
  job: "separate" | null;
}

// why the mix cannot be built, from the server's reason, and the way out
export function mixNeed(reason: string, voice: "takes" | "suno", song: string): MixNeed {
  if (reason === "stems not ready")
    return voice === "suno"
      ? { what: "Голос Suno и референсы ещё не разделены.", how: "Сведение берёт голос и бит из кэша. Разделение идёт один раз, несколько минут.", command: null, go: null, job: "separate" }
      : { what: "Референсы ещё не разделены на голос и бит.", how: "Сведение сравнивает голос с голосом референсов. Разделение идёт один раз, несколько минут.", command: null, go: null, job: "separate" };
  if (reason === "the song has no references")
    return {
      what: "У песни нет референсов.",
      how: "Сведение и варианты подбираются к референсам. Добавь их из терминала:",
      command: `uv run redline song refs ${song} --ref <файлы референсов>`,
      go: null,
      job: null,
    };
  if (reason === "no voice chosen")
    return voice === "takes"
      ? { what: "Для сведения нет голоса.", how: "Выбери дубль на шаге «Дубли»: сведение берёт голос оттуда.", command: null, go: "takes", job: null }
      : { what: "Для сведения нет голоса.", how: "Укажи голос песни.", command: null, go: "beat", job: null };
  return { what: `Сведение не собрать: ${reason}.`, how: "Проверь файлы песни.", command: null, go: null, job: null };
}
