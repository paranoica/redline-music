// The editor's message flow, apart from the page so it runs in node: the live sender, the
// delayed check and the playhead's animation loop.

// sends the newest value only: one in flight, the latest waiting
export class Latest<T> {
  #busy = false;
  #next: T | null = null;
  readonly #send: (value: T) => Promise<void>;

  constructor(send: (value: T) => Promise<void>) {
    this.#send = send;
  }

  push(value: T): void {
    if (this.#busy) {
      this.#next = value;
      return;
    }
    this.#busy = true;
    void this.#send(value).finally(() => {
      this.#busy = false;
      const next = this.#next;
      this.#next = null;
      if (next !== null) this.push(next);
    });
  }

  // swaps the waiting value only; one already sent stays as it went
  replacePending(value: T): void {
    if (this.#next !== null) this.#next = value;
  }
}

export type Outcome<R> = { ok: true; value: R } | { ok: false; error: unknown };

export interface CheckDeps<S, R> {
  run: (score: S) => Promise<R>;
  apply: (score: S, outcome: Outcome<R>) => void;
  setTimer: (fn: () => void, wait: number) => number;
  clearTimer: (id: number) => void;
}

// checks the score a while after the last edit; a reply for an older score is dropped
export class Checker<S, R> {
  readonly #deps: CheckDeps<S, R>;
  #timer = 0;
  #seq = 0;

  constructor(deps: CheckDeps<S, R>) {
    this.#deps = deps;
  }

  schedule(current: () => S, wait: number): void {
    // a check already out is for an older score from here on
    this.#seq++;
    this.#deps.clearTimer(this.#timer);
    this.#timer = this.#deps.setTimer(() => void this.#run(current()), wait);
  }

  async #run(score: S): Promise<void> {
    const seq = this.#seq;
    let outcome: Outcome<R>;
    try {
      outcome = { ok: true, value: await this.#deps.run(score) };
    } catch (error) {
      outcome = { ok: false, error };
    }
    if (seq !== this.#seq) return;
    this.#deps.apply(score, outcome);
  }
}

// one animation loop at a time; a frame returning false ends it
export class FrameLoop {
  readonly #raf: (fn: () => void) => number;
  #id: number | null = null;

  constructor(raf: (fn: () => void) => number) {
    this.#raf = raf;
  }

  start(frame: () => boolean): void {
    if (this.#id !== null) return;
    const run = (): void => {
      this.#id = null;
      if (frame()) this.#id = this.#raf(run);
    };
    run();
  }
}
