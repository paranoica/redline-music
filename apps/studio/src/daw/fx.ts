// The effects tab: the simple knobs of the voice or the beat over the studio's settings, «Авто —
// как в референсах», the full chain in a drawer, and a flip's 808 and hats switches.
// A knob is heard at once through the studio player and saved after a quiet moment: the studio
// saves only settings it has rendered exactly, so each save is a render and then the save.

import { createApi as createMixApi } from "../api.ts";
import { apiPath, apiRoot } from "../core/mount.ts";
import type { FlipApi, FlipState } from "../flip/api.ts";
import type { BassMode, FlipDoc, HatsMode } from "../flip/model.ts";
import type { Job, StartKind } from "../song/api.ts";
import { jobActive } from "../song/jobs.ts";
import type { Schema, Settings } from "../types.ts";
import { ChainPanels } from "../ui/chain.ts";
import { h } from "../ui/dom.ts";
import { KnobControl } from "./knob.ts";
import { KNOBS, knobsFromSettings, knobText, settingsFromKnob, type Knob } from "./knobs.ts";
import { SaveQueue, type SaveState } from "./queue.ts";
import type { ScreenFacts } from "./view.ts";

const SAVE_MS = 700;
const FLIP_IDLE_MS = 1500;

const message = (error: unknown): string => (error instanceof Error ? error.message : String(error));

export interface StudioLike {
  readonly ready: boolean;
  applySettings(settings: Settings): void;
}

export interface FxHost {
  base: string | undefined;
  token: string;
  facts(): ScreenFacts;
  jobs(): readonly Job[];
  // the studio backend while it plays the song, else null
  studio(): StudioLike | null;
  startJob(kind: StartKind): Promise<void>;
  flip(): FlipState | null;
  flipApi: FlipApi;
  // the song changed on the server: read it again
  reload(): Promise<void>;
  // a line on the beat lane while a flip rebuilds, null when done
  beatBusy(text: string | null): void;
  say(text: string): void;
  // where the drawer goes
  overlay: HTMLElement;
}

// ---------- the studio's settings ----------

class MixSettings {
  readonly #api: ReturnType<typeof createMixApi>;
  readonly #queue: SaveQueue<Settings>;
  #seq = 0;
  settings: Settings | null = null;
  schema: Schema | null = null;
  state: SaveState = "idle";
  failure: string | null = null;

  constructor(base: string | undefined, token: string, onState: () => void) {
    this.#api = createMixApi({ base: apiPath(apiRoot(base, "/api"), "/mix"), token });
    this.#queue = new SaveQueue<Settings>({
      delay: SAVE_MS,
      send: (s) => this.#save(s),
      onState: (state, error) => {
        this.state = state;
        this.failure = state === "failed" ? message(error) : null;
        onState();
      },
    });
  }

  async load(): Promise<Settings> {
    const session = await this.#api.session();
    const scalars = Object.fromEntries(session.schema.settings.map((s) => [s.key, s.default]));
    this.schema = session.schema;
    this.settings = { version: 2, ...scalars, rack: [], ...session.settings };
    return this.settings;
  }

  set(next: Settings, save: boolean): void {
    this.settings = next;
    if (save) this.#queue.push(structuredClone(next));
  }

  flush(): Promise<boolean> {
    return this.#queue.flush();
  }

  get pending(): boolean {
    return this.#queue.pending;
  }

  close(): void {
    this.#queue.dispose();
    this.#api.close();
  }

  async #save(s: Settings): Promise<void> {
    this.#seq = Math.max(Date.now(), this.#seq + 1);
    await this.#api.render(s, this.#seq, new AbortController().signal);
    await this.#api.save(s);
  }
}

// ---------- the pane ----------

export class FxPane {
  readonly root: HTMLElement;
  readonly #host: FxHost;
  readonly #mix: MixSettings;
  readonly #knobBox: HTMLElement;
  readonly #side: HTMLElement;
  readonly #status: HTMLElement;
  readonly #flipBox: HTMLElement;
  readonly #knobs = new Map<string, KnobControl>();
  #group: "voice" | "beat" = "voice";
  #loadedKey = "";
  #loading: Promise<void> | null = null;
  // the settings before the last «Авто», for one Ctrl+Z
  #beforeAuto: Settings | null = null;
  #autoAsked: Settings | null = null;
  #drawer: { root: HTMLElement; panels: ChainPanels } | null = null;
  #flip: FlipModes;

