// The song page: a rail of six stations over the workspace of the one that is open.
// The mix and beat surfaces mount into it one at a time.

import { Scope } from "../core/lifecycle.ts";
import type { Mount } from "../core/mount.ts";
import { h } from "../ui/dom.ts";
import { acceptSong, ApiError, createSongApi, staleSong } from "./api.ts";
import type { Job, JobKind, Ranking, RecSnapshot, SongChange, SongReply, StartKind } from "./api.ts";
import { Poller } from "./poll.ts";
import { createRail, defaultStation, railItems, STATION_NAMES } from "./rail.ts";
import type { StationId } from "./rail.ts";
import { createRecStation, createBeatStation, createMixStation, createResultStation } from "./stations.ts";
import type { KeyedView } from "./stations.ts";
import { finishedKinds, JOB_NAMES, JOB_STATE, jobActive, needCard, refusalText, SurfaceHost } from "./steps.ts";
import type { Ctx, PageState } from "./steps.ts";
import { createLyric, createTextStation } from "./text.ts";
import { createTakesStation } from "./takes.ts";
import { beatSurface } from "../flip/model.ts";

const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
};

export const mount: Mount = async (root, options) => {
  const scope = new Scope();
  const api = createSongApi({ base: options.base, token: options.token });
  const parking = h("div", { class: "surface-parking", hidden: true });
  const surfaces = new SurfaceHost(parking);
  let song: SongReply | null = null;
  let jobs: Job[] = [];
  let ranking: Ranking | null = null;
  let rec: RecSnapshot | null = null;
  let current: StationId | null = null;
  let view: KeyedView | null = null;
  let noteTimer: ReturnType<typeof setTimeout> | undefined;
  let jobsSeq = 0;
  let songSeq = 0;
  let opening = 0;

  root.classList.add("song-app");
  const jobList = h("ul", { class: "sdeck__jobs", "aria-label": "Задачи" });
  const rev = h("span", { class: "sdeck__rev num" });
  const title = h("span", { class: "deck__track" }, "…");
  const deck = h(
    "header",
    { class: "deck sdeck" },
    h("div", { class: "deck__brand" }, h("span", { class: "brand" }, "Redline"), title),
    jobList,
    h("div", { class: "deck__actions" }, rev),
  );
  const notice = h("p", { class: "notice", role: "status", hidden: true });
  const stage = h("main", { class: "stage-view" });
  const rail = createRail((id) => void open(id));
  root.append(deck, rail.el, notice, stage, parking);

  const say = (text: string | null): void => {
    clearTimeout(noteTimer);
    notice.hidden = text === null;
    notice.textContent = text ?? "";
    if (text !== null) noteTimer = setTimeout(() => say(null), 9000);
  };
  scope.add(() => clearTimeout(noteTimer));

  const state = (): PageState => {
    if (!song) throw new Error("the song is not loaded");
    return { song, jobs };
  };

  const jobsPoller = new Poller({
    ms: 1000,
    tick: async () => {
      await refreshJobs();
    },
  });
  scope.add(() => jobsPoller.dispose());

  // a beat rendered inside the beat surface is a job the page did not start
  const beatPoller = new Poller({
    ms: 2000,
    tick: async () => {
      await refreshJobs();
    },
  });
  scope.add(() => beatPoller.dispose());

  // the rail keeps the engine's state while the recording station is not open
  const recPoller = new Poller({
    ms: 1000,
    tick: async () => {
      applySnapshot(await api.recState());
    },
  });
  scope.add(() => recPoller.dispose());

  function syncRecPoll(): void {
    recPoller.setWanted(current !== "rec" && rec !== null && rec.stage !== "closed");
  }

  function applySnapshot(snap: RecSnapshot | null): void {
    const changed = rec?.recording !== snap?.recording || rec?.stage !== snap?.stage || rec?.take !== snap?.take;
    const tick = Math.floor(rec?.seconds ?? -1) !== Math.floor(snap?.seconds ?? -1) && Boolean(snap?.recording);
    rec = snap;
    syncRecPoll();
    if (changed || tick) paint();
  }

  function paint(): void {
    if (!song) return;
    title.textContent = `песня ${song.song.name}`;
    rev.textContent = `ред. ${song.song.rev}`;
    jobList.replaceChildren(
      ...jobs
        .filter((j) => jobActive(j) || j.state === "failed")
        .map((j) => {
          const parts: (Node | string)[] = [h("b", {}, JOB_NAMES[j.kind]), ` · ${JOB_STATE[j.state]}${j.state === "failed" && j.reason ? `: ${j.reason}` : ""}`];
          if (jobActive(j) && j.state !== "cancelling") {
            const stop = h("button", { class: "link", type: "button" }, "отмена");
            stop.addEventListener("click", () => void ctx.cancel(j.kind));
            parts.push(" ", stop);
          }
          return h("li", {}, ...parts);
        }),
    );
    const items = railItems({ stations: song.stations, jobs, ranking, rec, current });
    rail.update(items);
  }

  async function refreshJobs(): Promise<void> {
    const mine = ++jobsSeq;
    const next = await api.jobs();
    if (mine !== jobsSeq) return;
    const before = jobs;
    jobs = next;
    jobsPoller.setWanted(jobs.some(jobActive));
    const finished = finishedKinds(before, next);
    if (finished.includes("rank")) void loadRanking();
    if (finished.length) reload().catch(() => {});
    paint();
    view?.update();
  }

  async function loadRanking(): Promise<void> {
    try {
      ranking = (await api.takes()).ranking;
      paint();
    } catch {
      // the table station asks again
    }
  }

  async function reload(): Promise<void> {
    const mine = ++songSeq;
    const next = await api.song();
    if (mine !== songSeq) return;
    song = acceptSong(song, next);
    paint();
    view?.update();
  }

  const ctx: Ctx = {
    api,
    scope,
    token: options.token,
    state,
    reload,
    notice: say,
    go: (id) => void open(id),
    async change(set: SongChange) {
      try {
        const next = await api.setSong(state().song.song.rev, set);
        songSeq += 1;
        song = acceptSong(song, next);
        say(null);
        paint();
        view?.update();
        return true;
      } catch (error) {
        if (error instanceof ApiError && error.status === 412) {
          const fresh = staleSong(error.body);
          if (fresh) {
            songSeq += 1;
            song = acceptSong(song, fresh);
          }
          else await reload().catch(() => {});
          paint();
          view?.update();
          say("Песня изменилась в другом месте: я перечитал её. Повтори действие.");
        } else if (error instanceof ApiError && error.status === 409) {
          if (error.code === "busy") await refreshJobs().catch(() => {});
          say(refusalText(error, jobs));
        } else {
          say(error instanceof Error ? error.message : "Не получилось.");
        }
        return false;
      }
    },
    async start(kind: StartKind, seed?: number) {
      try {
        await api.startJob(kind, seed);
        say(null);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          if (error.code === "busy") await refreshJobs().catch(() => {});
          say(refusalText(error, jobs));
        } else {
          say(error instanceof Error ? error.message : "Не получилось.");
        }
      }
      await refreshJobs().catch(() => {});
      jobsPoller.setWanted(jobs.some(jobActive));
    },
    async cancel(kind: JobKind) {
      try {
        await api.cancelJob(kind);
      } catch (error) {
        say(error instanceof Error ? error.message : "Не получилось.");
      }
      await refreshJobs().catch(() => {});
    },
  };

  function build(id: StationId): KeyedView {
    const s = state().song.stations;
    switch (id) {
      case "beat":
        return createBeatStation(ctx, surfaces);
      case "text":
        return createTextStation(ctx);
      case "rec": {
        if (s.voice.kind === "suno") {
          return plain(needCard({ title: "Запись", what: "Запись не нужна.", how: "Голос песни — трек из Suno, его не записывают. Дальше — отделение голоса на шаге «Дубли»." }));
        }
        if (s.beat.kind === "own" && !s.beat.rendered) {
          return plain(needCard({ title: "Запись", what: "Бит ещё не отрендерен.", how: "Писать нужно под готовый бит: отрендери его на шаге «Бит».", actions: [goto("К биту", "beat")] }));
        }
        return createRecStation(ctx, createLyric(ctx, "read"), {
          snapshot: applySnapshot,
        });
      }
      case "takes":
        return createTakesStation(ctx, createLyric(ctx, "ref"));
      case "mix":
        return createMixStation(ctx, surfaces);
      case "result":
        return createResultStation(ctx);
    }
  }

  function plain(el: HTMLElement): KeyedView {
    return { el: h("div", { class: "view view--one" }, el), update() {}, dispose() {} };
  }

  function goto(label: string, id: StationId): HTMLElement {
    const b = h("button", { class: "btn btn--solid", type: "button" }, label);
    b.addEventListener("click", () => void open(id));
    return b;
  }

  async function open(id: StationId, first = false): Promise<void> {
    if (!song) return;
    const mine = ++opening;
    const kind = id === "beat" ? beatSurface(song.stations.beat) : id === "mix" ? id : null;
    const lost = kind ? surfaces.unsavedFor(kind) : null;
    if (lost && !confirm(`Несохранённые правки в ${lost === "beat" ? "бите" : lost === "flip" ? "флипе" : "сведении"} пропадут. Перейти?`)) return;
    if (!first && current === "text" && id !== "text" && view?.dirty?.() && !confirm("Несохранённая правка текста пропадёт. Перейти?")) return;
    if (!first) {
      await reload().catch(() => {});
      if (mine !== opening || scope.closed) return;
    }
    view?.shown?.(false);
    view?.dispose();
    current = id;
    surfaces.setActive(kind !== null);
    view = build(id);
    stage.replaceChildren(view.el);
    view.shown?.(true);
    stage.setAttribute("aria-label", STATION_NAMES[id]);
    beatPoller.setWanted(id === "beat");
    syncRecPoll();
    paint();
  }

  scope.listen(root.ownerDocument, "keydown", (e: KeyboardEvent) => {
    if (e.defaultPrevented || typing(e.target)) return;
    if (current === "rec" && view?.key?.(e)) e.preventDefault();
  });
  scope.listen(root.ownerDocument, "visibilitychange", () => {
    const seen = root.ownerDocument.visibilityState !== "hidden";
    jobsPoller.setVisible(seen);
    beatPoller.setVisible(seen);
    recPoller.setVisible(seen);
  });

  try {
    [song, jobs] = await Promise.all([api.song(), api.jobs()]);
    void loadRanking();
    jobsPoller.setWanted(jobs.some(jobActive));
    paint();
    await open(defaultStation(railItems({ stations: song.stations, jobs, ranking, rec, current })), true);
  } catch (error) {
    stage.replaceChildren(needCard({ title: "Песня", what: "Песня не загрузилась.", how: error instanceof Error ? error.message : "Сервер не ответил. Обнови страницу." }));
  }

  return {
    setActive: (on) => {
      if (!on) surfaces.setActive(false);
      else surfaces.setActive(current === "beat" || current === "mix");
    },
    dispose: async () => {
      if (scope.closed) return;
      opening += 1;
      jobsPoller.dispose();
      view?.shown?.(false);
      view?.dispose();
      view = null;
      scope.dispose();
      api.close();
      await surfaces.dispose();
      root.classList.remove("song-app");
      if (!root.classList.length) root.removeAttribute("class");
      deck.remove();
      rail.el.remove();
      notice.remove();
      stage.remove();
      parking.remove();
    },
  };
};
