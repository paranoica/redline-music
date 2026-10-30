// The recording station: the engine's snapshot as a model, and the card that shows it.
// Red is for the take in progress only.

import type { RecReply, RecSnapshot } from "./api.ts";
import { clock, takeLabel } from "./rail.ts";
import { decimal } from "./variants.ts";
import { h } from "../ui/dom.ts";

export type RecPhase = "closed" | "opening" | "calibrating" | "ready" | "live" | "stopping" | "failed" | "out_of_sync";

export const OPEN_STAGES = ["building", "launching", "relaunching", "analysing", "level_check", "ready"] as const;
export type OpenStage = (typeof OPEN_STAGES)[number];

const STAGE_NAMES: Record<OpenStage, string> = {
  building: "Сборка движка",
  launching: "Запуск",
  relaunching: "Перезапуск на частоте бита",
  analysing: "Анализ монитора",
  level_check: "Проверка уровня",
  ready: "Готов",
};

export interface StageRow {
  name: string;
  state: "done" | "now" | "wait" | "fail";
  value: string;
}

export function recPhase(s: RecSnapshot | null): RecPhase {
  if (!s) return "closed";
  if (s.out_of_sync) return "out_of_sync";
  if (s.stopping) return "stopping";
  if (s.recording) return "live";
  if (s.stage === "failed") return "failed";
  if (s.stage === "closed") return "closed";
  if (s.stage === "calibrating") return "calibrating";
  return s.stage === "ready" ? "ready" : "opening";
}

// `failedAt` is the stage the engine was in before it failed, when the page saw it
export function stageRows(s: RecSnapshot, failedAt: OpenStage | null): StageRow[] {
  const at = s.stage === "failed" ? (failedAt ?? "building") : (s.stage as OpenStage);
  const index = OPEN_STAGES.indexOf(at);
  return OPEN_STAGES.map((stage, i) => {
    let state: StageRow["state"] = i < index ? "done" : i === index ? "now" : "wait";
    if (s.stage === "ready") state = "done";
    if (s.stage === "failed" && i === index) state = "fail";
    let value = state === "done" ? "готово" : state === "now" ? "идёт" : state === "fail" ? "не вышло" : "ждёт";
    if (stage === "building" && state === "done") value = "собран";
    if (stage === "relaunching" && state === "done" && s.rate) value = `${s.rate / 1000} кГц`.replace(".", ",");
    if (stage === "analysing" && state === "done" && s.monitor_ms !== null) value = `${decimal(s.monitor_ms, 1)} мс`;
    return { name: STAGE_NAMES[stage], state, value };
  });
}

// the meter's fill for a peak in dB over a 60 dB scale
export function meterLevel(db: number | null): number {
  if (db === null || !Number.isFinite(db)) return 0;
  return Math.min(1, Math.max(0, (db + 60) / 60));
}

export function dbText(db: number | null): string {
  return db === null || !Number.isFinite(db) ? "— дБ" : `пик ${decimal(db, 1)} дБ`;
}

// a peak that holds for a moment, then falls
export class PeakHold {
  #level = 0;
  #at = 0;

  push(level: number, now: number): number {
    if (level >= this.#level || now - this.#at > 1500) {
      this.#level = level;
      this.#at = now;
    }
    return this.#level;
  }

  reset(): void {
    this.#level = 0;
  }
}

export function toggleNote(reply: RecReply): string | null {
  switch (reply.code) {
    case "recording":
      return null;
    case "kept":
      return "Дубль сохранён.";
    case "discarded":
      return "Дубль сброшен.";
    default:
      return `Ответ движка: ${reply.code}`;
  }
}

export const CALIBRATION: Record<"cup" | "loopback" | "speakers", string> = {
  cup: "Поставь наушник чашкой к микрофону",
  loopback: "Соедини выход карты со входом кабелем",
  speakers: "Колонки: задержка выйдет неточной",
};

export interface RecActions {
  open(): void;
  toggle(): void;
  discard(): void;
  calibrate(mode: "cup" | "loopback" | "speakers"): void;
  close(): void;
}

export interface RecView {
  el: HTMLElement;
  update(snapshot: RecSnapshot | null, now: number): void;
  note(text: string | null): void;
}

export interface RecOptions {
  actions: RecActions;
  beatLine: string;
}

