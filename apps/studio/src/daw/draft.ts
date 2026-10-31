// An own beat's working copy on the song screen: the draft the server keeps per seed over the
// newest saved score, saved after a quiet moment, and the commit that saves it as the next
// numbered score and renders it.

import type { Api as BeatApi } from "../beat/api.ts";
import { Refused } from "../beat/api.ts";
import { serialize, type Score } from "../beat/model.ts";
import { SaveQueue, type SaveState, type Timer } from "./queue.ts";
import type { ScreenFacts } from "./view.ts";

const DRAFT_MS = 600;
const RENDER_POLL_MS = 500;
// the draft and the newest score are read side by side; a save between the two is read again
const LOAD_TRIES = 3;

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export function refusal(error: unknown): string {
  if (error instanceof Refused && error.errors.length) return error.errors.slice(0, 3).join("; ");
  return message(error);
}

export type DraftApi = Pick<BeatApi, "draft" | "session" | "saveDraft" | "save" | "render" | "renderStatus">;

export interface DraftHost {
  api: DraftApi;
  facts(): Pick<ScreenFacts, "beatKind" | "beatDirty" | "hasBeatAudio">;
  // a score loaded for the seed; `edited`: it is not the newest saved score
  loaded(score: Score, edited: boolean): void;
  // a load ended, loaded or not
  loadEnded(): void;
  // the song reloaded after a render
  reload(): Promise<void>;
  // the draft's save or the render changed state
  changed(): void;
  say(text: string): void;
  timer?: Timer;
  sleep?: (ms: number) => Promise<void>;
}

type RenderState = { state: string; score_file: string | null; error: string | null };
type Read = { draft: { score: Score; base: string }; session: { score_file: string; score: Score } };

// The working copy to start from, or null when the draft and the newest score were read across
// a save. The newest score's text is what tells an edit from it.
export function startFrom({ draft, session }: Read): { score: Score; base: string; saved: string } | null {
  if (session.score_file !== draft.base) return null;
  return { score: draft.score, base: draft.base, saved: serialize(session.score) };
}

export class BeatDraft {
  readonly #host: DraftHost;
  readonly #queue: SaveQueue<Score>;
  readonly #sleep: (ms: number) => Promise<void>;
  #seed: number | null = null;
  #score: Score | null = null;
  #base: string | null = null;
  // the newest saved score as text, to tell an edit from it
  #saved = "";
  #loading: Promise<void> | null = null;
  #committing: Promise<void> | null = null;
  #rendering = false;
  #renders = 0;
  #state: SaveState = "idle";
  #closed = false;

  constructor(host: DraftHost) {
    this.#host = host;
    this.#sleep = host.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
    const options = {
      delay: DRAFT_MS,
      // the base is read when the draft goes out: a commit may have saved a newer score meanwhile
      send: (score: Score) => {
        const base = this.#base;
        if (!base) return Promise.reject(new Error("партитура не загрузилась"));
        return host.api.saveDraft(score, base).then(() => undefined);
      },
      onState: (state: SaveState, error?: unknown) => {
        this.#state = state;
        if (state === "failed") host.say(`Черновик бита не сохранился: ${refusal(error)}`);
        host.changed();
      },
    };
    this.#queue = new SaveQueue(host.timer ? { ...options, timer: host.timer } : options);
  }

  // an edit the server's newest score does not have, saved or not
  get dirty(): boolean {
    return this.#score !== null && (serialize(this.#score) !== this.#saved || this.#queue.pending);
  }

  get score(): Score | null {
    return this.#score;
  }

  get rendering(): boolean {
    return this.#rendering;
  }

  // the renders a commit waited for, counted before the song reloads with one
  get renders(): number {
    return this.#renders;
  }

  get saveState(): SaveState {
    return this.#state;
  }

