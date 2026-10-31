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
  #ms: number;
  readonly #tick: () => Promise<void> | void;
  readonly #clock: PollClock;
  #wanted = false;
  #visible = true;
  #timer: unknown = null;
  #armedMs = 0;
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

  // a shorter interval replaces the wait already set; a longer one starts after it
  setMs(ms: number): void {
    if (ms === this.#ms) return;
    this.#ms = ms;
    if (this.#timer !== null && ms < this.#armedMs) {
      this.#clock.clearTimeout(this.#timer);
      this.#arm(ms);
    }
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
    this.#armedMs = ms;
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