  constructor(host: FxHost) {
    this.#host = host;
    this.#mix = new MixSettings(host.base, host.token, () => this.#paintStatus());
    this.#knobBox = h("div", { class: "fx__knobs" });
    this.#status = h("div", { class: "fx__status", role: "status" });
    this.#side = h("div", { class: "fx__side" });
    this.#flipBox = h("div", { class: "fx__flip" });
    this.#flip = new FlipModes(host, this.#flipBox);
    this.root = h("div", { class: "fx" }, this.#flipBox, h("div", { class: "fx__main" }, this.#knobBox, this.#status), this.#side);
  }

  // `key`: what the settings belong to; a new one reads them again
  show(group: "voice" | "beat", key: string): void {
    this.#group = group;
    const facts = this.#host.facts();
    this.#flipBox.hidden = !(group === "beat" && facts.beatKind === "flip");
    if (!this.#flipBox.hidden) this.#flip.show();
    if (facts.studioReady && key !== this.#loadedKey && !this.#loading) {
      this.#loadedKey = key;
      this.#loading = this.#mix
        .load()
        .then(
          () => undefined,
          (error: unknown) => {
            this.#loadedKey = "";
            this.#host.say(`Настройки звука не загрузились: ${message(error)}`);
          },
        )
        .finally(() => {
          this.#loading = null;
          this.#paint();
        });
    }
    this.#paint();
  }

  // the jobs that finished since the last look
  jobsDone(kinds: readonly string[], jobs: readonly Job[]): void {
    if (!kinds.includes("mix") || !this.#autoAsked) return;
    const before = this.#autoAsked;
    this.#autoAsked = null;
    const job = jobs.find((j) => j.kind === "mix");
    if (job?.state !== "done") {
      this.#host.say(job?.state === "cancelled" ? "Авто отменено." : `Авто не вышло: ${job?.reason ?? "причина не названа"}`);
      this.#paint();
      return;
    }
    this.#mix.load().then(
      (settings) => {
        this.#host.studio()?.applySettings(settings);
        this.#beforeAuto = before;
        this.#host.say("Авто подобрало крутилки ближе к референсам. Ctrl+Z — вернуть как было.");
        this.#paint();
      },
      (error: unknown) => this.#host.say(`Настройки после Авто не загрузились: ${message(error)}`),
    );
  }

  // Ctrl+Z right after «Авто»: the settings before it, once
  undo(): boolean {
    const before = this.#beforeAuto;
    if (!before) return false;
    this.#beforeAuto = null;
    this.#mix.set(before, true);
    this.#host.studio()?.applySettings(before);
    this.#host.say("Вернул настройки, что были до Авто.");
    this.#paint();
    return true;
  }

  get canUndo(): boolean {
    return this.#beforeAuto !== null;
  }

  async flush(): Promise<void> {
    await this.#mix.flush();
    await this.#flip.flush();
  }

  dispose(): void {
    this.#mix.close();
    this.#flip.dispose();
    this.#drawer?.root.remove();
    this.#drawer = null;
  }

  #waitText(): string | null {
    const f = this.#host.facts();
    if (f.studioReady) return this.#mix.settings ? null : "загружаю настройки…";
    const preparing = this.#host.jobs().some((j) => (j.kind === "separate" || j.kind === "mix") && jobActive(j));
    if (preparing) return "готовлю голос…";
    if (f.voiceKind === "own" && f.chosen === null) return "сначала выбери дубль ★";
    return "готовлю голос…";
  }

  #paint(): void {
    const list = KNOBS.filter((k) => k.group === this.#group);
    const wait = this.#waitText();
    const settings = this.#mix.settings;
    const values = settings && !wait ? safeKnobs(settings) : null;
    const want = list.map((k) => k.id).join();
    if (this.#knobBox.dataset["ids"] !== want) {
      this.#knobBox.dataset["ids"] = want;
      this.#knobs.clear();
      this.#knobBox.replaceChildren(...list.map((k) => this.#knob(k)));
    }
    for (const k of list) {
      const control = this.#knobs.get(k.id);
      if (!control) continue;
      control.setDisabled(values === null);
      if (values && settings) control.set(values[k.id], safeText(settings, k));
      else control.setText("—");
    }
    this.#paintSide(wait);
    this.#paintStatus();
  }

  #knob(k: Knob): HTMLElement {
    const reset = (): number => (this.#mix.schema ? defaultsKnob(this.#mix.schema, k) : 0.5);
    const control = new KnobControl(k.name, 0, reset, {
      input: (v) => this.#turn(k, v, false),
      commit: (v) => this.#turn(k, v, true),
    });
    this.#knobs.set(k.id, control);
    return control.root;
  }

  #turn(k: Knob, value: number, save: boolean): void {
    const now = this.#mix.settings;
    if (!now) return;
    let next: Settings;
    try {
      next = settingsFromKnob(now, k.id, value);
    } catch (error) {
      this.#host.say(message(error));
      return;
    }
    this.#beforeAuto = null;
    this.#mix.set(next, save);
    this.#host.studio()?.applySettings(next);
    this.#knobs.get(k.id)?.setText(safeText(next, k));
    this.#drawer?.panels.show(next);
  }

