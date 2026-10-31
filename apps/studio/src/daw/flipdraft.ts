// A flip's working copy on the song screen. It is the one owner of the FlipDoc: edits go through
// `apply` into one undo history, the draft is saved over its revision after a quiet moment, and a
// commit renders the draft as a new version, waits for it and makes it the song's beat.

import { History } from "../core/history.ts";
import { isBusy, isStale, type FlipApi, type FlipState } from "../flip/api.ts";
import { draftProblem, finishedSince, normalisePatterns, type BlockEdit, type FlipDoc, type Layout, type Refusal } from "../flip/model.ts";
import { ApiError } from "../song/api.ts";
import { jobActive } from "../song/jobs.ts";
import { SaveQueue, type SaveState, type Timer } from "./queue.ts";

const DRAFT_MS = 600;
const POLL_MS = 500;
const MAX_POLLS = 1200;
// a commit renders again while edits made during a render are left behind
const ROUNDS = 3;
const BUSY_TRIES = 3;

export const STALE_TEXT = "Флип изменён в другой вкладке";

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export class StaleDraft extends Error {
  constructor() {
    super(STALE_TEXT);
  }
}

export type FlipDraftApi = Pick<FlipApi, "state" | "saveDraft" | "render" | "use" | "rev" | "jobs" | "beacon">;

export interface FlipDraftHost {
  api: FlipDraftApi;
  // finished takes of the song, which a new version of the beat changes the meaning of
  takes(): number;
  confirm(text: string): boolean;
  recording(): boolean;
  // the song changed on the server (a new beat, a draft read again): read it
  reload(): Promise<void>;
  changed(): void;
  say(text: string): void;
  timer?: Timer;
  sleep?: (ms: number) => Promise<void>;
}

export function editConfirm(takes: number): string {
  const what = takes === 1 ? "дубль останется записанным" : takes < 5 ? "дубля останутся записанными" : "дублей останутся записанными";
  return `Флип пересоберётся новой версией. ${takes} ${what} под прошлую. Продолжить?`;
}

// the arrangement of a doc as the block ops take it
export function flipLayout(doc: FlipDoc): Layout {
  return { blocks: doc.arrangement, patterns: doc.hats.patterns, effects: doc.effects, bpm: doc.grid.bpm };
}

// the doc with the arrangement, hat patterns and effects of a block op
export function withBlocks(doc: FlipDoc, edit: BlockEdit): FlipDoc {
  return { ...doc, arrangement: edit.blocks, hats: { ...doc.hats, patterns: edit.patterns }, effects: edit.effects ?? doc.effects };
}

export type FlipOp = (doc: FlipDoc) => FlipDoc | Refusal | null;

const keyOf = (doc: FlipDoc): string => JSON.stringify(doc);

const owners = new WeakMap<object, FlipDraft>();

// the draft that works through this api client
export function draftOf(api: object): FlipDraft | undefined {
  return owners.get(api);
}

export class FlipDraft {
  readonly #host: FlipDraftHost;
  readonly #sleep: (ms: number) => Promise<void>;
  #queue: SaveQueue<FlipDoc>;
  #doc: FlipDoc | null = null;
  #rev = 0;
  #history: History<FlipDoc> | null = null;
  // the doc whose render is the song's beat; null when it is not known to be one
  #clean: string | null = null;
  // a render that failed: nothing is committed again until the next edit
  #failed = false;
  // the version made for a doc and whether it has finished
  #made: { n: number; key: string; done: boolean } | null = null;
  #asked = false;
  #committing: Promise<void> | null = null;
  #rendering = false;
  #epoch = 0;
  #state: SaveState = "idle";
  #closed = false;

  constructor(host: FlipDraftHost) {
    this.#host = host;
    this.#sleep = host.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    this.#queue = this.#newQueue();
    owners.set(host.api, this);
  }

  get doc(): FlipDoc | null {
    return this.#doc;
  }

  get rev(): number {
    return this.#rev;
  }

  // edits the song's beat does not have, saved or not
  get dirty(): boolean {
    return this.#doc !== null && (this.#queue.pending || keyOf(this.#doc) !== this.#clean);
  }

