// What a mounted app owns while it lives: listeners, timers and frames, all released by one
// dispose. After dispose every call is a no-op, so a late callback cannot start something new.

export interface ListenTarget {
  addEventListener(type: string, listener: (event: Event) => void, options?: boolean | AddEventListenerOptions): void;
  removeEventListener(type: string, listener: (event: Event) => void, options?: boolean | EventListenerOptions): void;
}

export interface Clock {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(id: unknown): void;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(id: unknown): void;
  requestAnimationFrame(fn: () => void): unknown;
  cancelAnimationFrame(id: unknown): void;
}

const globalClock: Clock = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (id) => globalThis.clearTimeout(id as number),
  setInterval: (fn, ms) => globalThis.setInterval(fn, ms),
  clearInterval: (id) => globalThis.clearInterval(id as number),
  requestAnimationFrame: (fn) => globalThis.requestAnimationFrame(fn),
  cancelAnimationFrame: (id) => globalThis.cancelAnimationFrame(id as number),
};

export class Scope {
  readonly #clock: Clock;
  readonly #cleanups = new Set<() => void>();
  readonly #timers = new Map<number, () => void>();
  #next = 0;
  #closed = false;

  constructor(clock: Clock = globalClock) {
    this.#clock = clock;
  }

  get closed(): boolean {
    return this.#closed;
  }

  // listeners, timers and frames still held
  get size(): number {
    return this.#cleanups.size + this.#timers.size;
  }

  // runs on dispose; returns the way to run it early and forget it
  add(cleanup: () => void): () => void {
    if (this.#closed) {
      cleanup();
      return () => {};
    }
    let done = false;
    const once = (): void => {
      if (done) return;
      done = true;
      this.#cleanups.delete(once);
      cleanup();
    };
    this.#cleanups.add(once);
    return once;
  }

  // the caller names the event type it listens for; the target hands it that event
  listen<T extends Event>(
    target: ListenTarget,
    type: string,
    listener: (event: T) => void,
    options?: boolean | AddEventListenerOptions,
  ): () => void {
    if (this.#closed) return () => {};
    const handler = listener as (event: Event) => void;
    target.addEventListener(type, handler, options);
    return this.add(() => target.removeEventListener(type, handler, options));
  }

  setTimeout(fn: () => void, ms: number): number {
    return this.#timer(
      (id) =>
        this.#clock.setTimeout(() => {
          this.#timers.delete(id);
          fn();
        }, ms),
      (raw) => this.#clock.clearTimeout(raw),
    );
  }

  setInterval(fn: () => void, ms: number): number {
    return this.#timer(() => this.#clock.setInterval(fn, ms), (raw) => this.#clock.clearInterval(raw));
  }

  requestFrame(fn: () => void): number {
    return this.#timer(
      (id) =>
        this.#clock.requestAnimationFrame(() => {
          this.#timers.delete(id);
          fn();
        }),
      (raw) => this.#clock.cancelAnimationFrame(raw),
    );
  }

  clearTimeout(id: number): void {
    this.#release(id);
  }

  clearInterval(id: number): void {
    this.#release(id);
  }

  cancelFrame(id: number): void {
    this.#release(id);
  }

  // resolves after the wait, or at once on dispose
  sleep(ms: number): Promise<void> {
    return new Promise((resolve) => {
      if (this.#closed) return resolve();
      const id = this.setTimeout(resolve, ms);
      this.add(() => {
        this.#release(id);
        resolve();
      });
    });
  }

  dispose(): void {
    if (this.#closed) return;
    this.#closed = true;
    for (const [, cancel] of [...this.#timers]) cancel();
    this.#timers.clear();
    for (const cleanup of [...this.#cleanups]) cleanup();
    this.#cleanups.clear();
  }

  #timer(start: (id: number) => unknown, stop: (raw: unknown) => void): number {
    if (this.#closed) return 0;
    const id = ++this.#next;
    const raw = start(id);
    this.#timers.set(id, () => stop(raw));
    return id;
  }

  #release(id: number): void {
    const cancel = this.#timers.get(id);
    if (!cancel) return;
    this.#timers.delete(id);
    cancel();
  }
}

// The keyboard of one app: attached to the document only while the app is the active surface.
export class KeySurface {
  readonly #doc: ListenTarget;
  readonly #handler: (event: KeyboardEvent) => void;
  #off: (() => void) | null = null;

  constructor(doc: ListenTarget, handler: (event: KeyboardEvent) => void) {
    this.#doc = doc;
    this.#handler = handler;
  }

  get active(): boolean {
    return this.#off !== null;
  }

  setActive(on: boolean): void {
    if (on === this.active) return;
    if (on) {
      const handler = this.#handler as (event: Event) => void;
      this.#doc.addEventListener("keydown", handler);
      this.#off = () => this.#doc.removeEventListener("keydown", handler);
    } else {
      this.#off?.();
      this.#off = null;
    }
  }
}