  #paintSide(wait: string | null): void {
    const running = this.#host.jobs().some((j) => j.kind === "mix" && jobActive(j));
    const auto = h("button", { class: "btn btn--main", type: "button", disabled: running || this.#autoAsked !== null }, running ? "Авто подбирает…" : "Авто — как в референсах");
    auto.addEventListener("click", () => void this.#auto());
    const more = h("button", { class: "chip", type: "button", disabled: wait !== null || !this.#mix.schema }, "+ ещё эффекты");
    more.addEventListener("click", () => this.#openDrawer());
    const note = this.#group === "voice" ? "Подберёт крутилки сам, ближе к твоим референсам по замерам. Потом подкрути на слух." : "Громкость бита и драйв 808 в общем сведении.";
    this.#side.replaceChildren(auto, h("div", { class: "fx__note" }, note), h("div", { class: "chain" }, more));
  }

  #paintStatus(): void {
    const wait = this.#waitText();
    if (wait) {
      this.#status.textContent = wait;
      this.#status.dataset["state"] = "wait";
      return;
    }
    const s = this.#mix.state;
    this.#status.dataset["state"] = s;
    this.#status.textContent = s === "waiting" || s === "saving" ? "сохраняю…" : s === "saved" ? "сохранено" : s === "failed" ? `не сохранилось: ${this.#mix.failure ?? ""}` : "";
  }

  async #auto(): Promise<void> {
    const f = this.#host.facts();
    if (f.voiceKind === "own" && f.chosen === null) {
      this.#host.say("Сначала выбери дубль ★.");
      return;
    }
    if (this.#mix.pending) await this.#mix.flush();
    this.#autoAsked = this.#mix.settings ? structuredClone(this.#mix.settings) : {};
    this.#paint();
    await this.#host.startJob("mix");
    if (!this.#host.jobs().some((j) => j.kind === "mix" && jobActive(j))) {
      // refused or over at once: jobsDone will not come for it
      const job = this.#host.jobs().find((j) => j.kind === "mix");
      if (!job || job.state !== "done") this.#autoAsked = null;
      else this.jobsDone(["mix"], this.#host.jobs());
    }
    this.#paint();
  }

  #openDrawer(): void {
    if (this.#drawer) {
      this.#drawer.root.querySelector<HTMLElement>(".drawer__close")?.focus();
      return;
    }
    const schema = this.#mix.schema;
    const settings = this.#mix.settings;
    if (!schema || !settings) return;
    const panels = new ChainPanels(schema.settings, schema.groups, {
      input: (key, value) => {
        const now = this.#mix.settings;
        if (!now) return;
        const next = { ...now, [key]: value };
        this.#mix.set(next, false);
        this.#host.studio()?.applySettings(next);
      },
      commit: (key, value) => {
        const now = this.#mix.settings;
        if (!now) return;
        const next = { ...now, [key]: value };
        this.#beforeAuto = null;
        this.#mix.set(next, true);
        this.#host.studio()?.applySettings(next);
        panels.show(next);
        this.#paint();
      },
    });
    panels.show(settings);
    const close = h("button", { class: "btn btn--ghost drawer__close", type: "button" }, "Закрыть");
    const root = h(
      "aside",
      { class: "drawer", role: "dialog", "aria-label": "Все эффекты" },
      h("header", { class: "drawer__head" }, h("h2", {}, "Все эффекты"), h("span", { class: "spacer" }), close),
      h("div", { class: "drawer__body" }, ...panels.panels),
    );
    const finish = (): void => {
      root.remove();
      this.#drawer = null;
    };
    close.addEventListener("click", finish);
    root.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        finish();
      }
    });
    this.#host.overlay.append(root);
    this.#drawer = { root, panels };
    close.focus();
  }
}

function safeKnobs(s: Settings): Record<Knob["id"], number> | null {
  try {
    return knobsFromSettings(s);
  } catch {
    return null;
  }
}

function safeText(s: Settings, k: Knob): string {
  try {
    return knobText(s, k.id);
  } catch {
    return "—";
  }
}

// where a double click puts the knob: the schema's defaults
function defaultsKnob(schema: Schema, k: Knob): number {
  const defaults: Settings = Object.fromEntries(schema.settings.map((s) => [s.key, s.default]));
  return safeKnobs(defaults)?.[k.id] ?? 0.5;
}

// ---------- a flip's 808 and hats ----------

const BASS_MODES: [BassMode, string][] = [
  ["original", "оригинал"],
  ["ours", "наш"],
  ["off", "выкл"],
];
const HAT_MODES: [HatsMode, string][] = [
  ["original", "оригинал"],
  ["removed", "убрать"],
  ["ours", "наши"],
];

class FlipModes {
  readonly #host: FxHost;
  readonly #box: HTMLElement;
  #doc: FlipDoc | null = null;
  #timer: ReturnType<typeof setTimeout> | null = null;
  #saving: Promise<void> | null = null;
  #asked = false;
  #closed = false;
  #building = false;

