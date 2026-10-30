// Undo and redo over whole snapshots.

export class History<T> {
  #past: T[] = [];
  #future: T[] = [];
  #now: T;
  #key: string;
  readonly #limit: number;

  constructor(first: T, limit = 200) {
    this.#now = structuredClone(first);
    this.#key = JSON.stringify(first);
    this.#limit = limit;
  }

  get current(): T {
    return structuredClone(this.#now);
  }

  get canUndo(): boolean {
    return this.#past.length > 0;
  }

  get canRedo(): boolean {
    return this.#future.length > 0;
  }

  push(next: T): boolean {
    const key = JSON.stringify(next);
    if (key === this.#key) return false;
    this.#past.push(this.#now);
    if (this.#past.length > this.#limit) this.#past.shift();
    this.#now = structuredClone(next);
    this.#key = key;
    this.#future = [];
    return true;
  }

  undo(): T | null {
    const back = this.#past.pop();
    if (back === undefined) return null;
    this.#future.push(this.#now);
    return this.#move(back);
  }

  redo(): T | null {
    const forward = this.#future.pop();
    if (forward === undefined) return null;
    this.#past.push(this.#now);
    return this.#move(forward);
  }

  #move(to: T): T {
    this.#now = to;
    this.#key = JSON.stringify(to);
    return structuredClone(to);
  }
}
