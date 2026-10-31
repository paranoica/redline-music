// The bottom editor's contents for the selected lane: an own beat's piano roll and drum grid, the
// effects, the text. Own-beat edits go to one draft per seed after a quiet moment; the numbered
// score and its render happen at the commit points only (commitBeat), and a small ▶ auditions the
// selected block live through the beat engine.

import type { Api as BeatApi } from "../beat/api.ts";
import { ModelError, type Score } from "../beat/model.ts";
import { History } from "../core/history.ts";
import { apiPath, apiRoot } from "../core/mount.ts";
import type { FlipApi, FlipState } from "../flip/api.ts";
import type { Job, SongApi, SongChange, SongReply, StartKind } from "../song/api.ts";
import { h, icon } from "../ui/dom.ts";
import type { Clip } from "./clips.ts";
import { BeatDraft } from "./draft.ts";
import { DrumsView, type BeatEditing, type Block } from "./drums.ts";
import { FxPane, type StudioLike } from "./fx.ts";
import type { LyricLayout } from "./lyrics.ts";
import type { Playback } from "./player.ts";
import { RollView } from "./roll.ts";
import { chooseShare, shareGate, type ShareChoice } from "./share.ts";
import { TextTab } from "./text-tab.ts";
import type { EditorTab, LaneKind, ScreenFacts } from "./view.ts";

const PLAY = "M8 5.5v13L18 12z";
const STOP = "M7 7h10v10H7z";

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export const BEAT_LANES: readonly LaneKind[] = ["beat", "bass", "drums"];

export interface EditorHost {
  base: string | undefined;
  token: string;
  api: SongApi;
  beatApi: BeatApi;
  flipApi: FlipApi;
  reply(): SongReply;
  facts(): ScreenFacts;
  jobs(): readonly Job[];
  flip(): FlipState | null;
  clips(): readonly Clip[];
  // the playhead in bars, for the block picked when none is
  playheadBar(): number;
  playback(): Playback | null;
  studio(): StudioLike | null;
  // the song's own playback stops before an audition starts
  stopSong(): void;
  startJob(kind: StartKind): Promise<void>;
  change(set: SongChange): Promise<boolean>;
  reload(): Promise<void>;
  // the song reloads after a render and the player has the new beat
  beatRendered(): Promise<void>;
  layout(): LyricLayout;
  nudge(to: Partial<LyricLayout>): void;
  // the edited score, for the lanes
  scoreChanged(score: Score): void;
  // the beat's draft, render or flip state changed: the lane's line and the play button
  beatState(): void;
  // the editor wants to be drawn again
  repaint(): void;
  say(text: string): void;
  overlay: HTMLElement;
}

interface Ask {
  section: number;
  pattern: string;
  count: number;
  change: (score: Score, pattern: string) => Score;
}

export class Editor {
  readonly audition: HTMLButtonElement;
  readonly #host: EditorHost;
  readonly #roll: RollView;
  readonly #drums: DrumsView;
  readonly #fx: FxPane;
  readonly #text: TextTab;
  readonly #draft: BeatDraft;
  #history: History<Score> | null = null;
  readonly #answered = new Set<string>();
  #ask: Ask | null = null;
  #block: number | null = null;
  #auditioning = false;
  #lane: LaneKind | null = null;
  #tab: EditorTab | null = null;
  #closed = false;
  #flipBusy: string | null = null;
  #onUnload = (): void => this.#leaveNow();