export function createRecView({ actions, beatLine }: RecOptions): RecView {
  const card = h("section", { class: "card rec", "aria-label": "Запись" });
  let failedAt: OpenStage | null = null;
  let calibrating = false;
  let mode: "cup" | "loopback" | "speakers" = "cup";
  let armDiscard = false;
  let discardTimer: ReturnType<typeof setTimeout> | null = null;
  let message: string | null = null;
  const hold = new PeakHold();
  let shape = "";

  const head = (engine: (string | Node)[]): HTMLElement =>
    h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Запись"), h("p", { class: "rec__engine" }, ...engine));

  const noteLine = (): HTMLElement | null => (message ? h("p", { class: "rec__warn", role: "status" }, message) : null);

  function build(s: RecSnapshot | null, phase: RecPhase): void {
    card.replaceChildren();
    card.dataset["stage"] = phase === "live" ? "live" : phase === "opening" ? "opening" : phase === "ready" ? "ready" : phase;
    if (calibrating) {
      const choice = h("div", { class: "seg seg--small", role: "radiogroup", "aria-label": "Как калибровать" });
      const keys = Object.keys(CALIBRATION) as (keyof typeof CALIBRATION)[];
      choice.style.setProperty("--count", String(keys.length));
      choice.style.setProperty("--at", String(keys.indexOf(mode)));
      choice.append(h("span", { class: "seg__marker", "aria-hidden": "true" }));
      const names = { cup: "чашка", loopback: "кабель", speakers: "колонки" };
      for (const key of keys) {
        const item = h("button", { class: "seg__item", type: "button", role: "radio", "aria-checked": String(key === mode) }, names[key]);
        item.addEventListener("click", () => {
          mode = key;
          build(s, phase);
        });
        choice.append(item);
      }
      const go = h("button", { class: "btn btn--solid", type: "button" }, "Начать");
      go.addEventListener("click", () => {
        calibrating = false;
        actions.calibrate(mode);
      });
      const cancel = h("button", { class: "btn btn--ghost", type: "button" }, "Отмена");
      cancel.addEventListener("click", () => {
        calibrating = false;
        build(s, phase);
      });
      card.append(
        head([h("b", {}, "калибровка"), " задержки"]),
        h(
          "div",
          { class: "card__body" },
          h("p", { class: "state__say" }, CALIBRATION[mode]),
          h(
            "p",
            { class: "state__why" },
            "Прозвучит свип: по нему считается задержка. На время калибровки сессия закрывается, потом движок откроется сам. Через колонки в задержку попадёт путь звука по воздуху, для дублей она будет неточной.",
          ),
          h("p", {}, choice),
          h("div", { class: "state__acts" }, go, cancel),
        ),
      );
      return;
    }
    if (phase === "calibrating") {
      card.append(
        head(["движок ", h("b", {}, "калибруется")]),
        h(
          "div",
          { class: "card__body" },
          h("p", { class: "state__say busy" }, "Калибровка: идёт свип"),
          h("p", { class: "state__why" }, "Сессия закрыта на время калибровки; когда свип кончится, движок откроется сам."),
        ),
      );
      return;
    }
    if (phase === "closed" || !s) {
      const open = h("button", { class: "btn btn--solid", type: "button" }, "Открыть движок");
      open.addEventListener("click", actions.open);
      card.append(
        head(["движок ", h("b", {}, "закрыт")]),
        h(
          "div",
          { class: "card__body" },
          h("p", { class: "state__say" }, "Движок записи не открыт."),
          h("p", { class: "state__why" }, "Открытие занимает около десяти секунд: сборка при первом разе, запуск, подгон под частоту бита и проверка уровня."),
          h("div", { class: "state__acts" }, open),
          noteLine(),
        ),
      );
      return;
    }
    if (phase === "out_of_sync") {
      const again = h("button", { class: "btn btn--solid", type: "button" }, "Перезапустить движок");
      again.addEventListener("click", actions.open);
      card.append(
        head(["движок ", h("b", {}, "не отвечает")]),
        h(
          "div",
          { class: "card__body" },
          h("p", { class: "state__say" }, "Движок не ответил вовремя."),
          h("p", { class: "state__why" }, "Пока он не перезапущен, старт и стоп не принимаются: страница не знает, что он сейчас делает. Уже записанные дубли на месте."),
          h("div", { class: "state__acts" }, again),
        ),
      );
      return;
    }
    if (phase === "stopping") {
      card.append(
        head(["движок ", h("b", {}, "останавливается")]),
        h(
          "div",
          { class: "card__body" },
          h("p", { class: "state__say busy" }, `Дописываю ${s.take ? takeLabel(s.take) : "дубль"}`),
          h("p", { class: "state__why" }, "Стоп принят. Файл закрывается, это до 15 секунд; кнопка записи вернётся сама."),
          h("p", { class: "rec__clock num", "data-dim": "true", "data-ref": "clock" }, clock(s.seconds)),
        ),
      );
      return;
    }
    if (phase === "failed" || phase === "opening") {
      const rows = stageRows(s, failedAt);
      const list = h("ol", { class: "stages", "aria-label": "Открытие движка" });
      const done = rows.filter((r) => r.state === "done").length;
      list.style.setProperty("--reach", String(rows.length > 1 ? Math.min(1, done / (rows.length - 1)) : 0));
      for (const row of rows) {
        list.append(
          h(
            "li",
            { class: "stage", "data-state": row.state },
            h("span", { class: "stage__jack", "aria-hidden": "true" }),
            h("span", { class: "stage__name" }, row.name),
            h("span", { class: "stage__val num" }, row.value),
          ),
        );
      }
      if (phase === "failed") {
        const again = h("button", { class: "btn btn--solid", type: "button" }, "Открыть снова");
        again.addEventListener("click", actions.open);
        card.append(
          head(["движок ", h("b", {}, "не открылся")]),
          h(
            "div",
            { class: "card__body" },
            list,
            h("p", { class: "state__why", style: "margin-top: var(--s4)" }, s.error ?? "Причина не названа."),
            h("div", { class: "state__acts" }, again),
          ),
        );
        return;
      }
      card.append(
        head(["движок ", h("b", {}, "открывается"), " · ~10 с"]),
        h("div", { class: "rec__core" }, h("div", { class: "rec__arm" }, list, h("p", { class: "arm__hint" }, "Писать можно, когда движок готов. Старт и стоп до этого не принимаются."))),
        facts(s, false),
      );
      return;
    }
    const live = phase === "live";
    const arm = h(
      "button",
      { class: "arm", type: "button", "aria-pressed": String(live), "aria-label": live ? "Стоп" : "Писать дубль" },
      h("span", { class: "arm__dot" }),
    );
    arm.addEventListener("click", actions.toggle);
    const meter = h(
      "div",
      { class: "inmeter", "data-ref": "meter", "data-live": "true", "data-clipped": "false", role: "meter", "aria-label": "Уровень микрофона", "aria-valuemin": "-60", "aria-valuemax": "0" },
      h("span", { class: "inmeter__clip", "aria-hidden": "true" }),
      h("span", { class: "inmeter__cliplabel" }, "клип"),
      h("div", { class: "inmeter__well", "aria-hidden": "true" }, h("span", { class: "inmeter__fill" }), h("span", { class: "inmeter__peak" })),
      h(
        "ol",
        { class: "inmeter__scale", "aria-hidden": "true" },
        ...[
          ["−6", 0.1],
          ["−12", 0.2],
          ["−24", 0.4],
          ["−48", 0.8],
        ].map(([label, at]) => h("li", { style: `--at: ${at}` }, String(label))),
      ),
      h("p", { class: "inmeter__read num", "data-ref": "read" }, "— дБ"),
    );
    const say = h(
      "div",
      { class: "rec__say" },
      h("p", { class: "arm__label", "data-ref": "label" }, live ? "Стоп " : "Писать дубль ", h("kbd", {}, "R")),
      h("p", { class: "rec__clock num", "data-ref": "clock", "data-dim": String(!live) }, clock(s.seconds)),
      h("p", { class: "rec__file" }, "файл ", h("span", { class: "num", "data-ref": "file" }, s.take ?? "—")),
    );
    const armBox = h(
      "div",
      { class: "rec__arm" },
      arm,
      say,
      h("p", { class: "arm__hint" }, live ? "Стоп сохраняет дубль; это может занять до 15 с." : "Старт и стоп: кнопка или клавиша R."),
    );
    const calibrate = h("button", { class: "btn btn--ghost", type: "button", disabled: live }, "Калибровка");
    calibrate.addEventListener("click", () => {
      calibrating = true;
      build(s, phase);
    });
    const foot = h("div", { class: "rec__foot" }, calibrate);
    if (live) {
      const drop = h("button", { class: "btn btn--ghost", type: "button" }, armDiscard ? "Точно сбросить?" : "Сбросить дубль");
      drop.addEventListener("click", () => {
        if (!armDiscard) {
          armDiscard = true;
          drop.textContent = "Точно сбросить?";
          discardTimer = setTimeout(() => {
            armDiscard = false;
            drop.textContent = "Сбросить дубль";
          }, 3000);
          return;
        }
        if (discardTimer) clearTimeout(discardTimer);
        armDiscard = false;
        actions.discard();
      });
      foot.append(drop);
    }
    foot.append(h("p", { class: "rec__warn" }, "Пиши в наушниках: из колонок бит попадёт в микрофон."));
    const extra = noteLine();
    if (extra) foot.append(extra);
    card.append(
      head([`движок `, h("b", { "data-ref": "engine" }, live ? "пишет" : "готов"), s.rate ? ` · ${s.rate / 1000} кГц`.replace(".", ",") : ""]),
      h("div", { class: "rec__core" }, armBox, meter),
      facts(s, true),
      foot,
    );
  }

  function facts(s: RecSnapshot, withClip: boolean): HTMLElement {
    const items: [string, string, string?][] = [
      ["Задержка монитора", s.monitor_ms === null ? "считаю" : `${decimal(s.monitor_ms, 1)} мс`, "monitor"],
      ["Сбои", String(s.dropouts), "drops"],
      ["Бит", beatLine],
    ];
    if (s.round_trip) items.push(["Задержка круга", `${s.round_trip.label}${s.round_trip.source === "estimate" ? " (оценка)" : ""}`]);
    if (withClip) items.push(["Клипов", "—", "clips"]);
    return h(
      "dl",
      { class: "rec__facts" },
      ...items.map(([name, value, ref]) => h("div", {}, h("dt", {}, name), h("dd", { class: "num", "data-ref": ref }, value))),
    );
  }

  const q = <T extends HTMLElement>(ref: string): T | null => card.querySelector<T>(`[data-ref="${ref}"]`);

  function paint(s: RecSnapshot, phase: RecPhase, now: number): void {
    const clockEl = q("clock");
    if (clockEl) clockEl.textContent = clock(s.seconds);
    if (phase !== "ready" && phase !== "live") return;
    const level = meterLevel(s.peak_db);
    const peak = hold.push(level, now);
    const meter = q("meter");
    if (meter) {
      meter.dataset["clipped"] = String(s.clip);
      meter.setAttribute("aria-valuenow", s.peak_db === null ? "-60" : String(Math.round(s.peak_db)));
      meter.style.setProperty("--lv", level.toFixed(3));
      meter.style.setProperty("--pk", peak.toFixed(3));
    }
    const read = q("read");
    if (read) read.textContent = dbText(s.peak_db);
    const file = q("file");
    if (file) file.textContent = s.take ?? "—";
    const monitor = q("monitor");
    if (monitor) monitor.textContent = s.monitor_ms === null ? "считаю" : `${decimal(s.monitor_ms, 1)} мс`;
    const drops = q("drops");
    if (drops) drops.textContent = String(s.dropouts);
    const engine = q("engine");
    if (engine) engine.textContent = s.age_ms > 1500 ? "не отвечает" : phase === "live" ? "пишет" : "готов";
  }

  return {
    el: card,
    note(text) {
      message = text;
      shape = "";
    },
    update(s, now) {
      const phase = recPhase(s);
      if (s && s.stage !== "failed" && s.stage !== "closed" && s.stage !== "ready" && s.stage !== "calibrating") failedAt = s.stage;
      const key = `${phase}|${calibrating}|${s?.stage ?? ""}|${s ? stageRows(s, failedAt).map((r) => r.state + r.value).join() : ""}|${s?.take ?? ""}|${message ?? ""}|${s?.round_trip?.label ?? ""}|${armDiscard}`;
      if (phase !== "live") hold.reset();
      if (key !== shape) {
        shape = key;
        build(s, phase);
      }
      if (s) paint(s, phase, now);
    },
  };
}
