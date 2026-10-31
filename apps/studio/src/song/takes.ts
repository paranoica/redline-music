// One take at a time, auditioned over the beat.

// plays one take at a time from a blob; the previous url is revoked before the next
export class TakePlayer {
  #audio: HTMLAudioElement | null = null;
  #url: string | null = null;
  #name: string | null = null;
  #wait = 0;
  readonly #fetch: (name: string) => Promise<Blob>;
  readonly #changed: () => void;

  constructor(fetchTake: (name: string) => Promise<Blob>, changed: () => void) {
    this.#fetch = fetchTake;
    this.#changed = changed;
  }

  get name(): string | null {
    return this.#name;
  }

  get time(): { now: number; total: number } {
    return { now: this.#audio?.currentTime ?? 0, total: this.#audio?.duration || 0 };
  }

  async toggle(name: string): Promise<void> {
    if (this.#name === name) {
      this.stop();
      return;
    }
    this.stop();
    const mine = ++this.#wait;
    this.#name = name;
    this.#changed();
    try {
      const blob = await this.#fetch(name);
      if (mine !== this.#wait) return;
      this.#url = URL.createObjectURL(blob);
      const audio = new Audio(this.#url);
      this.#audio = audio;
      audio.addEventListener("ended", () => {
        if (this.#audio === audio) this.stop();
      });
      audio.addEventListener("timeupdate", this.#changed);
      await audio.play();
    } catch {
      if (mine === this.#wait) this.stop();
    }
  }

  stop(): void {
    this.#wait += 1;
    if (this.#audio) {
      this.#audio.pause();
      this.#audio.removeAttribute("src");
      this.#audio.load();
    }
    if (this.#url) URL.revokeObjectURL(this.#url);
    this.#audio = null;
    this.#url = null;
    const was = this.#name;
    this.#name = null;
    if (was !== null) this.#changed();
  }
}
