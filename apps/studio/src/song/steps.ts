// What the stations share: the page's context, the missing-input card, job words, and the
// host that keeps one audio surface mounted at a time.

import type { Job, JobKind, SongApi, SongChange, SongReply } from "./api.ts";
import type { StationId } from "./rail.ts";
import type { Scope } from "../core/lifecycle.ts";
import type { Mount, Mounted } from "../core/mount.ts";
import { h } from "../ui/dom.ts";

export interface PageState {
  song: SongReply;
  jobs: Job[];
}

export interface Ctx {
  api: SongApi;
  token: string;
  scope: Scope;
  state(): PageState;
  // a change to the song: re-reads it on 412 and says what runs on 409
  change(set: SongChange): Promise<boolean>;
  reload(): Promise<void>;
  notice(text: string | null): void;
  go(id: StationId): void;
  start(kind: Exclude<JobKind, "beat_render">, seed?: number): Promise<void>;
  cancel(kind: JobKind): Promise<void>;
}

export interface StationView {
  el: HTMLElement;
  // the song or the jobs changed
  update(): void;
  dispose(): void;
  // the station is on screen (or not), for polls
  shown?(on: boolean): void;
}

export const JOB_NAMES: Record<JobKind, string> = {
  separate: "отделение голоса",
  rank: "оценка дублей",
  auto: "авто-сведение",
  result: "сборка итога",
  beat_render: "рендер бита",
};

export const JOB_STATE: Record<Job["state"], string> = {
  queued: "в очереди",
  running: "идёт",
  cancelling: "отменяется",
  done: "готово",
  failed: "не вышло",
  cancelled: "отменено",
};

export function jobActive(job: Job | undefined): boolean {
  return job !== undefined && (job.state === "queued" || job.state === "running" || job.state === "cancelling");
}

export function jobOf(jobs: readonly Job[], kind: JobKind): Job | undefined {
  return jobs.find((j) => j.kind === kind);
}

export function runningText(jobs: readonly Job[]): string {
  const live = jobs.filter(jobActive).map((j) => `${JOB_NAMES[j.kind]} · ${JOB_STATE[j.state]}`);
  return live.length ? live.join(", ") : "ничего не идёт";
}

export function copyButton(text: () => string, label = "Скопировать"): HTMLButtonElement {
  const button = h("button", { class: "btn btn--ghost cmd__copy", type: "button" }, label);
  let timer: ReturnType<typeof setTimeout> | undefined;
  button.addEventListener("click", () => {
    void navigator.clipboard?.writeText(text()).then(
      () => {
        button.textContent = "Скопировано";
        clearTimeout(timer);
        timer = setTimeout(() => (button.textContent = label), 1400);
      },
      () => {
        button.textContent = "Не удалось";
      },
    );
  });
  return button;
}

export function commandBox(command: string): HTMLElement {
  return h(
    "div",
    { class: "cmd" },
    h("p", { class: "cmd__code" }, h("span", { class: "cmd__prompt" }, "$ "), command),
    copyButton(() => command),
  );
}

export interface NeedOptions {
  title: string;
  what: string;
  how?: string;
  command?: string;
  then?: string;
  actions?: HTMLElement[];
  pending?: HTMLElement | null;
  after?: string;
}

export function needCard(o: NeedOptions): HTMLElement {
  return h(
    "section",
    { class: "card", "aria-label": o.title },
    h("header", { class: "card__head" }, h("h2", { class: "card__title" }, o.title)),
    h(
      "div",
      { class: "card__body" },
      h("p", { class: "need__what" }, o.what),
      o.how ? h("p", { class: "need__how" }, o.how) : null,
      o.command ? commandBox(o.command) : null,
      o.then ? h("p", { class: "need__then" }, o.then) : null,
      o.actions?.length ? h("div", { class: "state__acts", style: "margin-top: var(--s4)" }, ...o.actions) : null,
      o.pending ?? null,
    ),
    o.after ? h("p", { class: "need__after" }, o.after) : null,
  );
}

export function jobBar(ctx: Ctx, kind: Exclude<JobKind, "beat_render">, label: string, again?: string): HTMLElement {
  const job = jobOf(ctx.state().jobs, kind);
  const live = jobActive(job);
  const start = h("button", { class: "btn btn--ghost", type: "button", disabled: live }, live ? label : (again ?? label));
  start.addEventListener("click", () => void ctx.start(kind));
  const parts: (Node | string)[] = [start];
  if (live && job) {
    const stop = h("button", { class: "btn btn--ghost", type: "button", disabled: job.state === "cancelling" }, "Отмена");
    stop.addEventListener("click", () => void ctx.cancel(kind));
    parts.push(h("span", { class: "card__meta busy" }, JOB_STATE[job.state]), stop);
  } else if (job?.state === "failed") {
    parts.push(h("span", { class: "card__meta" }, `не вышло: ${job.reason ?? "причина не названа"}`));
  }
  return h("div", { class: "card__end" }, ...parts);
}

export const DESK_MIN = 1024;

export function desktopQuery(): MediaQueryList | null {
  return typeof matchMedia === "function" ? matchMedia(`(min-width: ${DESK_MIN}px)`) : null;
}

export function deskNote(what: string, tail: string): HTMLElement {
  return h("p", { class: "desk-note" }, h("b", {}, `${what} — на компьютере.`), ` Поверхности нужно от ${DESK_MIN} пикселей по ширине. ${tail}`);
}

// one audio surface at a time: mounting one disposes the other
export class SurfaceHost {
  #current: { kind: string; mounted: Mounted; root: HTMLElement } | null = null;
  #token = 0;
  #active = true;

  get kind(): string | null {
    return this.#current?.kind ?? null;
  }

  async show(kind: string, mount: Mount, slot: HTMLElement, base: string, token: string): Promise<void> {
    await this.dispose();
    const mine = ++this.#token;
    const root = h("div", { class: "surface" });
    slot.replaceChildren(root);
    const mounted = await mount(root, { base, token, active: this.#active });
    if (mine !== this.#token) {
      await mounted.dispose();
      root.remove();
      return;
    }
    this.#current = { kind, mounted, root };
  }

  load(kind: string, settings: Record<string, unknown>, label: string): void {
    if (this.#current?.kind === kind) this.#current.mounted.load?.(settings, label);
  }

  setActive(on: boolean): void {
    this.#active = on;
    this.#current?.mounted.setActive?.(on);
  }

  async dispose(): Promise<void> {
    this.#token += 1;
    const current = this.#current;
    this.#current = null;
    if (!current) return;
    await current.mounted.dispose();
    current.root.remove();
  }
}