  constructor(host: FxHost, box: HTMLElement) {
    this.#host = host;
    this.#box = box;
  }

  show(): void {
    const state = this.#host.flip();
    if (!this.#saving && !this.#timer && !this.#building) this.#doc = state?.draft ? structuredClone(state.draft) : null;
    const d = this.#doc;
    if (!d) {
      this.#box.replaceChildren(h("p", { class: "fx__note" }, "Черновика флипа нет."));
      return;
    }
    this.#box.replaceChildren(
      h("div", { class: "fx__row" }, h("span", { class: "fx__lbl" }, "808"), seg("808", BASS_MODES, d.bass808.mode, this.#building, (m) => this.#edit((x) => (x.bass808.mode = m)))),
      h(
        "div",
        { class: "fx__row" },
        h("span", { class: "fx__lbl" }, "Хэты"),
        seg("Хэты", HAT_MODES, d.hats.mode, this.#building, (m) =>
          this.#edit((x) => {
            x.hats.mode = m;
            if (m !== "original" && !x.hats.remove.includes("hh")) x.hats.remove.unshift("hh");
          }),
        ),
      ),
    );
  }

  async flush(): Promise<void> {
    if (this.#saving) await this.#saving;
  }

  dispose(): void {
    this.#closed = true;
    if (this.#timer) clearTimeout(this.#timer);
    this.#timer = null;
  }

  #edit(change: (d: FlipDoc) => void): void {
    const d = this.#doc;
    if (!d) return;
    const takes = this.#host.facts().takes;
    if (!this.#asked && takes > 0 && !confirm(`Флип пересоберётся новой версией. ${takes} дубл${takes === 1 ? "ь останется записанным" : takes < 5 ? "я останутся записанными" : "ей останутся записанными"} под прошлую. Продолжить?`)) return;
    this.#asked = true;
    change(d);
    this.show();
    const snapshot = structuredClone(d);
    const prior = this.#saving ?? Promise.resolve();
    this.#saving = prior
      .then(() => this.#host.flipApi.saveDraft(snapshot))
      .catch((error: unknown) => this.#host.say(`Черновик флипа не сохранился: ${message(error)}`))
      .finally(() => {
        this.#saving = null;
      });
    if (this.#timer) clearTimeout(this.#timer);
    this.#host.beatBusy("флип пересоберётся…");
    this.#timer = setTimeout(() => void this.#rebuild(), FLIP_IDLE_MS);
  }

  async #rebuild(): Promise<void> {
    this.#timer = null;
    if (this.#closed) return;
    await this.flush();
    this.#building = true;
    this.show();
    this.#host.beatBusy("флип пересобирается…");
    try {
      const api = this.#host.flipApi;
      const n = await api.render();
      for (;;) {
        await new Promise((r) => setTimeout(r, 800));
        if (this.#closed) return;
        const job = (await api.jobs()).find((j) => j.kind === "flip_render");
        if (job && jobActive(job)) continue;
        if (job?.state !== "done") throw new Error(job?.reason ?? "сборка не прошла");
        break;
      }
      await api.use(n, await api.rev());
      await this.#host.reload();
    } catch (error) {
      this.#host.say(`Флип не пересобрался: ${message(error)}`);
    } finally {
      this.#building = false;
      this.#host.beatBusy(null);
      if (!this.#closed) this.show();
    }
  }
}

function seg<T extends string>(label: string, options: [T, string][], value: T, disabled: boolean, pick: (v: T) => void): HTMLElement {
  const box = h("div", { class: "pickseg", role: "radiogroup", "aria-label": label });
  for (const [v, text] of options) {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === value), disabled }, text);
    b.addEventListener("click", () => {
      if (v !== value) pick(v);
    });
    box.append(b);
  }
  return box;
}