  // the seed changed: what was loaded for the old one goes; true when it did change
  reset(seed: number | null): boolean {
    if (seed === this.#seed) return false;
    this.#seed = seed;
    this.#score = null;
    this.#base = null;
    this.#saved = "";
    return true;
  }

  load(): Promise<void> {
    if (this.#loading) return this.#loading;
    const seed = this.#seed;
    const api = this.#host.api;
    this.#loading = (async () => {
      try {
        let start: ReturnType<typeof startFrom> = null;
        for (let i = 0; i < LOAD_TRIES && !start; i++) {
          const [draft, session] = await Promise.all([api.draft(), api.session()]);
          start = startFrom({ draft, session });
        }
        if (!start) throw new Error("партитура менялась, пока я её читал");
        if (this.#closed || seed !== this.#seed) return;
        this.#score = start.score;
        this.#base = start.base;
        this.#saved = start.saved;
        this.#host.loaded(start.score, serialize(start.score) !== start.saved);
        this.#host.changed();
      } catch (error) {
        this.#host.say(`Партитура не загрузилась: ${refusal(error)}`);
      } finally {
        this.#loading = null;
        if (!this.#closed) this.#host.loadEnded();
      }
    })();
    return this.#loading;
  }

  // an edit: the score as it is now, its draft saved after a quiet moment
  apply(score: Score): void {
    this.#score = score;
    if (this.#base) this.#queue.push(score);
  }

  // Saves the edited score as the next numbered one and renders it, then the song reloads.
  // `wait` false: the render is started and left to the server.
  commit(wait = true): Promise<void> {
    if (this.#committing) return this.#committing;
    const run = this.#commit(wait).finally(() => {
      this.#committing = null;
      this.#rendering = false;
      if (!this.#closed) this.#host.changed();
    });
    this.#committing = run;
    return run;
  }

  // the newest draft the server does not have yet, for the request that outlives the page
  unsent(): { score: Score; base: string } | null {
    if (!this.#queue.pending || !this.#score || !this.#base) return null;
    return { score: this.#score, base: this.#base };
  }

  dispose(): void {
    this.#closed = true;
    this.#queue.dispose();
  }

  async #commit(wait: boolean): Promise<void> {
    const facts = this.#host.facts();
    if (facts.beatKind !== "own") return;
    if (!this.#score) await this.load();
    const score = this.#score;
    let base = this.#base;
    if (!score || !base) {
      if (!facts.beatDirty && facts.hasBeatAudio) return;
      throw new Error("партитура не загрузилась");
    }
    if (!(await this.#queue.flush())) throw new Error("черновик не сохранился");
    const api = this.#host.api;
    const text = serialize(score);
    if (text !== this.#saved) {
      try {
        base = (await api.save(score, base)).file;
      } catch (error) {
        throw new Error(`партитура не сохранилась: ${refusal(error)}`);
      }
      this.#base = base;
      this.#saved = text;
      // an edit made while the save was out: its draft goes again, over the new score
      const now = this.#score;
      if (now && serialize(now) !== text) this.#queue.push(now);
    } else if (!facts.beatDirty && facts.hasBeatAudio) return;
    this.#rendering = true;
    this.#host.changed();
    let state = await this.#startRender(base);
    if (!wait) return;
    while (state.state !== "done" || state.score_file !== base) {
      if (state.state === "failed") throw new Error(state.error ?? "рендер не прошёл");
      if (state.state === "idle") state = await this.#startRender(base);
      await this.#sleep(RENDER_POLL_MS);
      if (this.#closed) return;
      state = await api.renderStatus();
      if (state.state === "done" && state.score_file !== base) state = await this.#startRender(base);
    }
    this.#renders += 1;
    // `rendering` drops in commit's finally, once the song has reloaded with the render
    await this.#host.reload();
  }

  async #startRender(file: string): Promise<RenderState> {
    try {
      return await this.#host.api.render(file);
    } catch (error) {
      // a render already running: wait for it, then ours
      if (error instanceof Refused && error.status === 409) return { state: "running", score_file: null, error: null };
      throw error;
    }
  }
}
