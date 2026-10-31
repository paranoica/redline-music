// Saves that follow edits: a value waits `delay` ms of quiet, one save is out at a time, and the
// newest value wins over any that came before it.

export type SaveState = "idle" | "waiting" | "saving" | "saved" | "failed";

export interface Timer {
  set(fn: () => void, ms: number): number;
  clear(id: number): void;
}

const windowTimer: Timer = {
  set: (fn, ms) => setTimeout(fn, ms) as unknown as number,
  clear: (id) => clearTimeout(id),
};

export interface QueueOptions<T> {
  send: (value: T) => Promise<void>;
  delay: number;
  timer?: Timer;
  onState?: (state: SaveState, error?: unknown) => void;
}

export class SaveQueue<T> {
  readonly #send: (value: T) => Promise<void>;
  readonly #delay: number;
  readonly #timer: Timer;
  readonly #onState: (state: SaveState, error?: unknown) => void;
  #latest: { value: T } | null = null;
  #wait: number | null = null;
  // the delay of the newest value ran out while a save was out
  #due = false;
  #flight: Promise<void> | null = null;
  #state: SaveState = "idle";
  #closed = false;

  constructor(options: QueueOptions<T>) {
    this.#send = options.send;
    this.#delay = options.delay;
    this.#timer = options.timer ?? windowTimer;
    this.#onState = options.onState ?? (() => {});
  }

  get state(): SaveState {
    return this.#state;
  }

  // a value the server does not have yet
  get pending(): boolean {
    return this.#latest !== null || this.#flight !== null;
  }

  push(value: T): void {
    if (this.#closed) return;
    this.#latest = { value };
    this.#due = false;
    this.#stopWait();
    this.#wait = this.#timer.set(() => {
      this.#wait = null;
      this.#due = true;
      this.#pump();
    }, this.#delay);
    if (!this.#flight) this.#set("waiting");
  }

  // sends what is waiting at once; true when the server has the newest value
  async flush(): Promise<boolean> {
    this.#stopWait();
    if (this.#latest) this.#due = true;
    this.#pump();
    while (this.#flight) {
      await this.#flight;
      if (this.#state === "failed" && !this.#due) break;
      this.#pump();
    }
    return this.#latest === null && this.#state !== "failed";
  }

  dispose(): void {
    this.#closed = true;
    this.#stopWait();
    this.#latest = null;
  }

  #stopWait(): void {
    if (this.#wait !== null) this.#timer.clear(this.#wait);
    this.#wait = null;
  }

  #pump(): void {
    if (this.#closed || this.#flight || !this.#latest || !this.#due) return;
    const { value } = this.#latest;
    this.#latest = null;
    this.#due = false;
    this.#set("saving");
    this.#flight = this.#send(value).then(
      () => this.#landed(null),
      (error: unknown) => this.#landed({ value, error }),
    );
  }

  #landed(failed: { value: T; error: unknown } | null): void {
    this.#flight = null;
    if (this.#closed) return;
    if (failed && this.#latest === null) {
      // kept for the next flush or edit; no retry on its own
      this.#latest = { value: failed.value };
      this.#set("failed", failed.error);
      return;
    }
    if (this.#latest) {
      if (this.#due || failed) {
        this.#due = true;
        this.#pump();
      } else this.#set("waiting");
      return;
    }
    this.#set("saved");
  }

  #set(state: SaveState, error?: unknown): void {
    this.#state = state;
    this.#onState(state, error);
  }
}
