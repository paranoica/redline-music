// The song screen: a transport bar, track heads and lanes on a bar ruler with a red playhead,
// and the bottom editor under them, collapsed until a tab is opened.

import { createApi as createBeatApi, type Library } from "../beat/api.ts";
import { deleteSection, duplicateSection, moveSection, type Score, type SectionEdit } from "../beat/model.ts";
import { History } from "../core/history.ts";
import { KeySurface, Scope } from "../core/lifecycle.ts";
import type { Loop } from "../core/loop.ts";
import { createFlipApi, type FlipState } from "../flip/api.ts";
import { copyBlockTo, copyRefusal, deleteBlock, deleteRefusal, moveBlock, moveBlockTo, splitBlock, totalBars as flipBars, type BlockEdit, type Layout, type Refusal } from "../flip/model.ts";
import { acceptSong, ApiError, createSongApi } from "../song/api.ts";
import type { Job, LayoutReply, Ranking, RecSnapshot, SongApi, SongChange, SongReply, StartKind } from "../song/api.ts";
import { finishedKinds, JOB_NAMES, jobActive, refusalText } from "../song/jobs.ts";
import { Poller } from "../song/poll.ts";
import { CALIBRATION, meterLevel, PeakHold, recPhase, recPollMs, stageRows, startRefusal, type OpenStage } from "../song/record.ts";
import { TakePlayer } from "../song/takes.ts";
import { parseLyric, type LyricPart } from "../song/text.ts";
import { dismissable, h, icon } from "../ui/dom.ts";
import { Audition, loadZones } from "./audition.ts";
import { fileClips, flipClips, ownClips, splitAt, type Clip } from "./clips.ts";
import { isDrag, planDrag } from "./drag.ts";
import { drawHits, drawNotes, drawWave, sizedCanvas, type PeakData } from "./draw.ts";
import { BEAT_LANES, Editor } from "./editor.ts";
import { exportPlan, exportText, type ExportPhase } from "./export.ts";
import { FlipDraft, flipLayout, StaleDraft, withBlocks } from "./flipdraft.ts";
import { layerProblem } from "./roll.ts";
import { DEFAULT_LAYOUT, lineAt, placeLines, type LinePlace, type LyricLayout } from "./lyrics.ts";
import { onNewKey, Playback, SimplePlayer, StudioPlayer, TAKE_LEAD, type BackendMakers, type LaneId, type LaneState } from "./player.ts";
import { barBeat, barSeconds, clockText, grid, toBars, toSeconds, type Grid } from "./time.ts";
import { BLOCKER_TEXT, commitPlan, exportBlockers, IDLE, nextStep, recordBlockers, step, stopAction, stopSettled, takeEnded, type Effect, type Pending, type Transport, type TransportEvent } from "./transport.ts";
import { beatKindOf, capTakes, chipText, dawKey, editorOpenAfter, editorTabs, factsOf, keyedBlock, pickAfterUndo, playbackKey, rulerGesture, scoreMarks, TAB_NAMES, visibleLanes } from "./view.ts";
import type { BassMark, EditorTab, HitMark, LaneKind, ScreenFacts } from "./view.ts";
import { takeLabel } from "./words.ts";

export const BAR_PX = 60;
const FALLBACK_BPM = 120;
const FALLBACK_BARS = 64;
const DEFAULT_LOOP_BARS = 4;

const PATHS = {
  home: "M6 5h2v14H6zM9.5 12 19 5v14z",
  play: "M7 4.5v15L20 12z",
  pause: "M6 5h4v14H6zm8 0h4v14h-4z",
  stop: "M6 6h12v12H6z",
  rec: "M12 5a7 7 0 1 0 0 14a7 7 0 1 0 0-14z",
  loop: "M7 7h10v3l4-4-4-4v3H5v6h2zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2z",
  small: "M8 5.5v13L18 12z",
};

type Mode = "cup" | "loopback" | "speakers";
const MODE_NAMES: Record<Mode, string> = { cup: "чашка", loopback: "кабель", speakers: "колонки" };

export interface ScreenOptions {
  base?: string | undefined;
  token: string;
  toStart: () => void;
}

export interface Screen {
  dispose(): Promise<void>;
}

interface Snapshot {
  chosen: string | null;
  layout: LayoutReply;
}

const typing = (target: EventTarget | null): boolean => {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable;
};

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

const tbtn = (cls: string, title: string, path: string): HTMLButtonElement => {
  const b = h("button", { class: `tbtn ${cls}`.trim(), type: "button", title, "aria-label": title });
  b.append(icon(path, "tbtn__icon"));
  return b;
};

