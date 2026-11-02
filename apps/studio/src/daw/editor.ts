// The bottom editor's contents for the selected lane: an own beat's channel rack and piano roll,
// a flip's notes and drum grid, the effects, the text. Own-beat edits go to one draft per seed
// after a quiet moment; the numbered score and its render happen at the commit points only
// (commitBeat), and a small ▶ auditions the selected block live through the beat engine. In
// pattern mode the transport's play loops the rack's pattern alone through the same engine.

import type { Api as BeatApi, PackList } from "../beat/api.ts";
import { ModelError, type InstrumentSpec, type Score } from "../beat/model.ts";
import { History } from "../core/history.ts";
import { apiPath, apiRoot } from "../core/mount.ts";
import type { FlipApi, FlipState } from "../flip/api.ts";
import type { Job, SongApi, SongChange, SongReply, StartKind } from "../song/api.ts";
import { h, icon } from "../ui/dom.ts";
import type { Clip } from "./clips.ts";
import { BeatDraft } from "./draft.ts";
import { DrumsView, flipDrums, scoreDrums, type BeatEditing, type Block } from "./drums.ts";
import type { FlipDraft } from "./flipdraft.ts";
import { FxPane, type StudioLike } from "./fx.ts";
import type { LyricLayout } from "./lyrics.ts";
import type { Playback } from "./player.ts";
import { addPicked, hitScore, loopedScore, patternPreview, stepAt, type Pick, type Preview, type StepAt } from "./pattern.ts";
import { paintSections, uniqueClip, type PaintPlan } from "./playlist.ts";
import { RackView } from "./rack.ts";
import { FlipRoll, layerProblem, RollView, scoreRoll, SourceNotes, type NoteRowRef } from "./roll.ts";
import { chooseShare, shareGate, type ShareChoice } from "./share.ts";
import { TextTab } from "./text-tab.ts";
import type { EditorTab, LaneKind, ScreenFacts } from "./view.ts";

const PLAY = "M8 5.5v13L18 12z";
const STOP = "M7 7h10v10H7z";

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

const hasText = (reply: SongReply): boolean => {
  const t = reply.stations.text;
  return t.seed !== null && t.version !== undefined && t.version !== null;
};

export const BEAT_LANES: readonly LaneKind[] = ["beat", "bass", "drums", "patterns"];

