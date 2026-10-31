// The effects tab: the simple knobs of the voice or the beat over the studio's settings, «Авто —
// как в референсах», the full chain in a drawer, and a flip's 808 and hats switches, the effects
// of its selected block and «Собрать по тексту».
// A knob is heard at once through the studio player and saved after a quiet moment: the studio
// saves only settings it has rendered exactly, so each save is a render and then the save.

import { createApi as createMixApi } from "../api.ts";
import { apiPath, apiRoot } from "../core/mount.ts";
import type { FlipApi, FlipState } from "../flip/api.ts";
import { fitPatterns, MAX_BARS, paintEffect, tapeCap, totalBars, type BassMode, type Block, type FlipDoc, type FlipEffect, type HatsMode, type Refusal } from "../flip/model.ts";
import { ApiError, type Job, type StartKind } from "../song/api.ts";
import { jobActive } from "../song/jobs.ts";
import type { Schema, Settings } from "../types.ts";
import { ChainPanels } from "../ui/chain.ts";
import { h } from "../ui/dom.ts";
import type { FlipDraft, FlipOp } from "./flipdraft.ts";
import { KnobControl } from "./knob.ts";
import { KNOBS, knobsFromSettings, knobText, settingsFromKnob, type Knob } from "./knobs.ts";
import { SaveQueue, type SaveState } from "./queue.ts";
import type { ScreenFacts } from "./view.ts";

const SAVE_MS = 700;

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
  flipDraft: FlipDraft;
  // the block of the flip's arrangement picked on the beat lane
  flipBlock(): number | null;
  // whether the song has a text to arrange the flip for
  hasText(): boolean;
  confirm(text: string): boolean;
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

// ---------- a flip's 808, hats, block effects and arrangement ----------

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

export const BLOCK_EFFECTS: [string, string][] = [
  ["reverse", "реверс"],
  ["tape_stop", "остановка ленты"],
  ["tape_start", "разгон ленты"],
  ["stutter", "статтер"],
  ["granular", "гранулятор"],
];

const effectName = (effect: string): string => BLOCK_EFFECTS.find(([id]) => id === effect)?.[1] ?? effect;

const TAPE_DEFAULT_S = 0.5;

// the play-order bars [from, to) of block i
export function blockSpan(blocks: readonly Block[], i: number): { from: number; to: number } | null {
  const block = blocks[i];
  if (!block) return null;
  const from = totalBars(blocks.slice(0, i));
  return { from, to: from + (block.to_bar - block.from_bar) };
}

// the effects that start in block i, with their place in the doc's list
export function blockEffects(doc: FlipDoc, i: number): { index: number; effect: FlipEffect }[] {
  const span = blockSpan(doc.arrangement, i);
  if (!span) return [];
  return doc.effects.flatMap((effect, index) => (effect.from_bar >= span.from && effect.from_bar < span.to ? [{ index, effect }] : []));
}

// an effect with its default params over the whole of block i
export function addBlockEffect(doc: FlipDoc, i: number, effect: string): FlipDoc | Refusal {
  const span = blockSpan(doc.arrangement, i);
  if (!span) return { ok: false, reason: "Выбери блок на дорожке бита." };
  const params: Record<string, number> = {};
  if (effect === "tape_stop" || effect === "tape_start") {
    const cap = tapeCap(doc.grid.bpm, span.to - span.from);
    if (cap < TAPE_DEFAULT_S) params["duration"] = cap;
  }
  const effects = paintEffect(doc.effects, { effect, from_bar: span.from, to_bar: span.to, params }, totalBars(doc.arrangement));
  if (!effects) return { ok: false, reason: "Эффекту не хватило тактов." };
  return { ...doc, effects };
}

export function removeEffect(doc: FlipDoc, index: number): FlipDoc | Refusal {
  if (!doc.effects[index]) return { ok: false, reason: "Такого эффекта нет." };
  return { ...doc, effects: doc.effects.filter((_, k) => k !== index) };
}

// whether replacing the arrangement would lose work: notes, drums or effects
export function replaceRisk(doc: FlipDoc): boolean {
  return doc.effects.length > 0 || doc.arrangement.some((b) => b.drums !== undefined || b.notes !== undefined);
}

// the arrangement a proposal gives, with plain blocks; effects and hat patterns belonged to the old ones
export function replaceArrangement(doc: FlipDoc, proposal: readonly Block[]): FlipDoc | Refusal {
  if (!proposal.length) return { ok: false, reason: "Из текста не вышло ни одного блока." };
  const arrangement = proposal.map((b) => ({ label: b.label, from_bar: b.from_bar, to_bar: b.to_bar }));
  if (totalBars(arrangement) > MAX_BARS) return { ok: false, reason: `Расстановка не длиннее ${MAX_BARS} тактов.` };
  return { ...doc, arrangement, hats: { ...doc.hats, patterns: fitPatterns([], arrangement.length) }, effects: [] };
}

export const REPLACE_ASK = "Блоки с нотами, барабанами или эффектами заменятся новой расстановкой. Продолжить?";

const proposeFailure = (error: unknown): string => (error instanceof ApiError && error.code === "no_text" ? "У песни нет текста для расстановки." : message(error));

