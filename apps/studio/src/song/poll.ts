// A poll that runs only while it is wanted and the tab is visible. One timer at most: the
// next wait starts when the tick has finished, so a slow answer never piles requests up.

export interface PollClock {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
}

export const realClock: PollClock = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => globalThis.clearTimeout(id as number),
};

export interface PollOptions {
  ms: number;
  tick: () => Promise<void> | void;
  clock?: PollClock;
}

export class Poller {
  readonly #ms: number;
  readonly #tick: () => Promise<void> | void;
  readonly #clock: PollClock;
  #wanted = false;
  #visible = true;
  #timer: unknown = null;
  #inFlight = false;
  #closed = false;

  constructor({ ms, tick, clock }: PollOptions) {
    this.#ms = ms;
    this.#tick = tick;
    this.#clock = clock ?? realClock;
  }

  get running(): boolean {
    return !this.#closed && this.#wanted && this.#visible;
  }

  // whether a timer is waiting (for tests)
  get waiting(): boolean {
    return this.#timer !== null;
  }

  // the station (or the work) that needs the poll is on screen / has something to watch
  setWanted(on: boolean): void {
    this.#wanted = on;
    this.#sync();
  }

  // the tab is visible
  setVisible(on: boolean): void {
    this.#visible = on;
    this.#sync();
  }

  dispose(): void {
    this.#closed = true;
    this.#sync();
  }

  #sync(): void {
    if (!this.running) {
      if (this.#timer !== null) this.#clock.clearTimeout(this.#timer);
      this.#timer = null;
      return;
    }
    if (this.#timer === null && !this.#inFlight) this.#arm(0);
  }

  #arm(ms: number): void {
    this.#timer = this.#clock.setTimeout(() => {
      this.#timer = null;
      void this.#run();
    }, ms);
  }

  async #run(): Promise<void> {
    if (!this.running || this.#inFlight) return;
    this.#inFlight = true;
    try {
      await this.#tick();
    } catch {
      // a failed poll is tried again on the next beat
    } finally {
      this.#inFlight = false;
    }
    if (this.running && this.#timer === null) this.#arm(this.#ms);
  }
}