export interface EditorHost {
  base: string | undefined;
  token: string;
  api: SongApi;
  beatApi: BeatApi;
  flipApi: FlipApi;
  flipDraft: FlipDraft;
  confirm(text: string): boolean;
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
  // the editor wants another of the lane's tabs on screen
  openTab(tab: EditorTab): void;
  say(text: string): void;
  // the block or the pattern started or stopped playing live
  liveChanged?(): void;
  // another pattern picked in the rack
  rackSwitched?(): void;
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
  readonly #rack: RackView;
  readonly #drums: DrumsView;
  readonly #flipRoll: RollView;
  readonly #flipDrums: DrumsView;
  readonly #source: SourceNotes;
  readonly #fx: FxPane;
  readonly #text: TextTab;
  readonly #draft: BeatDraft;
  #history: History<Score> | null = null;
  readonly #answered = new Set<string>();
  #ask: Ask | null = null;
  #block: number | null = null;
  // the note row the roll edits, picked in the rack
  #notes: NoteRowRef | null = null;
  #auditioning = false;
  // what the live engine loops while #auditioning: the selected block, or the rack's pattern alone
  #live: "block" | "pattern" = "block";
  #patternMode = false;
  #pack: Promise<PackList> | null = null;
  #auditionSeq = 0;
  #scoreKey: string | null = null;
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
    this.#roll = new RollView(
      scoreRoll({
        ...editing,
        notes: () => this.#notes,
        editPattern: (change) => {
          const score = this.#draft.score;
          if (score) this.#run(() => change(score), true);
        },
      }),
    );
    this.#drums = new DrumsView(scoreDrums(editing));
    this.#rack = new RackView({
      score: () => this.#draft.score,
      specs: () => this.#draft.instruments,
      seed: () => this.#seed(),
      section: () => this.block()?.section ?? null,
      wants: () => (this.#lane === "drums" ? "drum" : this.#lane === "bass" ? "note" : null),
      edit: (next, live) => this.editPattern(next, { live: live === true }),
      notes: (pattern, sound, open) => {
        this.#notes = { pattern, sound };
        if (open) host.openTab("roll");
      },
      say: (text) => host.say(text),
      preview: (sound, pattern) => this.#preview(sound, pattern),
      previewPick: (pick, root) => this.#previewPick(pick, root),
      switched: () => {
        this.#retarget();
        host.rackSwitched?.();
      },
      confirm: (text) => host.confirm(text),
      pack: () => this.#packList(),
    });
    this.#source = new SourceNotes(host.flipApi, () => host.flipDraft.flush(), () => host.repaint());
    const flipEditing = {
      doc: () => host.flipDraft.doc,
      apply: (op: Parameters<FlipDraft["apply"]>[0]) => host.flipDraft.apply(op),
      block: () => this.block(),
    };
    this.#flipRoll = new RollView(new FlipRoll({ ...flipEditing, source: () => this.#source.get(host.flipDraft.doc) }));
    this.#flipDrums = new DrumsView(flipDrums(flipEditing));
    this.#fx = new FxPane({
      base: host.base,
      token: host.token,
      facts: () => host.facts(),
      jobs: () => host.jobs(),
      studio: () => host.studio(),
      startJob: (kind) => host.startJob(kind),
      flip: () => host.flip(),
      flipApi: host.flipApi,
      flipDraft: host.flipDraft,
      flipBlock: () => (host.facts().beatKind === "flip" ? (this.block()?.section ?? null) : null),
      hasText: () => hasText(host.reply()),
      confirm: (text) => host.confirm(text),
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

  // the instruments the engine plays, empty until the score is loaded
  get instruments(): readonly InstrumentSpec[] {
    return this.#draft.instruments;
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

  // a roll note is picked, so Delete is the roll's and not the timeline's
  get notePicked(): boolean {
    return this.#flipRoll.hasPick || this.#roll.hasPick;
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

  // the block the editor was pointed at while it still exists; no playhead fallback
  ownBlock(): number | null {
    const at = this.#block;
    return at !== null && this.#host.clips().some((c) => c.index === at && c.bars >= 1) ? at : null;
  }

  // a block picked on the timeline points the rack and the roll at what it plays
  selectBlock(index: number): void {
    this.#block = index;
    this.#notes = null;
    this.#rack.follow();
    this.#source.retry();
    if (this.patternPlaying) this.#retarget();
    else if (this.#auditioning) void this.#startAudition();
  }

  // the pattern the rack shows, null before the score is loaded
  get rackPattern(): string | null {
    return this.#rack.current();
  }

  // a pattern's lane opened from its head: the rack shows it, the block stays
  showPattern(pattern: string): void {
    this.#notes = null;
    this.#rack.choose(pattern);
    if (this.patternPlaying) this.#retarget();
  }

  // a clip picked in the playlist: the editor works on its section, the rack shows its pattern
  selectClip(pattern: string, section: number): void {
    this.#block = section;
    this.#notes = null;
    this.#rack.choose(pattern);
    this.#source.retry();
    if (this.patternPlaying) this.#retarget();
    else if (this.#auditioning) void this.#startAudition();
  }

  // One paint gesture over the playlist: the sections given get the pattern, one undo step for
  // all of them; a refused section is said and left as it was. Null before the score is loaded.
  paintPattern(pattern: string, sections: readonly number[]): PaintPlan | null {
    const score = this.#draft.score;
    if (!score) return null;
    const plan = paintSections(score, pattern, sections, true);
    if (plan.refused.length) this.#host.say(plan.refused.map((r) => r.reason).join(" "));
    if (plan.painted.length) this.editPattern(plan.score);
    return plan;
  }

  // the clip of a pattern in a section taken out; false when there was none
  clearClip(pattern: string, section: number): boolean {
    const score = this.#draft.score;
    if (!score) return false;
    const plan = paintSections(score, pattern, [section], false);
    if (plan.refused.length) this.#host.say(plan.refused.map((r) => r.reason).join(" "));
    if (!plan.painted.length) return false;
    return this.editPattern(plan.score);
  }

  // the clip's section gets its own copy of the pattern, which the rack then shows; the new
  // pattern's name, null when refused
  uniqueClip(pattern: string, section: number): string | null {
    const score = this.#draft.score;
    if (!score) return null;
    const made = uniqueClip(score, { pattern, section });
    if (!made.ok) {
      this.#host.say(made.reason);
      return null;
    }
    // picked first: the edit draws the rack again at once
    this.#block = section;
    this.#rack.choose(made.name);
    this.editPattern(made.score);
    return made.name;
  }

  // the own beat's score is read once the beat lane is on screen, so block ops have it
  prepare(): void {
    this.#syncSeed();
    if (this.#host.facts().beatKind === "own" && !this.#draft.score) void this.#draft.load();
  }

  // An edit of the whole score, such as a section move: it goes into the same draft and history
  // as the roll's. False when the score is not loaded or a question is open.
  editScore(next: Score): boolean {
    if (!this.#draft.score || this.#ask) return false;
    this.#answered.clear();
    this.#apply(next, true);
    return true;
  }

  // An edit of a pattern wherever it plays, from the rack or the roll of a picked sound: it asks
  // nothing, and a question still open is dropped. `live`: a step of a drag, heard at once but
  // neither saved nor an undo step; the drag ends with the same call without it.
  editPattern(next: Score, opts: { live?: boolean } = {}): boolean {
    if (!this.#draft.score) return false;
    this.#ask = null;
    if (opts.live) {
      this.#sendLive(next);
      return true;
    }
    this.#apply(next, true);
    return true;
  }

  // the song's playback took over
  auditionOff(): void {
    const was = this.#auditioning;
    this.#auditioning = false;
    this.#paintAudition();
    if (was) this.#host.liveChanged?.();
  }

  // ---------- pattern mode ----------

  get patternMode(): boolean {
    return this.#patternMode;
  }

  get patternPlaying(): boolean {
    return this.#auditioning && this.#live === "pattern";
  }

  // song mode stops a pattern that plays; the song's playback is the screen's to stop
  setPatternMode(on: boolean): void {
    if (on === this.#patternMode) return;
    this.#patternMode = on;
    if (!on && this.patternPlaying) void this.#stopLive();
  }

  // the transport's play in pattern mode: the rack's pattern alone, round and round, or a stop
  async togglePattern(): Promise<void> {
    const pb = this.#host.playback();
    if (!pb) return;
    if (this.patternPlaying) return this.#stopLive();
    const score = this.#draft.score;
    if (!score) {
      this.prepare();
      this.#host.say("Партитура ещё загружается.");
      return;
    }
    const preview = this.#patternPreview(score);
    if (!preview) {
      this.#host.say("В бите нет паттерна, чтобы его играть.");
      return;
    }
    this.#host.stopSong();
    this.#auditioning = true;
    this.#live = "pattern";
    this.#paintAudition();
    this.#host.liveChanged?.();
    const seq = ++this.#auditionSeq;
    try {
      const pattern = this.#rack.current(score);
      await pb.audition(preview.score, 0, preview.bars);
      // a switch or an edit made while the engine started went nowhere: the latest pattern now
      const moved = this.#draft.score !== score || this.#rack.current(this.#draft.score ?? score) !== pattern;
      if (seq === this.#auditionSeq && this.patternPlaying && moved) this.#retarget();
    } catch (error) {
      if (seq !== this.#auditionSeq) return;
      this.#auditioning = false;
      this.#paintAudition();
      this.#host.liveChanged?.();
      this.#host.say(`Паттерн не играет: ${message(error)}`);
    }
  }

  // Lights the rack's playing step and turns its page to the playing bar. `songBars`: where the
  // song plays, null when it does not; the live engine, when it plays, goes first.
  paintCursor(songBars: number | null): void {
    const score = this.#draft.score;
    const pattern = this.#rack.shown;
    const live = this.#auditioning ? (this.#host.playback()?.auditionPosition() ?? null) : null;
    const playing = live !== null || songBars !== null;
    if (this.#tab !== "rack" || !score || pattern === null) {
      // a play started or stopped while the rack is away still counts for its pager
      this.#rack.playing(playing);
      return;
    }
    let at: StepAt | null = null;
    if (live !== null) {
      const bars = (live * score.bpm) / 240;
      at = stepAt(this.#live === "pattern" ? loopedScore(score, pattern) : score, pattern, bars);
    } else if (songBars !== null) at = stepAt(score, pattern, songBars);
    this.#rack.cursor(at, playing);
  }

  #patternPreview(score: Score): Preview | null {
    const pattern = this.#rack.current(score);
    return pattern === null ? null : patternPreview(score, pattern);
  }

  async #stopLive(): Promise<void> {
    this.#auditioning = false;
    this.#paintAudition();
    this.#host.liveChanged?.();
    await this.#host.playback()?.stopAudition();
  }

  // the rack shows another pattern: a pattern that plays follows it
  #retarget(): void {
    const score = this.#draft.score;
    if (!this.patternPlaying || !score) return;
    const preview = this.#patternPreview(score);
    if (!preview) {
      void this.#stopLive();
      return;
    }
    this.#host.playback()?.retargetAudition(preview.score, 0, preview.bars);
  }

  // an edit heard at once: the pattern alone in pattern mode, else the whole song
  #sendLive(next: Score): void {
    const pb = this.#host.playback();
    if (!pb) return;
    if (!this.patternPlaying) {
      pb.updateAudition(next);
      return;
    }
    const preview = this.#patternPreview(next);
    if (preview) pb.retargetAudition(preview.score, 0, preview.bars);
  }

  // ---------- hearing one sound ----------

  #preview(sound: string, pattern: string | null): void {
    const score = this.#draft.score;
    const hit = score ? hitScore(score, this.#draft.instruments, sound, pattern) : null;
    if (hit) void this.#hit(hit);
  }

  #previewPick(pick: Pick, root: number | null): void {
    const score = this.#draft.score;
    if (!score) return;
    // heard alone: a full score or another sound's mix under the same name has no say
    const bare: Score = { ...score, sounds: {} };
    delete bare.mix;
    const made = addPicked(bare, this.#draft.instruments, null, pick);
    if (!made.ok) return this.#host.say(made.reason);
    const hit = hitScore(made.score, this.#draft.instruments, made.name, null, root ?? undefined);
    if (hit) void this.#hit(hit);
  }

  async #hit(score: Score): Promise<void> {
    try {
      await this.#host.playback()?.hit(score);
    } catch (error) {
      this.#host.say(`Звук не играет: ${message(error)}`);
    }
  }

  // asked once; a failed read is asked again at the next menu
  #packList(): Promise<PackList> {
    if (!this.#pack) {
      this.#pack = this.#host.beatApi.pack();
      this.#pack.catch(() => (this.#pack = null));
    }
    return this.#pack;
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

  // Ctrl+Z / Ctrl+Y for the editor; false when it has nothing to take. An own beat's score
  // history answers whichever tab is open.
  undo(redo: boolean): boolean {
    const past = this.#history;
    if (this.#host.facts().beatKind === "own" && past && this.#draft.score) {
      const to = redo ? past.redo() : past.undo();
      if (to) {
        this.#apply(to, false);
        return true;
      }
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

  #seed(): number | null {
    const b = this.#host.reply().stations.beat;
    return b.kind === "own" && typeof b.seed === "number" ? b.seed : null;
  }

  #syncSeed(): void {
    if (!this.#draft.reset(this.#seed())) return;
    this.#history = null;
    this.#answered.clear();
    this.#ask = null;
    this.#block = null;
    this.#notes = null;
    this.#rack.reset();
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

  #run(make: () => Score, wide = false): void {
    let next: Score;
    try {
      next = make();
    } catch (error) {
      this.#host.say(error instanceof ModelError ? `Правка не встала: ${error.message}` : message(error));
      this.#host.repaint();
      return;
    }
    if (wide) this.editPattern(next);
    else this.#apply(next, true);
  }

  #apply(next: Score, push: boolean): void {
    this.#draft.apply(next);
    if (push) this.#history?.push(next);
    this.#sendLive(next);
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
      case "rack":
        this.#rack.show();
        return [this.#rack.root];
      case "roll": {
        const view = this.#host.facts().beatKind === "flip" ? this.#flipRoll : this.#roll;
        view.show();
        return [this.#askBar(), view.root].filter((n): n is HTMLElement => n !== null);
      }
      case "drums": {
        const view = this.#host.facts().beatKind === "flip" ? this.#flipDrums : this.#drums;
        view.show();
        return [this.#askBar(), view.root].filter((n): n is HTMLElement => n !== null);
      }
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

  #flipLayer(): boolean {
    const f = this.#host.facts();
    const doc = this.#host.flipDraft.doc;
    return f.beatKind === "flip" && doc !== null && layerProblem(doc) === null && (this.#tab === "roll" || this.#tab === "drums");
  }

  #paintAudition(): void {
    const f = this.#host.facts();
    const inBeat = this.#lane !== null && BEAT_LANES.includes(this.#lane);
    const flip = f.beatKind === "flip";
    const show = flip ? inBeat && this.#flipLayer() : f.beatKind === "own" && inBeat && this.#draft.score !== null;
    if (flip && !show && this.#auditioning) {
      this.#auditioning = false;
      void this.#host.playback()?.stopAudition();
    }
    this.audition.hidden = !show;
    this.audition.setAttribute("aria-pressed", String(this.#auditioning));
    this.audition.replaceChildren(icon(this.#auditioning ? STOP : PLAY, "eplay__icon"));
    const on = flip ? "Послушать блок по кругу: только наш слой, без исходника" : "Послушать кусок по кругу, с правками";
    this.audition.title = this.#auditioning ? "Остановить" : on;
    this.audition.setAttribute("aria-label", this.audition.title);
  }

  async #toggleAudition(): Promise<void> {
    const pb = this.#host.playback();
    if (!pb) return;
    if (this.#auditioning) return this.#stopLive();
    await this.#startAudition();
  }

  #flipKey(): string | null {
    const doc = this.#host.flipDraft.doc;
    const at = this.block();
    const block = doc && at ? doc.arrangement[at.section] : undefined;
    return doc && block ? JSON.stringify([block, doc.grid.bpm, doc.grid.swing]) : null;
  }

  // the block's ours layer as a beat score: the server builds it from the block being edited
  async #flipScore(): Promise<{ score: Score; bars: number } | null> {
    const doc = this.#host.flipDraft.doc;
    const at = this.block();
    const block = doc && at ? doc.arrangement[at.section] : undefined;
    if (!doc || !block) return null;
    this.#scoreKey = this.#flipKey();
    const score = await this.#host.flipApi.blockScore(block, { bpm: doc.grid.bpm, swing: doc.grid.swing });
    return { score, bars: block.to_bar - block.from_bar };
  }

  // a flip edit while the block plays: the layer is asked for again and swapped in
  flipEdited(): void {
    if (!this.#auditioning || this.#host.facts().beatKind !== "flip") return;
    if (this.#flipKey() === this.#scoreKey) return;
    const seq = ++this.#auditionSeq;
    void this.#flipScore().then(
      (made) => {
        if (made && seq === this.#auditionSeq && this.#auditioning) this.#host.playback()?.updateAudition(made.score);
      },
      (error: unknown) => this.#host.say(`Слой не обновился: ${message(error)}`),
    );
  }

  async #startAudition(): Promise<void> {
    const pb = this.#host.playback();
    if (!pb) return;
    const flip = this.#host.facts().beatKind === "flip";
    const score = this.#draft.score;
    const block = this.block();
    if (!block || (!flip && !score)) return;
    this.#host.stopSong();
    this.#auditioning = true;
    this.#live = "block";
    this.#paintAudition();
    this.#host.liveChanged?.();
    const seq = ++this.#auditionSeq;
    try {
      if (flip) {
        await this.#host.flipDraft.flush();
        const made = await this.#flipScore();
        if (!made || seq !== this.#auditionSeq || !this.#auditioning) return;
        await pb.audition(made.score, 0, made.bars);
      } else if (score) {
        await pb.audition(score, block.fromBar, block.bars);
        // an edit made while the engine started went nowhere: the latest score now
        const latest = this.#draft.score;
        if (seq === this.#auditionSeq && this.#auditioning && latest && latest !== score) pb.updateAudition(latest);
      }
    } catch (error) {
      this.#auditioning = false;
      this.#paintAudition();
      this.#host.liveChanged?.();
      this.#host.say(`Кусок не играет: ${message(error)}`);
    }
  }
}