  // dirty, and not an edit whose render already failed
  get wantsCommit(): boolean {
    return this.dirty && !this.#failed;
  }

  get rendering(): boolean {
    return this.#rendering;
  }

  get saveState(): SaveState {
    return this.#state;
  }

  get canUndo(): boolean {
    return this.#history?.canUndo ?? false;
  }

  get canRedo(): boolean {
    return this.#history?.canRedo ?? false;
  }

  // the words on the beat lane, null when there are none
  line(): string | null {
    if (this.#rendering) return "бит обновляется…";
    if (this.#state === "failed") return "черновик не сохранён";
    return null;
  }

  // the server's draft as the screen read it; `dirty`: the song's beat is not that draft. It
  // replaces the held doc only when this page has no edit the server lacks.
  adopt(state: FlipState, dirty: boolean): void {
    const draft = state.draft;
    if (!draft || this.#closed) return;
    if (this.#doc && this.#queue.pending) return;
    const incoming = normalisePatterns(draft);
    const key = keyOf(incoming);
    const changed = this.#doc === null || keyOf(this.#doc) !== key;
    this.#doc = incoming;
    this.#rev = state.draft_rev;
    if (changed) {
      this.#history = new History<FlipDoc>(incoming);
      this.#failed = false;
      this.#clean = dirty ? null : key;
    } else if (!dirty) this.#clean = key;
  }

  async load(dirty = false): Promise<void> {
    this.adopt(await this.#host.api.state(), dirty);
  }

  // An edit: `op` gets a copy of the doc and answers the next one, a refusal with its words, or
  // null for nothing to do. True when the edit was made.
  apply(op: FlipOp): boolean {
    const doc = this.#doc;
    if (!doc) {
      this.#host.say("Флип ещё не загрузился.");
      return false;
    }
    const next = op(structuredClone(doc));
    if (next === null) return false;
    if ("ok" in next) {
      this.#host.say(next.reason);
      return false;
    }
    if (keyOf(next) === keyOf(doc)) return false;
    const problem = draftProblem(next);
    if (problem !== null && draftProblem(doc) === null) {
      this.#host.say(problem);
      return false;
    }
    if (!this.#confirmed()) return false;
    this.#set(next);
    this.#history?.push(next);
    return true;
  }

  // one step back or forward in the history; true when the doc moved
  undo(redo: boolean): boolean {
    const to = redo ? this.#history?.redo() : this.#history?.undo();
    if (!to) return false;
    this.#set(to);
    return true;
  }

  // sends what is waiting; true when the server has the newest doc
  flush(): Promise<boolean> {
    return this.#queue.flush();
  }

  // The newest draft the server does not have, for the request that outlives the page.
  unsent(): { flip: FlipDoc; base: number } | null {
    if (!this.#queue.pending || !this.#doc) return null;
    return { flip: this.#doc, base: this.#rev };
  }

  leave(): void {
    const unsent = this.unsent();
    if (unsent) this.#host.api.beacon(unsent.flip, unsent.base);
  }

  async dispose(): Promise<void> {
    await this.#queue.flush().catch(() => false);
    this.#closed = true;
    this.#queue.dispose();
  }

  // Renders the draft as a new version and makes it the song's beat, then the song reloads. A
  // failure is thrown once and holds back further commits until the next edit.
  commit(): Promise<void> {
    if (this.#committing) return this.#committing;
    const run = this.#run().finally(() => {
      this.#committing = null;
      this.#rendering = false;
      if (!this.#closed) this.#host.changed();
    });
    this.#committing = run;
    return run;
  }

  // ---------- edits ----------

  #confirmed(): boolean {
    if (this.#asked) return true;
    const takes = this.#host.takes();
    if (takes > 0 && !this.#host.confirm(editConfirm(takes))) return false;
    this.#asked = true;
    return true;
  }

  #set(next: FlipDoc): void {
    this.#doc = next;
    this.#failed = false;
    this.#queue.push(next);
    this.#host.changed();
  }

  #newQueue(): SaveQueue<FlipDoc> {
    const options = {
      delay: DRAFT_MS,
      send: async (doc: FlipDoc) => {
        try {
          this.#rev = await this.#host.api.saveDraft(doc, this.#rev);
        } catch (error) {
          if (!isStale(error)) throw error;
          await this.#stale();
        }
      },
      onState: (state: SaveState, error?: unknown) => {
        this.#state = state;
        if (state === "failed") this.#host.say(`Черновик флипа не сохранился: ${message(error)}`);
        this.#host.changed();
      },
    };
    return new SaveQueue(this.#host.timer ? { ...options, timer: this.#host.timer } : options);
  }

  // another writer saved the draft first: what this page holds is dropped and the server's read
  async #stale(): Promise<void> {
    this.#epoch += 1;
    this.#queue.dispose();
    this.#queue = this.#newQueue();
    this.#doc = null;
    this.#history = null;
    this.#made = null;
    this.#failed = false;
    this.#host.say(STALE_TEXT);
    await this.#host.reload();
  }

  // ---------- commit ----------

  async #run(): Promise<void> {
    for (let round = 0; round < ROUNDS && this.wantsCommit; round++) {
      this.#rendering = true;
      this.#host.changed();
      try {
        if ((await this.#once()) === "held") return;
      } catch (error) {
        if (error instanceof StaleDraft) throw error;
        this.#failed = true;
        if (!this.#made?.done) this.#made = null;
        throw error;
      }
    }
  }

  async #once(): Promise<"used" | "held"> {
    const made = await this.#version();
    if (this.#host.recording()) return "held";
    let rev = await this.#host.api.rev();
    try {
      await this.#host.api.use(made.n, rev);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 412)) throw error;
      if (this.#host.recording()) return "held";
      rev = await this.#host.api.rev();
      await this.#host.api.use(made.n, rev);
    }
    this.#clean = made.key;
    this.#made = null;
    await this.#host.reload();
    return "used";
  }

  // The finished render of the newest draft: the one already made for it, else a new one. A
  // render that is running is waited for first, and then the newest draft is rendered.
  async #version(): Promise<{ n: number; key: string }> {
    for (let tries = 0; tries < BUSY_TRIES; tries++) {
      const epoch = this.#epoch;
      await this.#saved();
      const doc = this.#doc;
      if (this.#epoch !== epoch || !doc) throw new StaleDraft();
      const key = keyOf(doc);
      if (this.#made?.key === key && this.#made.done) return this.#made;
      const stamp = await this.#stamp();
      let made: { n: number; key: string; done: boolean };
      try {
        made = { n: await this.#host.api.render(this.#rev), key, done: false };
        this.#made = made;
      } catch (error) {
        if (isStale(error)) {
          await this.#stale();
          throw new StaleDraft();
        }
        if (!isBusy(error)) throw error;
        await this.#idle();
        continue;
      }
      await this.#wait(stamp);
      made.done = true;
      return made;
    }
    throw new Error("сборка флипа занята другой задачей");
  }

  async #saved(): Promise<void> {
    for (let i = 0; i < 4; i++) {
      if (await this.#queue.flush()) return;
      if (this.#queue.state === "failed") break;
    }
    throw new Error("черновик флипа не сохранился");
  }

  async #renderJob() {
    return (await this.#host.api.jobs()).find((j) => j.kind === "flip_render");
  }

  async #stamp(): Promise<number | null> {
    return (await this.#renderJob())?.finished ?? null;
  }

  async #idle(): Promise<void> {
    for (let i = 0; i < MAX_POLLS; i++) {
      if (!jobActive(await this.#renderJob())) return;
      await this.#sleep(POLL_MS);
    }
    throw new Error("сборка флипа не кончается");
  }

  // until the render started after `stamp` is over
  async #wait(stamp: number | null): Promise<void> {
    for (let i = 0; i < MAX_POLLS; i++) {
      await this.#sleep(POLL_MS);
      if (this.#closed) throw new Error("страница закрыта");
      const job = await this.#renderJob();
      if (jobActive(job) || !job || !finishedSince(stamp, job)) continue;
      if (job.state === "done") return;
      throw new Error(job.reason ?? "сборка не прошла");
    }
    throw new Error("сборка флипа не ответила");
  }
}