export async function mountScreen(root: HTMLElement, options: ScreenOptions): Promise<Screen> {
  const scope = new Scope();
  const api: SongApi = createSongApi({ base: options.base, token: options.token });
  const flipApi = createFlipApi({ base: api.flipBase, token: options.token });
  const beatApi = createBeatApi({ base: api.beatBase, token: options.token });
  const flipDraft = new FlipDraft({
    api: flipApi,
    takes: () => facts.takes,
    confirm: (text) => confirm(text),
    recording: () => transport.mode === "recording",
    reload: () => reload(),
    changed: () => {
      if (!ready || scope.closed) return;
      rebuildModel();
      render();
      bottom.flipEdited();
    },
    say: (text) => say(text),
  });
  const colors = getComputedStyle(root);
  const color = (name: string): string => colors.getPropertyValue(name).trim() || "#aaa";

  // ---------- state ----------
  let reply: SongReply;
  let jobs: Job[] = [];
  let ranking: Ranking | null = null;
  let rec: RecSnapshot | null = null;
  let recAt = 0;
  let failedAt: OpenStage | null = null;
  let flip: FlipState | null = null;
  // the block picked on the beat lane by a click, which the block bar belongs to
  let picked: number | null = null;
  // a click right after a drag is not a pick
  let suppressClick = false;
  let beatLaneEl: HTMLElement | null = null;
  let hoverBar: number | null = null;
  // the beat the song player was built over, to hold a swap while a flip plays
  let playingBeat = { file: null as string | null, renders: 0 };
  let score: Score | null = null;
  let beatLibraries: Library[] = [];
  let lyric: LyricPart[] = [];
  let layout: LayoutReply = { start_bar: null, bars_per_line: null };
  let facts: ScreenFacts;
  let g: Grid = grid(FALLBACK_BPM);
  let total = FALLBACK_BARS;
  let clips: Clip[] = [];
  let marks: { bass: BassMark[]; drums: HitMark[] } = { bass: [], drums: [] };
  let places: LinePlace[] = [];
  let beatPeaks: PeakData | null = null;
  const takePeaks = new Map<string, PeakData | "loading" | "failed">();
  let transport: Transport = IDLE;
  let loop: { fromBar: number; toBar: number } | null = null;
  let loopOn = false;
  let selected: LaneKind | null = null;
  let tab: EditorTab | null = null;
  let editorOpen = false;
  let allTakes = false;
  let calibrationOffered = false;
  let engineBusy = false;
  let exportPhase: ExportPhase = { kind: "idle" };
  let exportDone: HTMLElement | null = null;
  const lanes: Record<LaneId, LaneState> = { beat: { gain: 1, mute: false, solo: false }, voice: { gain: 1, mute: false, solo: false } };
  const hold = new PeakHold();
  let history: History<Snapshot> | null = null;
  let playback: Playback | null = null;
  let playbackAt = "";
  // the newest player's beat load, for a commit that waits for the new render to be heard
  let playbackLoad: Promise<void> = Promise.resolve();
  // a take the transport lost track of (a failed start or stop): the recorder is polled until it
  // holds none, then the takes are read again
  let watchTake = false;
  let ready = false;
  let jobsKey = "";
  let songSeq = 0;
  let jobsSeq = 0;
  const waiters: { want: (s: RecSnapshot) => boolean; done: (s: RecSnapshot | null) => void }[] = [];

  // ---------- frame ----------
  root.classList.add("daw");
  root.style.setProperty("--bar", `${BAR_PX}px`);

  const songName = h("div", { class: "brand__song" });
  const brand = h("button", { class: "brand", type: "button", title: "Мои песни" }, h("span", { class: "brand__mark", "aria-hidden": "true" }), songName);
  const home = tbtn("", "В начало (Home)", PATHS.home);
  const play = tbtn("tbtn--play", "Играть / пауза (пробел)", PATHS.play);
  const stopBtn = tbtn("", "Стоп", PATHS.stop);
  const recBtn = tbtn("tbtn--rec", "Запись с начала бита (R)", PATHS.rec);
  recBtn.setAttribute("aria-pressed", "false");
  const loopBtn = tbtn("tbtn--toggle", "Повтор куска (L)", PATHS.loop);
  loopBtn.setAttribute("aria-pressed", "false");
  const time = h("span", { class: "clock__time" }, "0:00.0");
  const barPos = h("span", { class: "clock__bar" }, "такт 1.1");
  const bpmText = h("span", { class: "clock__bpm" }, "— BPM");
  const strip = h("div", { class: "strip", role: "status", hidden: true });
  const transportBox = h("div", { class: "transport" }, home, play, stopBtn, recBtn, loopBtn, h("div", { class: "clock" }, time, barPos, bpmText), strip);
  const chip = h("button", { class: "nextchip", type: "button" });
  const meterFill = h("i", {});
  const meter = h("div", { class: "meter", title: "Общий уровень", role: "meter", "aria-label": "Общий уровень" }, meterFill);
  const moreBtn = h("button", { class: "btn btn--ghost more", type: "button", title: "Ещё", "aria-haspopup": "menu", "aria-expanded": "false" }, "⋯");
  const exportBtn = h("button", { class: "btn btn--main", type: "button" }, "Экспорт");
  const top = h("header", { class: "top" }, brand, transportBox, h("div", { class: "top__right" }, chip, meter, moreBtn, exportBtn));

  const headList = h("div", { class: "heads__list" });
  const heads = h("div", { class: "heads" }, h("div", { class: "ruler-pad" }, h("span", {}, "Дорожки")), headList);
  const ruler = h("div", { class: "ruler" });
  const loopMark = h("div", { class: "loop off", "aria-hidden": "true" });
  const laneBox = h("div", { class: "lanes" });
  const playhead = h("div", { class: "playhead", "aria-hidden": "true" });
  const inner = h("div", { class: "tl-inner" }, ruler, laneBox, playhead);
  const timeline = h("div", { class: "timeline" }, inner);
  const arr = h("main", { class: "arr" }, heads, timeline);

  const loading = h("div", { class: "loading", role: "status" }, "загружаю песню…");
  arr.append(loading);

  const bottom: Editor = new Editor({
    base: options.base,
    token: options.token,
    api,
    beatApi,
    flipApi,
    flipDraft,
    confirm: (text) => confirm(text),
    reply: () => reply,
    facts: () => facts,
    jobs: () => jobs,
    flip: () => flip,
    clips: () => clips,
    playheadBar: () => toBars(g, position()),
    playback: () => playback,
    studio: () => {
      const p = playback?.current;
      return p instanceof StudioPlayer && p.ready ? p : null;
    },
    stopSong: () => {
      takePlayer.stop();
      if (transport.mode === "playing") dispatch({ type: "pause" });
    },
    startJob: (kind) => startJob(kind),
    change: (set) => change(set),
    reload: () => reload(),
    beatRendered: async () => {
      await reload();
      await playbackLoad;
    },
    layout: () => lyricLayout() ?? DEFAULT_LAYOUT,
    nudge: (to) => nudge(to),
    scoreChanged: (next) => {
      score = next;
      rebuildModel();
      render();
    },
    beatState: () => {
      if (!ready) return;
      rebuildModel();
      render();
    },
    repaint: () => paintEditor(),
    say: (text) => say(text),
    overlay: root,
  });

  const what = h("span", { class: "what" });
  const tabBox = h("div", { class: "etabs__tabs", role: "tablist" });
  const collapse = h("button", { class: "add", type: "button" }, "развернуть ▴");
  const panes = h("div", { class: "ebody" });
  const editor = h("section", { class: "editor collapsed", "aria-label": "Редактор" }, h("div", { class: "etabs" }, what, tabBox, bottom.audition, h("span", { class: "spacer" }), collapse), panes);
  const hint = h("div", { class: "hint", role: "status" });
  const app = h("div", { class: "app" }, top, arr, editor);
  root.append(app, hint);

  let hintTimer = 0;
  const say = (text: string): void => {
    hint.textContent = text;
    hint.classList.add("show");
    scope.clearTimeout(hintTimer);
    hintTimer = scope.setTimeout(() => hint.classList.remove("show"), 4000);
  };

  // ---------- model ----------
  function rebuildModel(): void {
    facts = factsOf(reply, jobs, { flipAnalysed: flip ? flip.analysis !== null : null });
    if (facts.beatKind === "own" && (bottom.beatDirty || bottom.rendering)) facts = { ...facts, beatDirty: true };
    if (facts.beatKind === "flip") facts = { ...facts, beatDirty: flipDraft.wantsCommit || flipDraft.rendering };
    const analysis = flip?.analysis ?? null;
    if (facts.beatKind === "own" && score) {
      g = grid(score.bpm);
      clips = ownClips(score);
      total = Math.max(1, clips.reduce((n, c) => n + c.bars, 0));
      marks = scoreMarks(score);
    } else if (facts.beatKind === "flip" && (flipDraft.doc ?? flip?.draft)) {
      const doc = (flipDraft.doc ?? flip?.draft) as NonNullable<FlipState["draft"]>;
      // until the first render the beat plays the source itself, whose first bar starts at its downbeat
      g = grid(doc.grid.bpm, beatKindOf(reply.stations.beat) === "file" ? doc.grid.downbeat_s : 0);
      clips = flipClips(doc);
      total = Math.max(1, flipBars(doc.arrangement));
      marks = { bass: [], drums: [] };
    } else {
      g = grid(analysis?.bpm ?? facts.bpm ?? FALLBACK_BPM, facts.beatKind === "file" ? (analysis?.downbeat_s ?? 0) : 0);
      const duration = beatPeaks?.duration ?? analysis?.duration_s ?? 0;
      clips = fileClips(g, duration, facts.beatKind === "flip" ? "флип" : "бит");
      total = duration > 0 ? Math.max(1, Math.ceil(toBars(g, duration) - 1e-6)) : FALLBACK_BARS;
      marks = { bass: [], drums: [] };
    }
    if (picked !== null && (facts.beatKind === "file" || picked >= clips.length)) picked = null;
    const sections = lyric.map((p) => ({ kind: p.part ?? "", lines: p.lines.length }));
    places = placeLines(sections, total, lyricLayout());
  }

  function lyricLayout(): LyricLayout | null {
    const { start_bar, bars_per_line } = layout;
    return start_bar !== null && (bars_per_line === 1 || bars_per_line === 2) ? { startBar: start_bar, barsPerLine: bars_per_line } : null;
  }

  function lineText(p: LinePlace): string {
    return lyric[p.section]?.lines[p.line] ?? "";
  }

  // ---------- playback ----------
  function syncPlayback(): void {
    const key = playbackKey(facts, reply.stations.beat.file, bottom.renders);
    if (key === playbackAt && playback) return;
    // a flip that plays goes on over the beat it started with; the new render is heard from the next play
    if (playback && facts.beatKind === "flip" && transport.mode === "playing" && playbackKey(facts, playingBeat.file, playingBeat.renders) === playbackAt) return;
    playbackAt = key;
    playingBeat = { file: reply.stations.beat.file, renders: bottom.renders };
    const old = playback;
    const chosen = facts.chosen;
    const studioReady = facts.studioReady;
    const make: BackendMakers = {
      simple: () => new SimplePlayer({ base: options.base, token: options.token }, chosen),
      studio: () => new StudioPlayer({ base: options.base, token: options.token, onError: say }),
      ready: async () => studioReady && (await api.mixReady()) === "ready",
    };
    // a new render, the studio built or another take while the song plays: it goes on from there
    if (old && onNewKey({ had: true, playing: transport.mode === "playing", beatAudio: facts.hasBeatAudio }) === "renew") {
      playbackLoad = old.renew(make).catch((error: unknown) => say(`Звук не загрузился: ${message(error)}`));
      return;
    }
    const next = new Playback({
      ...make,
      audition: new Audition({ onError: say, prepare: (client) => loadZones(client, beatLibraries, (library, file) => beatApi.zone(library, file)) }),
    });
    playback = next;
    for (const lane of ["beat", "voice"] as const) next.setLane(lane, lanes[lane]);
    next.setLoop(currentLoop());
    if (old) {
      if (transport.mode === "playing") dispatch({ type: "stop" });
      // the old player's audition goes with it
      bottom.auditionOff();
      void old.dispose();
    }
    playbackLoad = facts.hasBeatAudio ? next.sync().catch((error: unknown) => say(`Звук не загрузился: ${message(error)}`)) : Promise.resolve();
  }

  function currentLoop(): Loop | null {
    if (!loopOn || !loop) return null;
    return { start: Math.max(0, toSeconds(g, loop.fromBar)), end: toSeconds(g, loop.toBar) };
  }

  const takePlayer = new TakePlayer(
    (name) => api.takeAudio(name, true),
    () => {
      if (!scope.closed) paintTakeButtons();
    },
  );
  scope.add(() => takePlayer.stop());

  // ---------- transport ----------
  function needsRender(press: Pending): boolean {
    return commitPlan(facts, press).wait;
  }

  function dispatch(e: TransportEvent): void {
    const was = transport;
    const next = step(transport, e);
    transport = next.state;
    if (was.mode === "recording" && transport.mode !== "recording") flushWaiters();
    if (transport.mode === "idle" && transport.error && was !== transport) say(transport.error);
    for (const effect of next.effects) run(effect);
    // a render that landed while the flip played is taken in now
    if (was.mode === "playing" && transport.mode !== "playing") syncPlayback();
    syncRecPoll();
    paintTop();
    paintModal();
  }

  function run(effect: Effect): void {
    switch (effect) {
      case "play":
        takePlayer.stop();
        bottom.auditionOff();
        // loading the sound can take a while; the end-of-song check waits for it
        playStarted = Infinity;
        void playback
          ?.play()
          .then(() => (playStarted = performance.now()))
          .catch((error: unknown) => {
            playStarted = 0;
            dispatch({ type: "error", message: `Звук не пошёл: ${message(error)}` });
          });
        return;
      case "pause":
        playback?.pause();
        return;
      // the playback's stop also ends a block audition
      case "stop":
        bottom.auditionOff();
        playback?.stop();
        return;
      case "commit":
        void commitBeat();
        return;
      case "open-engine":
        void openEngine();
        return;
      case "start-record":
        void startRecord();
        return;
      case "stop-record":
        void stopRecord();
        return;
      case "watch-take":
        watchTake = true;
        return;
      case "export":
        void runExport();
        return;
    }
  }

  function pressPlay(): void {
    if (transport.mode === "playing") dispatch({ type: "pause" });
    else if (transport.mode === "rendering" && transport.then === "play") dispatch({ type: "pause" });
    else if (transport.mode === "idle" || transport.mode === "rendering") {
      const plan = commitPlan(facts, "play");
      if (!facts.hasBeatAudio && !plan.wait) {
        say("Бит ещё не готов.");
        return;
      }
      dispatch({ type: "play", render: plan.wait });
      if (plan.background) commitInBackground();
    }
  }

  function pressRecord(): void {
    if (facts.voiceKind === "suno") return;
    if (transport.mode === "recording") {
      dispatch({ type: "stop" });
      return;
    }
    const blocked = recordBlockers({ voiceKind: facts.voiceKind, engineBusy }, transport);
    if (blocked.length) {
      say(BLOCKER_TEXT[blocked[0] as keyof typeof BLOCKER_TEXT]);
      return;
    }
    if (rec?.stopping) {
      say("Движок дописывает дубль, подожди.");
      return;
    }
    takePlayer.stop();
    dispatch({ type: "record", render: needsRender("record"), held: rec?.recording === true });
  }

  function seekTo(seconds: number): void {
    if (transport.mode === "recording") return;
    takePlayer.stop();
    playback?.seek(Math.max(0, seconds));
    paintFrame();
  }

  function toggleLoop(): void {
    if (!loopOn && !loop) {
      const at = Math.max(0, Math.floor(toBars(g, playback?.position() ?? 0)));
      const from = Math.min(at, Math.max(0, total - DEFAULT_LOOP_BARS));
      loop = { fromBar: from, toBar: Math.min(total, from + DEFAULT_LOOP_BARS) };
    }
    loopOn = !loopOn;
    playback?.setLoop(currentLoop());
    paintLoop();
  }

  async function commitBeat(): Promise<void> {
    try {
      await (facts.beatKind === "flip" ? flipDraft.commit() : bottom.commitBeat());
      if (scope.closed) return;
      dispatch({ type: "renderDone" });
    } catch (error) {
      dispatch({ type: "error", message: error instanceof StaleDraft ? error.message : `Бит не собрался: ${message(error)}` });
    }
  }

  // the flip renders while it plays or while the lanes are left; a failure is said, nothing waits
  function commitInBackground(): void {
    flipDraft.commit().then(
      () => {},
      (error: unknown) => {
        if (!scope.closed && !(error instanceof StaleDraft)) say(`Бит не собрался: ${message(error)}`);
      },
    );
  }

  // ---------- export ----------
  // the own beat's commit goes through the transport ("commit", then the "export" effect), so the
  // steps run here are read from the facts as they are after it
  function pressExport(): void {
    if (exportBusy()) return;
    closeExportDone();
    dispatch({ type: "export", render: exportPlan(facts)[0] === "commit" });
  }

  function exportBusy(): boolean {
    return exportPhase.kind === "running" || (transport.mode === "rendering" && transport.then === "export");
  }

  async function runExport(): Promise<void> {
    if (exportPhase.kind === "running") return;
    try {
      for (const s of exportPlan(facts)) {
        if (s === "commit") continue;
        exportPhase = { kind: "running", step: s };
        paintExport();
        await runJob(s);
        await reload();
      }
      exportPhase = { kind: "done", folder: reply.stations.result.folder };
    } catch (error) {
      if (scope.closed) return;
      exportPhase = { kind: "failed", message: message(error) };
      say(`Экспорт не прошёл: ${message(error)}`);
    }
    if (!scope.closed) paintExport();
  }

  // starts the job, or follows one of the same kind already running, until it ends
  async function runJob(kind: "mix" | "result"): Promise<void> {
    try {
      await api.startJob(kind);
    } catch (error) {
      await refreshJobs().catch(() => {});
      const same = error instanceof ApiError && error.code === "busy" && jobs.some((j) => j.kind === kind && jobActive(j));
      if (!same) throw new Error(error instanceof ApiError ? refusalText(error, jobs) : message(error));
    }
    for (;;) {
      await refreshJobs();
      const job = jobs.find((j) => j.kind === kind);
      if (job && !jobActive(job)) {
        if (job.state === "done") return;
        throw new Error(job.state === "cancelled" ? `${JOB_NAMES[kind]} отменено` : `${JOB_NAMES[kind]}: ${job.reason ?? "не вышло"}`);
      }
      await scope.sleep(1000);
      if (scope.closed) throw new Error("страница закрыта");
    }
  }

  function paintExport(): void {
    const blockers = exportBlockers(facts);
    const committing = transport.mode === "rendering" && transport.then === "export";
    const running = exportPhase.kind === "running" || committing || facts.exporting;
    exportBtn.textContent = committing ? exportText({ kind: "running", step: "commit" }) : exportPhase.kind === "idle" && facts.exporting ? exportText({ kind: "running", step: "result" }) : exportText(exportPhase);
    exportBtn.disabled = blockers.length > 0 || running;
    exportBtn.setAttribute("aria-busy", String(running));
    exportBtn.title = blockers.length ? BLOCKER_TEXT[blockers[0] as keyof typeof BLOCKER_TEXT] : running ? "Идёт экспорт" : facts.mixStale ? "Сведение устарело: экспорт сначала сведёт" : "Собрать итог";
    if (exportPhase.kind === "done" && !exportDone) showExportDone(exportPhase.folder);
  }

  function showExportDone(folder: string | null): void {
    const close = h("button", { class: "btn btn--ghost", type: "button", title: "Закрыть", "aria-label": "Закрыть" }, "×");
    close.addEventListener("click", closeExportDone);
    const parts: (Node | string)[] = [h("b", {}, "Готово")];
    if (folder) {
      const copy = h("button", { class: "btn btn--ghost", type: "button" }, "Скопировать путь");
      copy.addEventListener("click", () => {
        void navigator.clipboard?.writeText(folder).then(
          () => (copy.textContent = "Скопировано"),
          () => (copy.textContent = "Не удалось"),
        );
      });
      parts.push(h("span", { class: "export-done__path" }, folder), copy);
    } else parts.push(h("span", { class: "export-done__path" }, "итог собран, папку сервер не назвал"));
    exportDone = h("div", { class: "export-done", role: "status" }, ...parts, close);
    top.append(exportDone);
  }

  function closeExportDone(): void {
    exportDone?.remove();
    exportDone = null;
    if (exportPhase.kind !== "running") exportPhase = { kind: "idle" };
    paintExport();
  }

  // ---------- recording ----------
  const recPoller = new Poller({ ms: 1000, tick: async () => applySnapshot(await api.recState()) });
  scope.add(() => recPoller.dispose());

  function syncRecPoll(): void {
    recPoller.setMs(recPollMs(recPhase(rec), transport.mode === "recording" || waiters.length > 0 || watchTake));
    recPoller.setWanted(ready);
  }

  function applySnapshot(s: RecSnapshot | null): void {
    const was = rec;
    rec = s;
    recAt = performance.now();
    if (s && s.stage !== "failed" && s.stage !== "closed" && s.stage !== "ready" && s.stage !== "calibrating") failedAt = s.stage;
    for (const w of [...waiters]) {
      if (s && w.want(s)) {
        waiters.splice(waiters.indexOf(w), 1);
        w.done(s);
      }
    }
    if (watchTake && s && !s.recording && !s.stopping) {
      watchTake = false;
      void reload()
        .then(() => refreshJobs())
        .catch(() => {});
    }
    if (takeEnded(transport, s)) {
      dispatch({ type: "recordStopped" });
      void reload()
        .then(() => refreshJobs())
        .catch(() => {});
    }
    syncRecPoll();
    paintStrip();
    if (was?.take !== s?.take || was?.recording !== s?.recording) render();
  }

  function waitRec(want: (s: RecSnapshot) => boolean): Promise<RecSnapshot | null> {
    if (rec && want(rec) && performance.now() - recAt < 200) return Promise.resolve(rec);
    return new Promise((done) => {
      waiters.push({ want, done });
      syncRecPoll();
    });
  }

  function flushWaiters(): void {
    for (const w of waiters.splice(0)) w.done(null);
  }

  function recRefusal(error: unknown): string {
    if (error instanceof ApiError) {
      if (error.code === "engine_busy") engineBusy = true;
      return refusalText(error, jobs);
    }
    return message(error);
  }

  const settled = (s: RecSnapshot): boolean => s.stage === "ready" || s.stage === "failed" || s.out_of_sync;

  async function openEngine(): Promise<void> {
    try {
      let s: RecSnapshot | null = await api.recState();
      applySnapshot(s);
      if (recPhase(s) !== "ready") {
        await api.recOpen();
        s = await waitRec(settled);
      }
      if (!s || transport.mode !== "recording") return;
      if (s.stage !== "ready") {
        dispatch({ type: "error", message: `Движок не открылся: ${s.error ?? "без причины"}` });
        return;
      }
      engineBusy = false;
      const calibrate = !calibrationOffered && s.round_trip?.source !== "calibrated";
      calibrationOffered = calibrationOffered || calibrate;
      dispatch({ type: "opened", calibrate });
    } catch (error) {
      dispatch({ type: "error", message: recRefusal(error) });
    }
  }

  // the start's toggle while it is out: a stop pressed meanwhile waits for it
  let starting: Promise<boolean> | null = null;

  async function startRecord(): Promise<void> {
    const toggle = api.recToggle();
    starting = toggle.then(
      (r) => startRefusal(r) === null,
      () => false,
    );
    try {
      // anything but a started take (a late take collected, a moved quantum) is no take: the
      // screen must not wait for one, nor send a second toggle that would start one unseen
      const refused = startRefusal(await toggle);
      if (refused !== null) {
        dispatch({ type: "error", message: refused });
        return;
      }
      const s = await waitRec((x) => x.recording || x.stage === "failed" || x.out_of_sync);
      if (!s || transport.mode !== "recording") return;
      if (!s.recording) {
        dispatch({ type: "error", message: s.error ?? "Запись не началась." });
        return;
      }
      dispatch({ type: "recordStarted" });
    } catch (error) {
      dispatch({ type: "error", message: recRefusal(error) });
    } finally {
      starting = null;
    }
  }

  async function stopRecord(): Promise<void> {
    try {
      if (starting) await starting;
      // the take may have been stopped from another tab: a toggle with no take would start one, and
      // the take's name makes the engine refuse a stop that another tab's stop got to first
      const s = await api.recState();
      applySnapshot(s);
      const refused = stopAction(s) === "toggle" && s.take !== null && (await api.recToggle(s.take)).code === "not_running";
      await waitRec((x) => stopSettled(s.take, refused, x) || x.out_of_sync);
      dispatch({ type: "recordStopped" });
      await reload();
      await refreshJobs();
    } catch (error) {
      dispatch({ type: "error", message: recRefusal(error) });
    }
  }

  // the calibration as its own action, from the menu or the record flow
  async function calibrate(mode: Mode): Promise<boolean> {
    try {
      let s: RecSnapshot | null = await api.recState();
      applySnapshot(s);
      if (recPhase(s) === "closed" || recPhase(s) === "failed") {
        await api.recOpen();
        s = await waitRec(settled);
        if (!s || s.stage !== "ready") throw new Error(s?.error ?? "движок не открылся");
      }
      say("Калибровка: идёт свип.");
      const done = await api.recCalibrate(mode);
      const ms = done.values?.["ms"];
      await waitRec(settled);
      say(typeof ms === "number" ? `Задержка: ${ms.toFixed(1).replace(".", ",")} мс.` : "Калибровка готова.");
      return true;
    } catch (error) {
      say(`Калибровка не прошла: ${recRefusal(error)}`);
      return false;
    }
  }

  // ---------- data ----------
  async function reload(): Promise<void> {
    const mine = ++songSeq;
    const next = await api.song();
    if (mine !== songSeq || scope.closed) return;
    reply = acceptSong(reply, next);
    await refreshSide();
    rebuildModel();
    syncPlayback();
    render();
  }

  async function refreshSide(): Promise<void> {
    const kind = factsOf(reply, jobs, { flipAnalysed: null }).beatKind;
    const tasks: Promise<unknown>[] = [];
    if (kind === "own") {
      tasks.push(
        beatApi.session().then(
          (s) => {
            score = bottom.score ?? s.score;
            beatLibraries = s.libraries;
          },
          () => (score = bottom.score),
        ),
      );
      flip = null;
    } else {
      score = null;
      const dirty = factsOf(reply, jobs, { flipAnalysed: null }).beatDirty;
      tasks.push(
        flipApi.state().then(
          (s) => {
            flip = s;
            flipDraft.adopt(s, dirty);
          },
          () => (flip = null),
        ),
      );
    }
    tasks.push(api.peaks("beat", undefined, 4000).then((p) => (beatPeaks = { duration: p.duration_s, peaks: p.peaks }), () => (beatPeaks = null)));
    await Promise.all(tasks);
  }

  async function loadText(): Promise<void> {
    const t = reply.stations.text;
    if (t.seed === null || typeof t.version !== "number") {
      lyric = [];
      return;
    }
    try {
      lyric = parseLyric((await api.text(t.version)).text);
    } catch {
      lyric = [];
    }
  }

  async function loadRanking(): Promise<void> {
    try {
      ranking = (await api.takes()).ranking;
    } catch {
      ranking = null;
    }
  }

  const jobsPoller = new Poller({ ms: 1000, tick: () => refreshJobs() });
  scope.add(() => jobsPoller.dispose());

  async function refreshJobs(): Promise<void> {
    const mine = ++jobsSeq;
    const next = await api.jobs();
    if (mine !== jobsSeq || scope.closed) return;
    const done = finishedKinds(jobs, next);
    jobs = next;
    jobsPoller.setWanted(jobs.some(jobActive));
    if (done.includes("rank")) await loadRanking();
    if (done.length) bottom.jobsDone(done, jobs);
    const key = jobs.filter(jobActive).map((j) => `${j.kind}:${j.state}:${j.step ?? ""}`).join();
    if (done.length) await reload().catch(() => {});
    else {
      rebuildModel();
      if (key !== jobsKey) render();
      else paintTop();
    }
    jobsKey = key;
  }

  async function startJob(kind: StartKind): Promise<void> {
    try {
      await api.startJob(kind);
    } catch (error) {
      say(error instanceof ApiError ? refusalText(error, jobs) : message(error));
    }
    await refreshJobs().catch(() => {});
  }

  async function change(set: SongChange): Promise<boolean> {
    try {
      const next = await api.setSong(reply.song.rev, set);
      songSeq += 1;
      reply = acceptSong(reply, next);
      rebuildModel();
      syncPlayback();
      render();
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        await reload().catch(() => {});
        say("Песня изменилась в другом месте: я перечитал её. Повтори действие.");
      } else say(error instanceof ApiError ? refusalText(error, jobs) : message(error));
      return false;
    }
  }

  // ---------- history of song-level edits ----------
  const snapshot = (): Snapshot => ({ chosen: facts.chosen, layout: { ...layout } });

  async function apply(s: Snapshot): Promise<boolean> {
    if (s.chosen !== facts.chosen) {
      if (s.chosen === null) {
        say("Выбор дубля нельзя снять, только поменять.");
        return false;
      }
      if (!(await change({ voice_chosen: s.chosen }))) return false;
      if (!facts.studioReady && !jobs.some((j) => (j.kind === "separate" || j.kind === "mix") && jobActive(j))) void startJob("separate");
    }
    if (JSON.stringify(s.layout) !== JSON.stringify(layout)) {
      try {
        layout = await api.setLayout(s.layout);
      } catch (error) {
        say(`Раскладка строк не сохранилась: ${message(error)}`);
        return false;
      }
      rebuildModel();
      render();
    }
    return true;
  }

  async function edit(next: Snapshot): Promise<void> {
    if (await apply(next)) history?.push(snapshot());
  }

  async function undo(redo: boolean): Promise<void> {
    if (facts.beatKind === "flip" && (redo ? flipDraft.canRedo : flipDraft.canUndo)) {
      if (flipDraft.undo(redo)) picked = pickAfterUndo(picked, bottom.block()?.section ?? null);
      return;
    }
    if (bottom.undo(redo)) return;
    if (!history) return;
    const to = redo ? history.redo() : history.undo();
    if (!to) return;
    if (!(await apply(to))) {
      // the server refused: the history goes back to where the song is
      if (redo) history.undo();
      else history.redo();
    }
  }

  function nudge(to: Partial<LyricLayout>): void {
    const base = lyricLayout() ?? DEFAULT_LAYOUT;
    const next = { startBar: Math.max(0, to.startBar ?? base.startBar), barsPerLine: to.barsPerLine ?? base.barsPerLine };
    void edit({ chosen: facts.chosen, layout: { start_bar: next.startBar, bars_per_line: next.barsPerLine } });
  }

  // ---------- painting: the top bar ----------
  function paintTop(): void {
    songName.replaceChildren(reply.song.name, h("small", {}, "все песни"));
    const playingNow = transport.mode === "playing" || (transport.mode === "rendering" && transport.then === "play");
    play.replaceChildren(icon(playingNow ? PATHS.pause : PATHS.play, "tbtn__icon"));
    play.setAttribute("aria-label", playingNow ? "Пауза" : "Играть");
    const suno = facts.voiceKind === "suno";
    recBtn.hidden = suno;
    const recording = transport.mode === "recording";
    recBtn.setAttribute("aria-pressed", String(recording));
    const blocked = recording ? [] : recordBlockers({ voiceKind: facts.voiceKind, engineBusy }, transport);
    recBtn.disabled = blocked.length > 0;
    recBtn.title = blocked.length ? BLOCKER_TEXT[blocked[0] as keyof typeof BLOCKER_TEXT] : recording ? "Стоп записи (R)" : "Запись с начала бита (R)";
    root.classList.toggle("recording", transport.mode === "recording" && transport.step === "recording");
    bpmText.textContent = `${Math.round(g.bpm * 10) / 10} BPM`.replace(".", ",");
    const next = nextStep(facts);
    chip.textContent = chipText(next);
    chip.disabled = next === "done" || next === "pick" || next === "export";
    chip.hidden = suno && next === "record";
    paintExport();
    paintStrip();
  }

  function paintStrip(): void {
    const parts: (Node | string)[] = [];
    const phase = recPhase(rec);
    if (transport.mode === "rendering") parts.push(h("span", { class: "strip__busy" }, "Бит собирается…"));
    else if (transport.mode === "recording" && (transport.step === "opening" || transport.step === "calibrate") && rec && phase !== "ready") {
      const rows = stageRows(rec, failedAt);
      const now = rows.find((r) => r.state === "now" || r.state === "fail");
      parts.push(h("span", { class: "strip__dots", "aria-hidden": "true" }, ...rows.map((r) => h("i", { "data-state": r.state }))), h("span", {}, now ? now.name : "Движок открывается"));
    } else if (transport.mode === "recording" && transport.step === "opening") parts.push(h("span", { class: "strip__busy" }, "Открываю движок…"));
    else if (transport.mode === "recording" && (transport.step === "counting" || transport.step === "recording")) {
      parts.push(h("span", { class: "strip__rec" }, "● запись"), h("span", {}, `с начала бита · ${rec?.take ? takeLabel(rec.take) : "дубль"}`));
    } else if (transport.mode === "recording" && transport.step === "stopping") parts.push(h("span", { class: "strip__busy" }, "Дописываю дубль…"));
    else if (phase === "calibrating") parts.push(h("span", { class: "strip__busy" }, "Калибровка…"));
    strip.hidden = parts.length === 0;
    strip.replaceChildren(...parts);
  }

  // ---------- painting: tracks ----------
  function head(kind: LaneKind | "take" | "more", name: string, sub: string | Node | null, colorVar: string, ctl: Node[], extra: Partial<Record<string, string>> = {}): HTMLElement {
    const el = h(
      "div",
      { class: `head${kind === "take" || kind === "more" ? " head--take head--sub" : ""}`, "data-kind": kind, ...extra },
      h("span", { class: "head__color", "aria-hidden": "true" }),
      h("div", { class: "head__name" }, name, sub === null ? null : h("small", {}, sub)),
      h("div", { class: "head__ctl" }, ...ctl),
    );
    el.style.setProperty("--c", `var(${colorVar})`);
    if (kind !== "take" && kind !== "more") {
      el.setAttribute("aria-selected", String(selected === kind));
      el.addEventListener("click", () => select(kind));
    }
    return el;
  }

  function laneControls(lane: LaneId): Node[] {
    const state = lanes[lane];
    const mk = (cls: "m" | "s", label: string, on: boolean, title: string, toggle: () => Partial<LaneState>): HTMLButtonElement => {
      const b = h("button", { class: `ms ${cls}`, type: "button", "aria-pressed": String(on), title }, label);
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        lanes[lane] = { ...lanes[lane], ...toggle() };
        playback?.setLane(lane, lanes[lane]);
        b.setAttribute("aria-pressed", String(cls === "m" ? lanes[lane].mute : lanes[lane].solo));
      });
      return b;
    };
    const vol = h("input", { class: "vol", type: "range", min: "0", max: "1", step: "0.01", value: String(state.gain), title: "Громкость", "aria-label": "Громкость" });
    vol.addEventListener("click", (e) => e.stopPropagation());
    vol.addEventListener("input", () => {
      lanes[lane] = { ...lanes[lane], gain: Number(vol.value) };
      playback?.setLane(lane, lanes[lane]);
    });
    return [
      mk("m", "M", state.mute, "Заглушить", () => ({ mute: !lanes[lane].mute })),
      mk("s", "S", state.solo, "Соло", () => ({ solo: !lanes[lane].solo })),
      vol,
    ];
  }

  function lane(kind: string, cls = ""): HTMLElement {
    const el = h("div", { class: `lane ${cls}`.trim(), "data-kind": kind });
    el.style.setProperty("width", `${total * BAR_PX}px`);
    return el;
  }

  function block(fromBar: number, bars: number, label: string, colorVar: string, height: number, paint: ((ctx: CanvasRenderingContext2D, w: number, hgt: number) => void) | null): HTMLElement {
    const el = h("div", { class: "clip" });
    const width = Math.max(4, bars * BAR_PX - 2);
    el.style.setProperty("left", `${fromBar * BAR_PX + 1}px`);
    el.style.setProperty("width", `${width}px`);
    el.style.setProperty("--edge", `var(${colorVar})`);
    el.style.setProperty("--fill", `color-mix(in srgb, var(${colorVar}) 14%, transparent)`);
    if (label) el.append(h("span", { class: "clip__label" }, label));
    if (paint) {
      const inner = height - 10;
      const { canvas, ctx } = sizedCanvas(width - 2, inner);
      if (ctx) paint(ctx, width - 2, inner);
      el.append(canvas);
    }
    return el;
  }

  function wavePainter(data: PeakData | null, fromS: number, toS: number, colorVar: string) {
    return data ? (ctx: CanvasRenderingContext2D, w: number, hgt: number) => drawWave(ctx, w, hgt, data, fromS, toS, color(colorVar)) : null;
  }

  function takePeaksOf(name: string): PeakData | null {
    const got = takePeaks.get(name);
    if (got === undefined) {
      takePeaks.set(name, "loading");
      api.peaks("take", name, 2000).then(
        (p) => {
          takePeaks.set(name, { duration: p.duration_s, peaks: p.peaks });
          if (!scope.closed) render();
        },
        () => takePeaks.set(name, "failed"),
      );
      return null;
    }
    return typeof got === "object" ? got : null;
  }

  function takeBlock(name: string, label: string, height: number): HTMLElement | null {
    const data = takePeaksOf(name);
    const from = toBars(g, TAKE_LEAD);
    const duration = data?.duration ?? 0;
    if (!(duration > 0)) return data === null ? null : block(from, total - from, label, "--voice", height, null);
    return block(from, duration / barSeconds(g), label, "--voice", height, wavePainter(data, 0, duration, "--voice"));
  }

  function rankOf(name: string): { place: number | null; points: number | null } {
    const t = ranking?.takes.find((r) => (r.file.split(/[\\/]/).pop() ?? r.name) === name);
    return { place: t?.rank ?? null, points: t && ranking?.ranked ? t.total : null };
  }

  function render(): void {
    if (scope.closed || !ready) return;
    const view = visibleLanes(facts);
    const headEls: HTMLElement[] = [];
    const laneEls: HTMLElement[] = [];
    inner.style.setProperty("width", `${total * BAR_PX}px`);

    ruler.replaceChildren();
    const starts = new Set(clips.map((c) => Math.round(c.fromBar)));
    for (let b = 0; b < total; b++) ruler.append(h("span", { class: starts.has(b) ? "sec" : "" }, String(b + 1)));
    ruler.append(loopMark);

    for (const kind of view.lanes) {
      if (kind === "text") {
        const approximate = places.length > 0 && places[0]?.approximate === true;
        const per = (lyricLayout() ?? DEFAULT_LAYOUT).barsPerLine;
        const toggle = h("button", { class: "ms", type: "button", title: `Строка на ${per === 2 ? "два такта" : "один такт"}: нажми, чтобы поменять` }, `${per}т`);
        toggle.addEventListener("click", (e) => {
          e.stopPropagation();
          nudge({ barsPerLine: per === 2 ? 1 : 2 });
        });
        const sub = lyric.length === 0 ? "без текста" : approximate ? "примерно — потяни строки" : "строки по тактам";
        headEls.push(head("text", "Текст", sub, "--text-lane", lyric.length ? [toggle] : []));
        laneEls.push(textLane());
      } else if (kind === "voice") {
        headEls.push(head("voice", "Голос", voiceSub(), "--voice", laneControls("voice")));
        const el = lane("voice");
        if (facts.voiceKind === "suno") el.append(block(toBars(g, 0), total - toBars(g, 0), "Голос Suno", "--voice", 60, null));
        else if (facts.chosen) {
          const b = takeBlock(facts.chosen, `${takeLabel(facts.chosen)} ★`, 60);
          if (b) el.append(b);
        } else if (facts.takes > 0) el.append(h("span", { class: "lane__note" }, "выбери лучший дубль ★"));
        else el.append(h("span", { class: "lane__note" }, "запиши дубль: R, с начала бита"));
        laneEls.push(el);
      } else if (kind === "takes") {
        takeRows(headEls, laneEls);
      } else if (kind === "beat") {
        headEls.push(head("beat", "Бит", beatSub(), "--beat", laneControls("beat")));
        laneEls.push(pickable(beatLane(view.beat), "beat"));
      } else if (kind === "bass") {
        headEls.push(head("bass", "808", "ноты", "--bass", []));
        const el = lane("bass");
        for (const c of clips) el.append(clipOf(c, block(c.fromBar, c.bars, "", "--bass", 60, (ctx, _w, hgt) => drawNotes(ctx, marks.bass, c.fromBar, c.bars, BAR_PX, hgt, color("--bass")))));
        pickable(el, "bass");
        laneEls.push(el);
      } else if (kind === "drums") {
        headEls.push(head("drums", "Барабаны", "удары", "--drum", []));
        const el = lane("drums");
        for (const c of clips) el.append(clipOf(c, block(c.fromBar, c.bars, "", "--drum", 60, (ctx, _w, hgt) => drawHits(ctx, marks.drums, c.fromBar, c.bars, BAR_PX, hgt, color("--drum")))));
        pickable(el, "drums");
        laneEls.push(el);
      }
    }
    headList.replaceChildren(...headEls);
    laneBox.replaceChildren(...laneEls);
    paintLoop();
    paintTop();
    paintEditor();
    paintFrame();
  }

  function clipOf(c: Clip, el: HTMLElement): HTMLElement {
    el.dataset["clip"] = String(c.index);
    const on = selected !== null && BEAT_LANES.includes(selected) && (facts.beatKind === "own" ? bottom.block()?.section === c.index : facts.beatKind === "flip" && picked === c.index);
    el.classList.toggle("clip--on", on);
    return el;
  }

  // a click on a block selects its lane and the block, and opens the editor
  function pickable(el: HTMLElement, kind: LaneKind): HTMLElement {
    el.addEventListener("click", (e) => {
      if (suppressClick) return;
      const target = (e.target as HTMLElement).closest<HTMLElement>(".clip[data-clip]");
      if (!target || (facts.beatKind !== "own" && facts.beatKind !== "flip")) return;
      picked = Number(target.dataset["clip"]);
      bottom.selectBlock(picked);
      editorOpen = true;
      select(kind, true);
      render();
    });
    return el;
  }

  // ---------- blocks of the beat lane ----------
  const blocksEditable = (): boolean => (facts.beatKind === "flip" && flipDraft.doc !== null) || (facts.beatKind === "own" && bottom.score !== null);

  // one flip edit on the block arrangement; the pick follows the block the op made or moved
  function flipBlocks(make: (l: Layout) => BlockEdit | Refusal | null): void {
    if (transport.mode === "recording") {
      say("Во время записи бит не правится.");
      return;
    }
    const out = { at: null as number | null };
    const done = flipDraft.apply((doc) => {
      const edit = make(flipLayout(doc));
      if (edit === null) return null;
      if ("ok" in edit) return edit;
      out.at = edit.at;
      return withBlocks(doc, edit);
    });
    if (done && out.at !== null) {
      picked = out.at;
      bottom.selectBlock(out.at);
      rebuildModel();
      render();
    }
  }

  // one section edit of an own beat, through the editor's draft and history
  function ownSections(make: (s: Score) => SectionEdit): void {
    if (transport.mode === "recording") {
      say("Во время записи бит не правится.");
      return;
    }
    const current = bottom.score;
    if (!current) {
      say("Партитура ещё грузится.");
      return;
    }
    const out = make(current);
    if (!out.ok) {
      say(out.reason);
      return;
    }
    if (!bottom.editScore(out.score)) return;
    picked = out.selected;
    bottom.selectBlock(out.selected);
    rebuildModel();
    render();
  }

  function duplicateBlock(i: number): void {
    if (!blocksEditable()) return;
    if (facts.beatKind === "own") {
      ownSections((s) => duplicateSection(s, i));
      return;
    }
    flipBlocks((l) => {
      const why = copyRefusal(l, i);
      return why ? { ok: false, reason: why } : copyBlockTo(l, i, i + 1);
    });
  }

  function removeBlock(i: number): void {
    if (!blocksEditable()) return;
    if (facts.beatKind === "own") {
      ownSections((s) => deleteSection(s, i));
      return;
    }
    flipBlocks((l) => {
      const why = deleteRefusal(l, i);
      return why ? { ok: false, reason: why } : deleteBlock(l, i);
    });
  }

  function stepBlock(i: number, dir: -1 | 1): void {
    if (!blocksEditable()) return;
    if (facts.beatKind === "own") ownSections((s) => moveSection(s, i, i + dir));
    else flipBlocks((l) => moveBlock(l, i, dir));
  }

  function cutBlock(i: number): void {
    const clip = clips[i];
    if (!blocksEditable() || !clip || facts.beatKind !== "flip") return;
    const at = splitAt(clip, hoverBar);
    if (at === null) {
      say("Разрезать можно блок от двух тактов.");
      return;
    }
    flipBlocks((l) => splitBlock(l, i, at));
  }

  function blockBar(c: Clip): HTMLElement {
    const btn = (text: string, title: string, act: () => void, disabled = false): HTMLButtonElement => {
      const b = h("button", { class: "clipbar__btn", type: "button", title, "aria-label": title, disabled }, text);
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        act();
      });
      return b;
    };
    const i = c.index;
    const bar = h(
      "div",
      { class: "clipbar", role: "toolbar", "aria-label": `Блок ${c.label}` },
      btn("◀", "Сдвинуть левее", () => stepBlock(i, -1), i === 0),
      btn("▶", "Сдвинуть правее", () => stepBlock(i, 1), i === clips.length - 1),
      btn("Копия", "Копия после блока (Ctrl+D)", () => duplicateBlock(i)),
      ...(facts.beatKind === "flip" ? [btn("Разрезать", "Разрезать по такту под курсором, без него — посередине", () => cutBlock(i), (c.source?.toBar ?? 0) - (c.source?.fromBar ?? 0) < 2)] : []),
      btn("Удалить", "Удалить блок (Delete)", () => removeBlock(i)),
    );
    bar.style.setProperty("left", `${c.fromBar * BAR_PX + 4}px`);
    bar.addEventListener("pointerdown", (e) => e.stopPropagation());
    return bar;
  }

  // a block dragged along the lane drops between blocks; Ctrl copies it there
  function dragBlocks(el: HTMLElement): void {
    el.addEventListener("pointermove", (e: PointerEvent) => {
      if (!(e.target as HTMLElement).closest(".clipbar")) hoverBar = (e.clientX - inner.getBoundingClientRect().left) / BAR_PX;
    });
    el.addEventListener("pointerdown", (e: PointerEvent) => {
      const target = (e.target as HTMLElement).closest<HTMLElement>(".clip[data-clip]");
      if (e.button !== 0 || !target || !blocksEditable() || transport.mode === "recording") return;
      const index = Number(target.dataset["clip"]);
      const doc = target.ownerDocument;
      const x0 = e.clientX;
      const y0 = e.clientY;
      const ghost = h("div", { class: "clip-ghost", "aria-hidden": "true" });
      ghost.style.setProperty("width", `${(clips[index]?.bars ?? 1) * BAR_PX - 2}px`);
      let dragging = false;
      let plan: ReturnType<typeof planDrag> = null;
      const aim = (ev: PointerEvent): void => {
        const bar = (ev.clientX - inner.getBoundingClientRect().left) / BAR_PX;
        plan = planDrag(clips.map((c) => c.bars), index, bar, ev.ctrlKey || ev.metaKey);
        const show = plan !== null && !plan.noop;
        ghost.hidden = !show;
        ghost.classList.toggle("clip-ghost--copy", plan?.copy === true);
        ghost.classList.toggle("clip-ghost--refused", plan?.reason != null);
        if (plan) ghost.style.setProperty("left", `${plan.ghostBar * BAR_PX}px`);
        if (!ghost.isConnected) (beatLaneEl ?? el).append(ghost);
      };
      const move = (ev: PointerEvent): void => {
        if (!dragging && isDrag(x0, ev.clientX, y0, ev.clientY)) {
          dragging = true;
          target.classList.add("clip--drag");
        }
        if (dragging) aim(ev);
      };
      const end = (): void => {
        doc.removeEventListener("pointermove", move);
        doc.removeEventListener("pointerup", up);
        doc.removeEventListener("pointercancel", cancel);
        ghost.remove();
        target.classList.remove("clip--drag");
      };
      const cancel = (): void => end();
      const up = (ev: PointerEvent): void => {
        end();
        if (!dragging) return;
        suppressClick = true;
        scope.setTimeout(() => (suppressClick = false), 0);
        aim(ev);
        ghost.remove();
        const drop = plan as ReturnType<typeof planDrag>;
        if (!drop || drop.noop) return;
        if (drop.reason) {
          say(drop.reason);
          return;
        }
        picked = index;
        if (facts.beatKind === "own") ownSections((s) => (drop.copy ? duplicateSection(s, index, drop.to) : moveSection(s, index, drop.to)));
        else flipBlocks((l) => (drop.copy ? copyBlockTo(l, index, drop.to) : moveBlockTo(l, index, drop.to)));
      };
      doc.addEventListener("pointermove", move);
      doc.addEventListener("pointerup", up);
      doc.addEventListener("pointercancel", cancel);
    });
  }

  function voiceSub(): string {
    if (facts.voiceKind === "suno") return "трек из Suno";
    if (facts.takes === 0) return "дублей нет";
    return facts.chosen ? takeLabel(facts.chosen) : `${facts.takes} ${facts.takes === 1 ? "дубль" : facts.takes < 5 ? "дубля" : "дублей"}`;
  }

  function beatSub(): string {
    const busy = flipDraft.line() ?? bottom.beatLine();
    if (busy) return busy;
    if (facts.beatKind === "own") return reply.stations.beat.seed !== undefined ? `свой · ${reply.stations.beat.seed}` : "свой";
    const file = reply.stations.beat.file ?? "";
    return facts.beatKind === "flip" ? "флип" : (file.split("/").pop() ?? "файл");
  }

  function textLane(): HTMLElement {
    const el = lane("text", "lane--text");
    for (const p of places) {
      if (p.bars <= 0) continue;
      const text = lineText(p);
      const line = h("div", { class: "lyric", title: text, "data-from": String(p.fromBar) }, text);
      line.style.setProperty("left", `${p.fromBar * BAR_PX}px`);
      line.style.setProperty("width", `${p.bars * BAR_PX - 2}px`);
      el.append(line);
    }
    // drag the lines along by whole bars: the start bar moves
    el.addEventListener("pointerdown", (e: PointerEvent) => {
      const target = (e.target as HTMLElement).closest(".lyric") as HTMLElement | null;
      if (!target || e.button !== 0) return;
      const x0 = e.clientX;
      el.setPointerCapture(e.pointerId);
      let delta = 0;
      const move = (ev: PointerEvent): void => {
        delta = Math.round((ev.clientX - x0) / BAR_PX);
        el.style.setProperty("--shift", `${delta * BAR_PX}px`);
      };
      const up = (ev: PointerEvent): void => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        el.removeEventListener("pointercancel", up);
        el.style.removeProperty("--shift");
        if (Math.abs(ev.clientX - x0) < 4) {
          seekTo(toSeconds(g, Number(target.dataset["from"] ?? 0)));
          return;
        }
        const base = lyricLayout() ?? DEFAULT_LAYOUT;
        if (delta !== 0 && base.startBar + delta >= 0) nudge({ startBar: base.startBar + delta });
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
      el.addEventListener("pointercancel", up);
    });
    return el;
  }

  function takeRows(headEls: HTMLElement[], laneEls: HTMLElement[]): void {
    const live = transport.mode === "recording" && rec?.recording ? rec.take : null;
    const ranks: Record<string, number> = {};
    for (const n of facts.takeNames) {
      const r = rankOf(n).place;
      if (r !== null) ranks[n] = r;
    }
    const { shown, hidden } = capTakes({ names: facts.takeNames, ranks, chosen: facts.chosen, live, all: allTakes });
    for (const name of shown) {
      const isLive = name === live;
      const star = h("button", { class: "star", type: "button", "aria-pressed": String(facts.chosen === name), title: "Лучший дубль", disabled: isLive }, "★");
      star.addEventListener("click", (e) => {
        e.stopPropagation();
        if (facts.chosen !== name) void edit({ chosen: name, layout: { ...layout } });
      });
      const listen = h("button", { class: "tplay", type: "button", "data-take": name, "aria-pressed": String(takePlayer.name === name), title: "Послушать поверх бита", disabled: isLive });
      listen.append(icon(PATHS.small, "tplay__icon"));
      listen.addEventListener("click", (e) => {
        e.stopPropagation();
        if (takePlayer.name !== name && transport.mode === "playing") dispatch({ type: "pause" });
        void takePlayer.toggle(name);
      });
      const { points } = rankOf(name);
      const badge = points !== null ? h("span", { class: "badge", title: "Баллы оценки: чище и ровнее по замерам" }, String(Math.round(points))) : null;
      const label = h("span", {}, takeLabel(name));
      const nameEl = h("div", { class: "head__name" }, star, " ", label, isLive ? h("span", { class: "recdot" }, "● запись") : null);
      const el = h("div", { class: `head head--take head--sub${isLive ? " head--rec" : ""}`, "data-kind": "take" }, h("span", { class: "head__color", "aria-hidden": "true" }), nameEl, h("div", { class: "head__ctl" }, badge, listen));
      el.style.setProperty("--c", isLive ? "var(--red)" : "var(--voice)");
      headEls.push(el);
      const row = lane("take", `lane--take${isLive ? " lane--rec" : ""}`);
      if (isLive) {
        const grow = h("div", { class: "reclabel", "data-ref": "grow" });
        grow.style.setProperty("left", `${toBars(g, TAKE_LEAD) * BAR_PX}px`);
        row.append(grow);
      } else {
        row.classList.toggle("lane--dim", facts.chosen !== null && facts.chosen !== name);
        const b = takeBlock(name, "", 40);
        if (b) row.append(b);
      }
      laneEls.push(row);
    }
    if (hidden > 0 || allTakes) {
      const more = h("button", { class: "add", type: "button" }, allTakes ? "свернуть" : `ещё ${hidden}`);
      more.addEventListener("click", () => {
        allTakes = !allTakes;
        render();
      });
      const el = h("div", { class: "head head--take head--sub head--more", "data-kind": "more" }, h("span", { class: "head__color", "aria-hidden": "true" }), h("div", { class: "head__name" }, more), h("div", {}));
      el.style.setProperty("--c", "var(--voice)");
      headEls.push(el);
      laneEls.push(lane("more", "lane--take lane--more"));
    }
  }

  function beatLane(mode: "analyse" | "file" | "blocks"): HTMLElement {
    const el = lane("beat");
    if (mode === "blocks") {
      for (const c of clips) {
        const from = toSeconds(g, c.fromBar);
        el.append(clipOf(c, block(c.fromBar, c.bars, c.label, "--beat", 60, wavePainter(beatPeaks, from, from + c.bars * barSeconds(g), "--beat"))));
      }
      beatLaneEl = el;
      if (facts.beatKind === "own") bottom.prepare();
      if (blocksEditable()) {
        dragBlocks(el);
        const chosen = picked === null ? undefined : clips[picked];
        if (chosen && transport.mode !== "recording") el.append(blockBar(chosen));
      }
      return el;
    }
    const duration = beatPeaks?.duration ?? 0;
    const from = toBars(g, 0);
    const bars = duration > 0 ? duration / barSeconds(g) : total - from;
    el.append(block(from, bars, "", "--beat", 60, wavePainter(beatPeaks, 0, duration, "--beat")));
    if (mode === "analyse") {
      const running = jobs.some((j) => j.kind === "flip_stems" && jobActive(j));
      const go = h("button", { class: "btn analyse", type: "button", disabled: running }, running ? "Разбираю бит…" : "Разобрать бит (≈1 мин)");
      go.addEventListener("click", () => void startJob("flip_stems"));
      el.append(go);
    }
    return el;
  }

  function paintTakeButtons(): void {
    for (const b of headList.querySelectorAll<HTMLElement>(".tplay")) b.setAttribute("aria-pressed", String(b.dataset["take"] === takePlayer.name));
  }

  function paintLoop(): void {
    loopBtn.setAttribute("aria-pressed", String(loopOn));
    loopMark.classList.toggle("off", !loopOn || !loop);
    if (loop) {
      loopMark.style.setProperty("left", `${loop.fromBar * BAR_PX}px`);
      loopMark.style.setProperty("width", `${(loop.toBar - loop.fromBar) * BAR_PX}px`);
    }
  }

  // ---------- the editor shell ----------
  function leaveBeat(): void {
    if (facts.beatKind === "flip") {
      if (flipDraft.wantsCommit && transport.mode !== "recording") commitInBackground();
      return;
    }
    if (facts.beatKind !== "own" || !bottom.beatDirty || transport.mode !== "idle") return;
    dispatch({ type: "renderStarted" });
    void commitBeat();
  }

  function select(kind: LaneKind, keepPick = false): void {
    if (selected !== kind && !keepPick) picked = null;
    if (selected !== null && BEAT_LANES.includes(selected) && !BEAT_LANES.includes(kind)) leaveBeat();
    editorOpen = editorOpenAfter(editorOpen, selected, kind);
    selected = kind;
    const tabs = editorTabs(kind, facts.beatKind);
    if (!tab || !tabs.includes(tab)) tab = tabs[0] ?? null;
    for (const el of headList.querySelectorAll<HTMLElement>(".head[aria-selected]")) el.setAttribute("aria-selected", String(el.dataset["kind"] === kind));
    paintEditor();
  }

  function paintEditor(): void {
    const names: Record<LaneKind, string> = { text: "Текст", voice: "Голос", takes: "Голос", beat: "Бит", bass: "808", drums: "Барабаны" };
    editor.classList.toggle("collapsed", !editorOpen || selected === null);
    collapse.hidden = selected === null;
    collapse.textContent = editorOpen ? "свернуть ▾" : "развернуть ▴";
    if (selected === null) {
      what.replaceChildren("Выбери дорожку слева");
      tabBox.replaceChildren();
      panes.replaceChildren();
      return;
    }
    what.replaceChildren("Редактор: ", h("b", {}, names[selected]));
    const tabs = editorTabs(selected, facts.beatKind);
    const layers = facts.beatKind === "flip" && flipDraft.doc ? layerProblem(flipDraft.doc) : null;
    tabBox.replaceChildren(
      ...tabs.map((t) => {
        const blocked = layers !== null && (t === "roll" || t === "drums");
        const b = h("button", { class: "etab", type: "button", role: "tab", "aria-selected": String(t === tab), "data-tab": t, disabled: blocked, title: blocked ? (layers ?? undefined) : undefined }, TAB_NAMES[t]);
        b.addEventListener("click", () => {
          tab = t;
          editorOpen = true;
          paintEditor();
        });
        return b;
      }),
    );
    bottom.mount(panes, selected, tab, tabs, editorOpen);
  }

  collapse.addEventListener("click", () => {
    editorOpen = !editorOpen;
    if (!editorOpen && selected !== null && BEAT_LANES.includes(selected)) leaveBeat();
    paintEditor();
  });

  // ---------- the frame loop ----------
  function position(): number {
    if (transport.mode === "recording" && rec?.recording) {
      const since = rec.stopping ? 0 : (performance.now() - recAt) / 1000;
      return TAKE_LEAD + rec.seconds + since;
    }
    if (takePlayer.name) return takePlayer.time.now;
    return playback?.position() ?? 0;
  }

  function paintFrame(): void {
    const at = position();
    const bars = toBars(g, at);
    playhead.style.setProperty("left", `${Math.max(0, bars) * BAR_PX}px`);
    time.textContent = clockText(at);
    barPos.textContent = `такт ${barBeat(Math.max(0, bars))}`;
    const now = lineAt(places, bars);
    for (const el of laneBox.querySelectorAll<HTMLElement>(".lyric")) el.classList.toggle("now", now !== null && Number(el.dataset["from"]) === now.fromBar);
    const grow = laneBox.querySelector<HTMLElement>('[data-ref="grow"]');
    if (grow && rec) grow.style.setProperty("width", `${Math.max(0, rec.seconds / barSeconds(g)) * BAR_PX}px`);
    const moving = transport.mode === "playing" || transport.mode === "recording" || takePlayer.name !== null;
    if (moving) {
      const x = Math.max(0, bars) * BAR_PX;
      if (x > timeline.scrollLeft + timeline.clientWidth - 80 || x < timeline.scrollLeft) timeline.scrollLeft = Math.max(0, x - 120);
    }
    const peak = transport.mode === "recording" ? (rec?.peak_db ?? null) : (() => {
      const lin = playback?.meter() ?? 0;
      return lin > 0 ? 20 * Math.log10(lin) : null;
    })();
    meterFill.style.setProperty("--v", `${(hold.push(meterLevel(peak), performance.now()) * 100).toFixed(1)}%`);
    if (transport.mode === "playing" && playback && !playback.playing && performance.now() - playStarted > 800) dispatch({ type: "pause" });
  }

  let playStarted = 0;
  const loopFrame = (): void => {
    paintFrame();
    scope.requestFrame(loopFrame);
  };

  // ---------- the ruler ----------
  scope.listen(ruler, "pointerdown", (e: PointerEvent) => {
    if (e.button !== 0) return;
    const left = inner.getBoundingClientRect().left;
    const down = e.clientX - left;
    ruler.setPointerCapture(e.pointerId);
    const preview = (x: number): void => {
      const gst = rulerGesture(down, x, BAR_PX, total);
      if (gst.kind !== "loop") return;
      loopMark.classList.remove("off");
      loopMark.style.setProperty("left", `${gst.fromBar * BAR_PX}px`);
      loopMark.style.setProperty("width", `${(gst.toBar - gst.fromBar) * BAR_PX}px`);
    };
    const move = (ev: PointerEvent): void => preview(ev.clientX - left);
    const up = (ev: PointerEvent): void => {
      ruler.removeEventListener("pointermove", move);
      ruler.removeEventListener("pointerup", up);
      ruler.removeEventListener("pointercancel", up);
      const gst = rulerGesture(down, ev.clientX - left, BAR_PX, total);
      if (gst.kind === "seek") {
        if (loopOn && loop && (gst.bar < loop.fromBar || gst.bar >= loop.toBar)) {
          loopOn = false;
          playback?.setLoop(null);
        }
        paintLoop();
        seekTo(toSeconds(g, gst.bar));
        return;
      }
      loop = { fromBar: gst.fromBar, toBar: gst.toBar };
      loopOn = true;
      playback?.setLoop(currentLoop());
      paintLoop();
    };
    ruler.addEventListener("pointermove", move);
    ruler.addEventListener("pointerup", up);
    ruler.addEventListener("pointercancel", up);
  });

  scope.listen(timeline, "scroll", () => {
    headList.scrollTop = timeline.scrollTop;
  });

  // ---------- the calibration modal ----------
  let modal: HTMLElement | null = null;
  let modalMode: Mode = "cup";

  function paintModal(): void {
    const want = transport.mode === "recording" && transport.step === "calibrate";
    if (!want) {
      modal?.remove();
      modal = null;
      return;
    }
    if (modal) return;
    const seg = h("div", { class: "pick3", role: "radiogroup", "aria-label": "Как калибровать" });
    const sayMode = h("p", { class: "dialog__say" }, CALIBRATION[modalMode]);
    for (const m of Object.keys(MODE_NAMES) as Mode[]) {
      const b = h("button", { type: "button", role: "radio", "aria-checked": String(m === modalMode) }, MODE_NAMES[m]);
      b.addEventListener("click", () => {
        modalMode = m;
        for (const x of seg.querySelectorAll("button")) x.setAttribute("aria-checked", String(x === b));
        sayMode.textContent = CALIBRATION[m];
      });
      seg.append(b);
    }
    const go = h("button", { class: "btn btn--main", type: "button" }, "Калибровать");
    const skip = h("button", { class: "btn btn--ghost", type: "button" }, "Пропустить");
    go.addEventListener("click", async () => {
      go.disabled = true;
      skip.disabled = true;
      await calibrate(modalMode);
      if (transport.mode === "recording" && transport.step === "calibrate") dispatch({ type: "calibrated" });
    });
    skip.addEventListener("click", () => dispatch({ type: "calibrated" }));
    const box = h(
      "div",
      { class: "dialog__box", role: "dialog", "aria-modal": "true", "aria-label": "Калибровка задержки" },
      h("h2", {}, "Калибровка задержки"),
      h("p", { class: "dialog__why" }, "Движок не знает задержку круга. Свип займёт несколько секунд; без него дубли могут съехать по времени. Предлагаю один раз."),
      seg,
      sayMode,
      h("div", { class: "dialog__row" }, skip, go),
    );
    modal = h("div", { class: "dialog" }, box);
    modal.addEventListener("keydown", (e) => {
      if (e.key === "Escape") dispatch({ type: "stop" });
    });
    root.append(modal);
    go.focus();
  }

  // ---------- the ⋯ menu ----------
  let menu: HTMLElement | null = null;
  let closeMenu: (() => void) | null = null;

  async function openMenu(): Promise<void> {
    if (menu) {
      closeMenu?.();
      return;
    }
    const items: HTMLElement[] = [];
    const item = (label: string, act: () => void, disabled = false): void => {
      const b = h("button", { class: "menu__item", type: "button", role: "menuitem", disabled }, label);
      b.addEventListener("click", () => {
        closeMenu?.();
        act();
      });
      items.push(b);
    };
    const group = (label: string): void => {
      items.push(h("p", { class: "menu__group" }, label));
    };
    if (facts.voiceKind === "own") {
      group("Калибровка");
      for (const m of Object.keys(MODE_NAMES) as Mode[]) item(`${MODE_NAMES[m]} — ${CALIBRATION[m].toLowerCase()}`, () => void calibrate(m), transport.mode === "recording");
      item(
        "Сбросить последний дубль",
        () => {
          if (!confirm("Сбросить последний записанный дубль?")) return;
          api.recDiscard().then(
            async (r) => {
              say(r.code === "discarded" ? "Дубль сброшен." : `Ответ движка: ${r.code}`);
              await reload();
            },
            (error: unknown) => say(recRefusal(error)),
          );
        },
        facts.takes === 0,
      );
    }
    const running = jobs.filter((j) => jobActive(j) && j.state !== "cancelling");
    if (running.length) {
      group("Задачи");
      for (const j of running) {
        item(`Отменить: ${JOB_NAMES[j.kind]}`, () => {
          api.cancelJob(j.kind).then(
            () => refreshJobs(),
            (error: unknown) => say(message(error)),
          );
        });
      }
    }
    group("Песня");
    const folder = reply.stations.result.folder;
    item(folder ? `Папка итога: ${folder}` : "Папки итога ещё нет", () => {
      if (!folder) return;
      void navigator.clipboard?.writeText(folder).then(
        () => say("Путь к папке скопирован."),
        () => say(folder),
      );
    }, folder === null);
    let seeds: number[] = [];
    try {
      seeds = (await api.sources()).seeds;
    } catch {
      seeds = [];
    }
    const current = reply.stations.beat.kind === "own" ? reply.stations.beat.seed : undefined;
    for (const seed of seeds) {
      if (seed === current) continue;
      item(`Сменить бит на свой · ${seed}`, () => void change({ beat: { own: seed } }), transport.mode === "recording");
    }
    if (!seeds.length) item("Сменить бит: своих битов нет", () => {}, true);
    menu = h("div", { class: "menu", role: "menu" }, ...items);
    top.append(menu);
    moreBtn.setAttribute("aria-expanded", "true");
    closeMenu = dismissable(menu, moreBtn, () => {
      menu?.remove();
      menu = null;
      closeMenu = null;
      moreBtn.setAttribute("aria-expanded", "false");
    });
  }
  scope.add(() => closeMenu?.());

  // ---------- wiring ----------
  brand.addEventListener("click", () => options.toStart());
  home.addEventListener("click", () => seekTo(currentLoop()?.start ?? 0));
  play.addEventListener("click", pressPlay);
  stopBtn.addEventListener("click", () => {
    takePlayer.stop();
    dispatch({ type: "stop" });
  });
  recBtn.addEventListener("click", pressRecord);
  loopBtn.addEventListener("click", toggleLoop);
  moreBtn.addEventListener("click", () => void openMenu());
  exportBtn.addEventListener("click", pressExport);
  chip.addEventListener("click", () => {
    switch (nextStep(facts)) {
      case "analyse":
        void startJob("flip_stems");
        return;
      case "listen":
        pressPlay();
        return;
      case "record":
        pressRecord();
        return;
      case "prepare-mix":
        void startJob("mix");
        return;
      default:
        return;
    }
  });

  const keys = new KeySurface(root.ownerDocument, (e: KeyboardEvent) => {
    if (e.defaultPrevented || modal) return;
    const action = dawKey(e, typing(e.target));
    if (!action) return;
    // a focused button would take Space as a click too
    e.preventDefault();
    switch (action) {
      case "play":
        pressPlay();
        return;
      case "record":
        pressRecord();
        return;
      case "loop":
        toggleLoop();
        return;
      case "home":
        seekTo(currentLoop()?.start ?? 0);
        return;
      case "undo":
        void undo(false);
        return;
      case "redo":
        void undo(true);
        return;
      case "duplicate":
        {
          const at = keyedBlock(selected, picked, clips.length);
          if (at !== null) duplicateBlock(at);
        }
        return;
      case "delete":
        // a Delete inside the editor belongs to what is edited there, a roll note
        {
          const at = keyedBlock(selected, picked, clips.length);
          if (at !== null && !editor.contains(e.target as Node | null) && !bottom.notePicked) removeBlock(at);
        }
        return;
    }
  });
  keys.setActive(true);
  scope.add(() => keys.setActive(false));
  scope.listen(window, "pagehide", () => flipDraft.leave());
  scope.listen(root.ownerDocument, "visibilitychange", () => {
    const seen = root.ownerDocument.visibilityState !== "hidden";
    jobsPoller.setVisible(seen);
    recPoller.setVisible(seen);
  });

  // ---------- first load ----------
  [reply, jobs] = await Promise.all([api.song(), api.jobs()]);
  const firstLayout = api.layout().then(
    (l) => (layout = l),
    () => (layout = { start_bar: null, bars_per_line: null }),
  );
  await Promise.all([refreshSide(), loadText(), loadRanking(), firstLayout, api.recState().then(applySnapshot, () => {})]);
  rebuildModel();
  ready = true;
  loading.remove();
  history = new History<Snapshot>(snapshot());
  // the engine may already be recording from before the page loaded
  if ((rec as RecSnapshot | null)?.recording) transport = { mode: "recording", step: "recording" };
  syncRecPoll();
  jobsPoller.setWanted(jobs.some(jobActive));
  syncPlayback();
  render();
  scope.requestFrame(loopFrame);

  return {
    async dispose() {
      if (scope.closed) return;
      flushWaiters();
      await bottom.dispose();
      await flipDraft.dispose();
      scope.dispose();
      api.close();
      flipApi.close();
      beatApi.close();
      modal?.remove();
      app.remove();
      hint.remove();
      root.classList.remove("daw", "recording");
      await playback?.dispose();
    },
  };
}