// Asks the server for an arrangement for the song's text and puts it in the draft as one edit,
// after a question when it would replace blocks with notes, drums or effects. True when it did.
export async function proposeInto(draft: FlipDraft, api: Pick<FlipApi, "propose">, confirm: (text: string) => boolean, say: (text: string) => void): Promise<boolean> {
  let proposal: Block[];
  try {
    proposal = await api.propose();
  } catch (error) {
    say(`Не собралось по тексту: ${proposeFailure(error)}`);
    return false;
  }
  const doc = draft.doc;
  if (!doc) return false;
  if (!proposal.length) {
    say("Из текста не вышло ни одного блока: расстановка осталась как была.");
    return false;
  }
  if (replaceRisk(doc) && !confirm(REPLACE_ASK)) return false;
  return draft.apply((d) => replaceArrangement(d, proposal));
}

class FlipModes {
  readonly #host: FxHost;
  readonly #box: HTMLElement;
  #proposing = false;

  constructor(host: FxHost, box: HTMLElement) {
    this.#host = host;
    this.#box = box;
  }

  show(): void {
    const draft = this.#host.flipDraft;
    const d = draft.doc;
    if (!d) {
      this.#box.replaceChildren(h("p", { class: "fx__note" }, "Черновика флипа нет."));
      return;
    }
    const busy = draft.rendering;
    const rows: HTMLElement[] = [
      h("div", { class: "fx__row" }, h("span", { class: "fx__lbl" }, "808"), seg("808", BASS_MODES, d.bass808.mode, busy, (m) => this.#edit(draft, (x) => ({ ...x, bass808: { ...x.bass808, mode: m } })))),
      h(
        "div",
        { class: "fx__row" },
        h("span", { class: "fx__lbl" }, "Хэты"),
        seg("Хэты", HAT_MODES, d.hats.mode, busy, (m) =>
          this.#edit(draft, (x) => {
            x.hats.mode = m;
            if (m !== "original" && !x.hats.remove.includes("hh")) x.hats.remove.unshift("hh");
            return x;
          }),
        ),
      ),
      this.#blockRow(draft, d),
    ];
    const chips = this.#chips(draft, d);
    if (chips) rows.push(chips);
    rows.push(this.#proposeRow(d));
    this.#box.replaceChildren(...rows);
  }

  #blockRow(draft: FlipDraft, d: FlipDoc): HTMLElement {
    const at = this.#host.flipBlock();
    const block = at === null ? undefined : d.arrangement[at];
    const row = h("div", { class: "fx__row fx__row--wrap" }, h("span", { class: "fx__lbl" }, "На блок"));
    for (const [id, name] of BLOCK_EFFECTS) {
      const add = h("button", { class: "chip chip--small", type: "button", disabled: !block, title: block ? `${name} на весь блок «${block.label}»` : "Выбери блок на дорожке бита" }, `+ ${name}`);
      add.addEventListener("click", () => {
        if (at !== null) this.#edit(draft, (doc) => addBlockEffect(doc, at, id));
      });
      row.append(add);
    }
    return row;
  }

  #chips(draft: FlipDraft, d: FlipDoc): HTMLElement | null {
    const at = this.#host.flipBlock();
    const list = at === null ? [] : blockEffects(d, at);
    if (!list.length) return null;
    const row = h("div", { class: "fx__row fx__row--wrap" }, h("span", { class: "fx__lbl" }, "Стоят"));
    for (const { index, effect } of list) {
      const name = effectName(effect.effect);
      const drop = h("button", { class: "chip__x", type: "button", title: `Убрать: ${name}`, "aria-label": `Убрать: ${name}` }, "×");
      drop.addEventListener("click", () => this.#edit(draft, (doc) => removeEffect(doc, index)));
      row.append(h("span", { class: "chip chip--small chip--tag", title: `такты ${effect.from_bar + 1}–${effect.to_bar}` }, name, drop));
    }
    return row;
  }

  #proposeRow(d: FlipDoc): HTMLElement {
    const has = this.#host.hasText();
    const go = h("button", { class: "chip", type: "button", disabled: !has || this.#proposing || d.arrangement.length === 0, title: has ? "Расставить блоки под текст песни" : "У песни нет текста" }, this.#proposing ? "Собираю…" : "Собрать по тексту");
    go.addEventListener("click", () => void this.#propose());
    return h("div", { class: "fx__row" }, h("span", { class: "fx__lbl" }, ""), go, h("span", { class: "fx__note" }, has ? "заменит расстановку; Ctrl+Z вернёт" : "текста у песни нет"));
  }

  async #propose(): Promise<void> {
    if (this.#proposing) return;
    this.#proposing = true;
    this.show();
    try {
      await proposeInto(this.#host.flipDraft, this.#host.flipApi, (text) => this.#host.confirm(text), (text) => this.#host.say(text));
    } finally {
      this.#proposing = false;
      this.show();
    }
  }

  async flush(): Promise<void> {
    await this.#host.flipDraft.flush();
  }

  dispose(): void {}

  #edit(draft: FlipDraft, op: FlipOp): void {
    draft.apply(op);
    this.show();
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
