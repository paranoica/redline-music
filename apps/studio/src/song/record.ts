// The recording engine's snapshot as a model: its phase, the opening stages, the meter.

import { takeLabel } from "../daw/words.ts";
import type { RecReply, RecSnapshot } from "./api.ts";

const decimal = (value: number, digits: number): string => value.toFixed(digits).replace(".", ",").replace("-", "−");

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

const OPEN_PHASES: readonly RecPhase[] = ["opening", "calibrating", "ready", "live", "stopping", "out_of_sync"];

// `busy`: the screen waits on the recorder (a take, a waiter, a take being watched)
export function recPollMs(phase: RecPhase, busy: boolean): number {
  return busy || OPEN_PHASES.includes(phase) ? 50 : 1000;
}

// What a toggle sent to start a take answered: null when the take started, else why it did not.
// A take whose stop timed out is collected by the next toggle, so that answer is no new take.
export function startRefusal(reply: RecReply): string | null {
  const v = reply.values ?? {};
  const take = typeof v["take"] === "string" ? ` (${takeLabel(`${v["take"]}.wav`)})` : "";
  switch (reply.code) {
    case "recording":
      return null;
    case "quantum_moved":
      return `Дубль не начался: буфер звуковой карты сменился (было ${String(v["was"])}, стало ${String(v["now"])} кадров). Закрой движок и открой снова.`;
    case "kept_late":
      return `Дубль не начался: движок дописал прошлый дубль${take}, он сохранён. Нажми запись ещё раз.`;
    case "finished_late_discarded":
      return `Дубль не начался: движок дописал прошлый дубль${take}, он сброшен. Нажми запись ещё раз.`;
    case "kept":
      return `Дубль не начался: движок закончил дубль, который уже шёл${take}, он сохранён.`;
    case "discarded":
      return `Дубль не начался: движок сбросил дубль, который уже шёл${take}.`;
    default:
      return `Дубль не начался: ответ движка ${reply.code}.`;
  }
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

export const CALIBRATION: Record<"cup" | "loopback" | "speakers", string> = {
  cup: "Поставь наушник чашкой к микрофону",
  loopback: "Соедини выход карты со входом кабелем",
  speakers: "Колонки: задержка выйдет неточной",
};