  constructor(host: EditorHost) {
    this.#host = host;
    const editing: BeatEditing = {
      score: () => this.#draft.score,
      block: () => this.block(),
      edit: (section, pattern, change) => this.#edit(section, pattern, change),
    };
    this.#roll = new RollView(editing);
    this.#drums = new DrumsView(editing);
    this.#fx = new FxPane({
      base: host.base,
      token: host.token,
      facts: () => host.facts(),
      jobs: () => host.jobs(),
      studio: () => host.studio(),
      startJob: (kind) => host.startJob(kind),
      flip: () => host.flip(),
      flipApi: host.flipApi,
      reload: () => host.reload(),
      beatBusy: (text) => {
        this.#flipBusy = text;
        host.beatState();
      },
      say: (text) => host.say(text),
      overlay: host.overlay,
    });
    this.#text = new TextTab({
      api: host.api,
      reply: () => host.reply(),
      change: (set) => host.change(set),
      reload: () => host.reload(),
      layout: () => host.layout(),
      nudge: (to) => host.nudge(to),
      say: (text) => host.say(text),
    });
    this.#draft = new BeatDraft({
      api: host.beatApi,
      facts: () => host.facts(),
      loaded: (score, edited) => {
        this.#history = new History<Score>(score);
        if (edited) host.scoreChanged(score);
      },
      loadEnded: () => host.repaint(),
      reload: () => host.beatRendered(),
      changed: () => host.beatState(),
      say: (text) => host.say(text),
    });
    this.audition = h("button", { class: "eplay", type: "button", title: "Послушать кусок по кругу, с правками", "aria-pressed": "false", hidden: true });
    this.audition.append(icon(PLAY, "eplay__icon"));
    this.audition.addEventListener("click", () => void this.#toggleAudition());
    addEventListener("pagehide", this.#onUnload);
  }

  // ---------- what the screen asks ----------

  // an own-beat edit the server's newest score does not have, saved or not
  get beatDirty(): boolean {
    return this.#draft.dirty;
  }

  // the score as edited, null until it is loaded
  get score(): Score | null {
    return this.#draft.score;
  }

  get rendering(): boolean {
    return this.#draft.rendering;
  }

  get renders(): number {
    return this.#draft.renders;
  }

  // the words on the beat lane while it is being rebuilt, null when it is not
  beatLine(): string | null {
    if (this.#draft.rendering) return "бит обновляется…";
    if (this.#flipBusy) return this.#flipBusy;
    if (this.#draft.saveState === "failed") return "черновик не сохранён";
    return null;
  }

  block(): Block | null {
    const clips = this.#host.clips().filter((c) => c.bars >= 1);
    if (!clips.length) return null;
    let clip = this.#block === null ? undefined : clips.find((c) => c.index === this.#block);
    if (!clip) {
      const at = this.#host.playheadBar();
      clip = clips.find((c) => at >= c.fromBar && at < c.fromBar + c.bars) ?? clips[0];
    }
    if (!clip) return null;
    return { section: clip.index, fromBar: Math.round(clip.fromBar), bars: Math.max(1, Math.round(clip.bars)), label: clip.label };
  }

  selectBlock(index: number): void {
    this.#block = index;
    if (this.#auditioning) void this.#startAudition();
  }

  // the song's playback took over
  auditionOff(): void {
    this.#auditioning = false;
    this.#paintAudition();
  }

  // the panes for the lane, the active tab drawn
  mount(panes: HTMLElement, lane: LaneKind, tab: EditorTab | null, tabs: readonly EditorTab[], open: boolean): void {
    this.#lane = lane;
    this.#tab = tab;
    this.#syncSeed();
    const own = this.#host.facts().beatKind === "own" && BEAT_LANES.includes(lane);
    if (own && !this.#draft.score) void this.#draft.load();
    const els = tabs.map((t) => {
      const pane = h("div", { class: `epane${t === tab ? " on" : ""}`, "data-pane": t, role: "tabpanel" });
      if (t === tab && open) pane.append(...this.#content(t, lane));
      return pane;
    });
    panes.replaceChildren(...els);
    this.#paintAudition();
  }

  // Ctrl+Z / Ctrl+Y inside the editor; false when it is not the editor's to take
  undo(redo: boolean): boolean {
    if (this.#tab === "roll" || this.#tab === "drums") {
      const past = this.#history;
      if (!past || !this.#draft.score) return false;
      const to = redo ? past.redo() : past.undo();
      if (!to) return true;
      this.#apply(to, false);
      return true;
    }
    if (this.#tab === "fx" && !redo) return this.#fx.undo();
    return false;
  }

  jobsDone(kinds: readonly string[], jobs: readonly Job[]): void {
    this.#fx.jobsDone(kinds, jobs);
  }

  // Saves the edited score as the next numbered one and renders it, then the song reloads.
  // `wait` false: the render is started and left to the server.
  commitBeat(wait = true): Promise<void> {
    return this.#draft.commit(wait);
  }

  async dispose(): Promise<void> {
    if (this.#closed) return;
    removeEventListener("pagehide", this.#onUnload);
    await this.#fx.flush().catch(() => {});
    if (this.beatDirty) await this.commitBeat(false).catch(() => {});
    this.#closed = true;
    this.#draft.dispose();
    this.#fx.dispose();
    if (this.#auditioning) await this.#host.playback()?.stopAudition();
  }

  // ---------- loading ----------

  #syncSeed(): void {
    const b = this.#host.reply().stations.beat;
    const seed = b.kind === "own" && typeof b.seed === "number" ? b.seed : null;
    if (!this.#draft.reset(seed)) return;
    this.#history = null;
    this.#answered.clear();
    this.#ask = null;
    this.#block = null;
  }

  // ---------- edits ----------

  #edit(section: number, pattern: string, change: (score: Score, pattern: string) => Score): void {
    const score = this.#draft.score;
    if (!score || this.#ask) return;
    const gate = shareGate(score, section, pattern, this.#answered);
    if (gate.kind === "ask") {
      this.#ask = { section, pattern, count: gate.count, change };
      this.#host.repaint();
      return;
    }
    this.#run(() => change(score, pattern));
  }

  #answer(choice: ShareChoice | null): void {
    const ask = this.#ask;
    this.#ask = null;
    const score = this.#draft.score;
    if (!ask || !score || choice === null) {
      this.#host.repaint();
      return;
    }
    this.#run(() => {
      const made = chooseShare(score, ask.section, ask.pattern, choice);
      this.#answered.add(made.pattern);
      return ask.change(made.score, made.pattern);
    });
  }

  #run(make: () => Score): void {
    let next: Score;
    try {
      next = make();
    } catch (error) {
      this.#host.say(error instanceof ModelError ? `Правка не встала: ${error.message}` : message(error));
      this.#host.repaint();
      return;
    }
    this.#apply(next, true);
  }

  #apply(next: Score, push: boolean): void {
    this.#draft.apply(next);
    if (push) this.#history?.push(next);
    this.#host.playback()?.updateAudition(next);
    this.#host.scoreChanged(next);
    this.#host.beatState();
  }

  // the page goes away: the newest draft goes out with the request kept alive
  #leaveNow(): void {
    const unsent = this.#draft.unsent();
    if (!unsent) return;
    const url = apiPath(apiRoot(this.#host.base, "/api/beat"), "/draft");
    void fetch(url, {
      method: "POST",
      keepalive: true,
      headers: { "X-Token": this.#host.token, "Content-Type": "application/json" },
      body: JSON.stringify(unsent),
    }).catch(() => {});
  }

  // ---------- drawing ----------

  #content(tab: EditorTab, lane: LaneKind): Node[] {
    switch (tab) {
      case "roll":
        this.#roll.show();
        return [this.#askBar(), this.#roll.root].filter((n): n is HTMLElement => n !== null);
      case "drums":
        this.#drums.show();
        return [this.#askBar(), this.#drums.root].filter((n): n is HTMLElement => n !== null);
      case "fx": {
        const f = this.#host.facts();
        const group = BEAT_LANES.includes(lane) ? "beat" : "voice";
        this.#fx.show(group, JSON.stringify([f.chosen, this.#host.reply().stations.beat.file, f.studioReady]));
        return [this.#fx.root];
      }
      case "text":
        this.#text.show();
        return [this.#text.root];
    }
  }

  #askBar(): HTMLElement | null {
    const ask = this.#ask;
    if (!ask) return null;
    const all = h("button", { class: "chip", type: "button" }, `изменить во всех ${ask.count} местах`);
    const here = h("button", { class: "chip", type: "button" }, "только здесь");
    const no = h("button", { class: "chip chip--ghost", type: "button" }, "отмена");
    all.addEventListener("click", () => this.#answer("all"));
    here.addEventListener("click", () => this.#answer("here"));
    no.addEventListener("click", () => this.#answer(null));
    const bar = h("div", { class: "askbar", role: "alertdialog", "aria-label": "Общий паттерн" }, h("span", {}, `Паттерн ${ask.pattern} играет в ${ask.count} местах.`), all, here, no);
    queueMicrotask(() => here.focus({ preventScroll: true }));
    return bar;
  }

  #paintAudition(): void {
    const f = this.#host.facts();
    const show = f.beatKind === "own" && this.#lane !== null && BEAT_LANES.includes(this.#lane) && this.#draft.score !== null;
    this.audition.hidden = !show;
    this.audition.setAttribute("aria-pressed", String(this.#auditioning));
    this.audition.replaceChildren(icon(this.#auditioning ? STOP : PLAY, "eplay__icon"));
    this.audition.title = this.#auditioning ? "Остановить кусок" : "Послушать кусок по кругу, с правками";
  }

  async #toggleAudition(): Promise<void> {
    const pb = this.#host.playback();
    if (!pb) return;
    if (this.#auditioning) {
      this.#auditioning = false;
      this.#paintAudition();
      await pb.stopAudition();
      return;
    }
    await this.#startAudition();
  }

  async #startAudition(): Promise<void> {
    const pb = this.#host.playback();
    const score = this.#draft.score;
    const block = this.block();
    if (!pb || !score || !block) return;
    this.#host.stopSong();
    this.#auditioning = true;
    this.#paintAudition();
    try {
      await pb.audition(score, block.fromBar, block.bars);
    } catch (error) {
      this.#auditioning = false;
      this.#paintAudition();
      this.#host.say(`Кусок не играет: ${message(error)}`);
    }
  }
}
