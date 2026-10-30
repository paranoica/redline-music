// Wires the studio: the session to the panels, settings to the engine's racks, the engine's
// levels to the meters and the waveform, and the A/B at equal loudness.

import { Superseded, createApi, type Api, type SessionReply } from "./api.ts";
import { analysisFailure, retryDelay } from "./core/analysis.ts";
import { catalogueOf } from "./core/diff.ts";
import { clock, formatNumber } from "./core/format.ts";
import { History } from "./core/history.ts";
import { keyAction } from "./core/keys.ts";
import { KeySurface, Scope } from "./core/lifecycle.ts";
import { audioHold, type Mount } from "./core/mount.ts";
import { intoLoop, type Loop } from "./core/loop.ts";
import { abGains, dbToGain, integratedLufs, loudestSpan } from "./core/loudness.ts";
import { buildLanes, buildRacks, parseRack } from "./core/racks.ts";
import { changedKeys, needsAnalysis, staleKeys } from "./core/stale.ts";
import { parseWav } from "./core/wav.ts";
import type { EngineClient } from "./engine/client.ts";
import { startHost, type Host } from "./engine/host.ts";
import type { Levels, Source } from "./engine/protocol.ts";
import { ChainPanels } from "./ui/chain.ts";
import { LanesView } from "./ui/lanes.ts";
import { Dropdown, Segmented } from "./ui/controls.ts";
import { closeDismissables, h, insertMarkup, refsIn } from "./ui/dom.ts";
import { Meters } from "./ui/meters.ts";
import { RackEditor } from "./ui/rack.ts";
import { Waveform } from "./ui/waveform.ts";
import { studioMarkup } from "./studio-markup.ts";
import type { Analysis, Lane, Racks, Settings, Track, UserEffect } from "./types.ts";

const LOOP_SECONDS = 20;
const ANALYSIS_WAIT_MS = 300;
// a measured loudness of the result moves the A/B level only when it moved this much
const RESULT_LUFS_STEP = 0.5;

type Tone = "plain" | "busy" | "error" | "done";

function lookups(nodes: readonly Node[]) {
  const ref = refsIn(nodes);
  return {
    status: ref<HTMLElement>("status"),
    play: ref<HTMLButtonElement>("play"),
    loop: ref<HTMLButtonElement>("loop"),
    time: ref<HTMLElement>("time"),
    total: ref<HTMLElement>("total"),
    ab: ref<HTMLElement>("ab"),
    abGain: ref<HTMLElement>("ab-gain"),
    refPick: ref<HTMLElement>("ref-pick"),
    render: ref<HTMLButtonElement>("render"),
    save: ref<HTMLButtonElement>("save"),
    track: ref<HTMLElement>("track-name"),
    wave: ref<HTMLElement>("wave"),
    canvas: ref<HTMLCanvasElement>("wave-canvas"),
    lanes: ref<HTMLElement>("lanes"),
    tools: ref<HTMLElement>("tools"),
    desk: ref<HTMLElement>("desk"),
    stripSlot: ref<HTMLElement>("strip-slot"),
    analysis: ref<HTMLElement>("analysis-state"),
  };
}

type Ui = ReturnType<typeof lookups>;

interface Parts {
  root: HTMLElement;
  ui: Ui;
  api: Api;
  scope: Scope;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Stem {
  rate: number;
  frames: number;
  data: Float32Array;
}

class Studio {
  readonly #root: HTMLElement;
  readonly #ui: Ui;
  readonly #api: Api;
  readonly #scope: Scope;
  readonly #session: SessionReply;
  readonly #client: EngineClient;
  readonly #ctx: AudioContext;
  readonly #rate: number;
  readonly #original: Stem;
  readonly #defaults: Settings;
  readonly #stale: Set<string>;
  readonly #history: History<Settings>;
  readonly #wave: Waveform;
  readonly #meters = new Meters();
  readonly #chain: ChainPanels;
  readonly #rack: RackEditor;
  readonly #lanes: LanesView;
  readonly #refs: Dropdown<number>;
  readonly #ab: Segmented<Source>;
  readonly #presets: Dropdown<string>;
  readonly #undo: HTMLButtonElement;
  readonly #redo: HTMLButtonElement;
  #settings: Settings;
  // the settings as last saved or loaded; the edits since are what leaving would lose
  #savedKey: string;
  #numbers: Analysis;
  #presetList: Record<string, Settings>;
  #analysing: AbortController | null = null;
  #analysisTimer = 0;
  #analysisRetries = 0;
  #loop: Loop;
  #loopOn = true;
  #playing = false;
  #toggledAt = 0;
  #anchor = { frame: 0, at: 0 };
  // after a seek or a play the clock waits until the engine says the output is heard
  #held = false;
  #frame = 0;
  #side: Source = "result";
  readonly #lufs: Record<Source, number | null> = { result: null, original: null, reference: null, render: null };
  #reference: { index: number; name: string; data: Float32Array } | null = null;
  #referenceSeq = 0;
  #render: { key: string; data: Float32Array; lufs: number | null; seconds: number } | null = null;
  #renderSeq = 0;
  #rendering: AbortController | null = null;

  constructor(parts: Parts, session: SessionReply, client: EngineClient, ctx: AudioContext, original: Stem) {
    this.#root = parts.root;
    this.#ui = parts.ui;
    this.#api = parts.api;
    this.#scope = parts.scope;
    this.#session = session;
    this.#client = client;
    this.#ctx = ctx;
    this.#rate = session.track.rate;
    this.#original = original;
    this.#numbers = session.analysis;
    this.#presetList = session.presets;
    const scalars = Object.fromEntries(session.schema.settings.map((s) => [s.key, s.default]));
    this.#defaults = { version: 2, ...scalars, rack: [] };
    this.#settings = { ...this.#defaults, ...session.settings };
    this.#stale = staleKeys(session.stale);
    this.#savedKey = JSON.stringify(this.#settings);
    this.#history = new History(this.#settings);

    this.#ui.track.textContent = session.track.name;
    this.#ui.total.textContent = clock(session.track.seconds);
    this.#wave = new Waveform(this.#ui.wave, this.#ui.canvas, {
      seek: (s) => this.#seek(s),
      loop: (loop, done) => this.#setLoop(loop, done),
    });
    this.#wave.setAudio(original.data, this.#rate);
    this.#ui.wave.setAttribute("aria-valuemax", session.track.seconds.toFixed(1));

    this.#chain = new ChainPanels(session.schema.settings, session.schema.groups, {
      input: (key, v) => this.#change({ ...this.#settings, [key]: v }, false),
      commit: (key, v) => this.#change({ ...this.#settings, [key]: v }, true),
    });
    this.#rack = new RackEditor(session.schema.effects, {
      input: (rack) => this.#change({ ...this.#settings, rack }, false),
      commit: (rack) => this.#change({ ...this.#settings, rack }, true),
    });
    this.#lanes = new LanesView(this.#ui.lanes, session.schema.effects, original.frames / this.#rate, {
      input: (rack) => this.#change({ ...this.#settings, rack }, false),
      commit: (rack) => this.#change({ ...this.#settings, rack }, true),
    });
    const panels = this.#chain.panels;
    const grid = h("div", { class: "panels" }, ...panels.slice(0, 4), this.#rack.root, ...panels.slice(4));
    this.#ui.desk.append(this.#chain.flow, grid);
    this.#ui.stripSlot.replaceWith(this.#meters.root);
    this.#meters.idle();

    this.#ab = new Segmented<Source>(
      "Что слушать (B, R)",
      [
        { value: "original", label: "Оригинал" },
        { value: "result", label: "Результат" },
        { value: "reference", label: "Референс" },
        { value: "render", label: "Рендер" },
      ],
      "result",
      (s) => this.#listen(s),
      "seg seg--ab",
    );
    this.#ui.ab.prepend(this.#ab.root);
    this.#abAvailability();
    const first = session.references[0];
    this.#refs = new Dropdown<number>(
      "Какой референс",
      session.references.map((r) => ({ value: r.index, label: r.name })),
      first?.index ?? null,
      (i) => void this.#loadReference(i),
      first ? "референс…" : "нет референсов",
    );
    this.#refs.setDisabled(!first);
    this.#ui.refPick.append(this.#refs.root);

    const view = new Segmented<"quick" | "full">(
      "Какие настройки показывать",
      [
        { value: "quick", label: "Быстро" },
        { value: "full", label: "Всё" },
      ],
      "quick",
      (v) => {
        this.#root.dataset.view = v;
        view.set(v);
      },
      "seg seg--view",
    );
    this.#undo = h("button", { class: "btn btn--ghost", type: "button", title: "Ctrl+Z" }, "Отменить");
    this.#redo = h("button", { class: "btn btn--ghost", type: "button", title: "Ctrl+Shift+Z" }, "Вернуть");
    const reset = h("button", { class: "btn btn--ghost", type: "button" }, "Сбросить всё");
    this.#undo.addEventListener("click", () => this.#step(this.#history.undo()));
    this.#redo.addEventListener("click", () => this.#step(this.#history.redo()));
    reset.addEventListener("click", () => this.#change(structuredClone(this.#defaults), true));
    this.#presets = new Dropdown<string>("Пресет", [], null, (name) => this.#applyPreset(name), "пресет…");
    this.#showPresets();
    const presetName = h("input", { class: "text", type: "text", maxlength: 40, placeholder: "имя пресета", "aria-label": "Имя нового пресета" });
    const presetSave = h("button", { class: "btn btn--ghost", type: "button" }, "Сохранить пресет");
    presetSave.addEventListener("click", () => void this.#savePreset(presetName));
    this.#ui.tools.append(
      h("div", { class: "tools__group" }, view.root, this.#undo, this.#redo, reset),
      h("div", { class: "tools__group" }, this.#presets.root, presetName, presetSave),
    );

    this.#loop = loudestSpan(original.data, this.#rate, Math.min(LOOP_SECONDS, session.track.seconds));
    this.#wire();
    this.#refresh();
  }

  dispose(): void {
    this.#analysing?.abort();
    this.#rendering?.abort();
    this.#wave.dispose();
    this.#lanes.dispose();
    this.#client.onLevels = () => {};
    this.#client.onEnded = () => {};
    this.#client.onFatal = () => {};
    this.#client.onError = () => {};
  }

  #status(text: string, tone: Tone = "plain", kind = ""): void {
    const el = this.#ui.status;
    // a stopped engine stays on screen: nothing else matters until the page is reloaded
    if (el.dataset.kind === "engine") return;
    el.textContent = text;
    el.dataset.tone = tone;
    el.dataset.kind = kind;
  }

  start(): void {
    const s = this.#session;
    if (s.racks) {
      const racks = { vocal: parseRack(s.racks.vocal), beat: parseRack(s.racks.beat), master: parseRack(s.racks.master) };
      this.#pushRacks(racks);
    } else {
      this.#status(`Живые рэки не собрались: ${s.racks_error ?? "без причины"}. Поправь настройки — звук пойдёт без обработки, пока рэк не соберётся.`, "error", "rack");
    }
    this.#setLoop(this.#loop, true);
    this.#measureOriginal();
    this.#listen("result");
    if (s.racks) this.#status("Готово. Пробел — играть. Крути — звук меняется сразу.", "done");
    const first = s.references[0];
    if (first) void this.#loadReference(first.index, true);
  }

  // --- settings ---

  #change(next: Settings, commit: boolean): void {
    this.#settings = next;
    if (commit) this.#history.push(next);
    this.#refresh();
    this.#pushRacks();
    if (commit) this.#scheduleAnalysis();
  }

  #step(to: Settings | null): void {
    if (!to) return;
    this.#settings = to;
    this.#refresh();
    this.#pushRacks();
    this.#scheduleAnalysis();
  }

  #refresh(): void {
    this.#chain.show(this.#settings);
    this.#rack.show((this.#settings.rack ?? []) as UserEffect[]);
    this.#lanes.show((this.#settings.rack ?? []) as UserEffect[]);
    this.#undo.disabled = !this.#history.canUndo;
    this.#redo.disabled = !this.#history.canRedo;
    this.#showSave();
  }

  #pushRacks(racks?: Racks, lanes?: Lane[]): void {
    try {
      const built = racks ?? buildRacks(this.#settings, this.#numbers);
      this.#client.setRacks(built, lanes ?? buildLanes(this.#settings, this.#numbers));
      if (this.#ui.status.dataset.tone === "error" && this.#ui.status.dataset.kind === "rack") this.#status("Рэк снова собрался.", "done");
    } catch (error) {
      this.#status(`Рэк с этими настройками не собрать: ${message(error)}`, "error", "rack");
    }
  }

  #scheduleAnalysis(): void {
    const analysed = this.#numbers.settings;
    if (analysed && !needsAnalysis(changedKeys(analysed, this.#settings), this.#stale)) return;
    this.#analysisRetries = 0;
    this.#scope.clearTimeout(this.#analysisTimer);
    this.#analysisTimer = this.#scope.setTimeout(() => void this.#analyse(), ANALYSIS_WAIT_MS);
  }

  async #analyse(): Promise<void> {
    this.#analysing?.abort();
    const control = new AbortController();
    this.#analysing = control;
    const asked = structuredClone(this.#settings);
    const key = JSON.stringify(asked);
    this.#ui.analysis.dataset.state = "busy";
    this.#ui.analysis.textContent = "пересчитываю анализ";
    try {
      const reply = await this.#api.analysis(asked, control.signal);
      if (control.signal.aborted) return;
      this.#numbers = reply.analysis;
      const lanes = reply.lanes.map((l) => (Array.isArray(l) ? { slot: l[0], param: l[1], points: l[2] } : l));
      if (JSON.stringify(this.#settings) === key) {
        const r = reply.racks;
        this.#pushRacks({ vocal: parseRack(r.vocal), beat: parseRack(r.beat), master: parseRack(r.master) }, lanes);
      } else {
        this.#pushRacks();
      }
      this.#analysisRetries = 0;
      this.#ui.analysis.dataset.state = "done";
      this.#ui.analysis.textContent = "анализ свежий";
    } catch (error) {
      const outcome = analysisFailure(error, control.signal.aborted);
      if (outcome === "quiet") return;
      if (outcome === "again") {
        const wait = retryDelay(this.#analysisRetries++);
        if (wait !== null) {
          this.#scope.clearTimeout(this.#analysisTimer);
          this.#analysisTimer = this.#scope.setTimeout(() => void this.#analyse(), wait);
          return;
        }
        this.#ui.analysis.dataset.state = "error";
        this.#ui.analysis.textContent = "анализ занят другой вкладкой";
        this.#status("Анализ занят другой вкладкой студии — обнови страницу, когда закроешь её. Звук идёт с прошлыми числами.", "error");
        return;
      }
      this.#ui.analysis.dataset.state = "error";
      this.#ui.analysis.textContent = "анализ не прошёл";
      this.#status(`Анализ не прошёл: ${message(error)}. Звук идёт с прошлыми числами.`, "error");
    }
  }

  get dirty(): boolean {
    return JSON.stringify(this.#settings) !== this.#savedKey;
  }

  load(settings: Record<string, unknown>, label: string): void {
    this.#change({ ...structuredClone(this.#defaults), ...(structuredClone(settings) as Partial<Settings>) }, true);
    this.#savedKey = JSON.stringify(this.#settings);
    this.#status(`Звучит ${label}.`, "done");
  }

  #applyPreset(name: string): void {
    const preset = this.#presetList[name];
    if (!preset) return;
    this.#change({ ...structuredClone(this.#defaults), ...structuredClone(preset) }, true);
    this.#status(`Пресет «${name}».`, "done");
  }

  #showPresets(): void {
    this.#presets.setOptions(Object.keys(this.#presetList).map((n) => ({ value: n, label: n })));
  }

  async #savePreset(input: HTMLInputElement): Promise<void> {
    const name = input.value.trim();
    if (!name) {
      this.#status("Дай пресету имя.", "error");
      input.focus();
      return;
    }
    try {
      await this.#api.preset(name, this.#settings);
      const differs = Object.fromEntries(changedKeys(this.#defaults, this.#settings).map((k) => [k, this.#settings[k]]));
      this.#presetList = { ...this.#presetList, [name]: differs };
      this.#showPresets();
      input.value = "";
      this.#status(`Пресет «${name}» сохранён.`, "done");
    } catch (error) {
      this.#status(`Пресет не сохранён: ${message(error)}`, "error");
    }
  }

  // --- transport ---

  #wire(): void {
    const c = this.#client;
    c.onLevels = (l) => this.#levels(l);
    c.onEnded = () => {
      this.#playing = false;
      this.#anchor = { frame: 0, at: this.#ctx.currentTime };
      this.#showPlay();
    };
    c.onFatal = (reason) => {
      this.#playing = false;
      this.#showPlay();
      this.#ui.play.disabled = true;
      this.#status(`Движок звука остановился: ${reason}. Убери тяжёлые эффекты и обнови страницу.`, "error", "engine");
    };
    c.onError = (e) => this.#status(`Движок: ${e.track ? `${trackName(e.track)} — ` : ""}${e.message}`, "error");
    this.#ui.play.addEventListener("click", () => void this.#togglePlay());
    this.#ui.loop.addEventListener("click", () => this.#setLoopOn(!this.#loopOn));
    this.#ui.render.addEventListener("click", () => void this.#exactRender());
    this.#ui.save.addEventListener("click", () => void this.#save());
  }

  key(e: KeyboardEvent): void {
    const t = e.target as HTMLElement | null;
    const typing = !!t && (t.isContentEditable || (t instanceof HTMLInputElement && t.type !== "range") || t instanceof HTMLTextAreaElement);
    const action = keyAction(e, typing);
    if (!action) return;
    // a focused button takes Space as its own click
    if (action === "play" && t instanceof HTMLButtonElement && t !== this.#ui.play) return;
    e.preventDefault();
    if (action === "play") void this.#togglePlay();
    else if (action === "loop") this.#setLoopOn(!this.#loopOn);
    else if (action === "ab") this.#listen(this.#side === "original" ? "result" : "original");
    else if (action === "reference" && this.#reference) this.#listen(this.#side === "reference" ? "result" : "reference");
    else if (action === "undo") this.#undo.click();
    else if (action === "redo") this.#redo.click();
  }

  async #togglePlay(): Promise<void> {
    if (this.#client.dead || this.#scope.closed) return;
    await this.#ctx.resume().catch((error: unknown) => {
      if (!this.#scope.closed) throw error;
    });
    if (this.#client.dead || this.#scope.closed) return;
    this.#playing = !this.#playing;
    this.#toggledAt = performance.now();
    this.#anchor = { frame: this.#frame, at: this.#ctx.currentTime };
    this.#held = this.#playing;
    this.#client.play(this.#playing);
    this.#showPlay();
    if (this.#playing) this.#tick();
  }

  #showPlay(): void {
    this.#ui.play.setAttribute("aria-pressed", String(this.#playing));
    this.#ui.play.setAttribute("aria-label", this.#playing ? "Пауза (пробел)" : "Играть (пробел)");
    this.#root.dataset.playing = String(this.#playing);
  }

  #seek(seconds: number): void {
    if (this.#client.dead) return;
    // the engine takes a seek outside a loop that is on to the loop's start; the clock goes too
    const loop = { start: Math.round(this.#loop.start * this.#rate), end: Math.round(this.#loop.end * this.#rate) };
    const frame = intoLoop(Math.round(seconds * this.#rate), this.#loopOn, loop);
    this.#frame = frame;
    this.#anchor = { frame, at: this.#ctx.currentTime };
    this.#held = true;
    this.#client.seek(frame);
    this.#showPosition(frame / this.#rate);
  }

  #setLoop(loop: Loop, done: boolean): void {
    if (this.#client.dead) return;
    this.#loop = loop;
    this.#wave.setLoop(loop, this.#loopOn);
    this.#lanes.setLoop(loop, this.#loopOn);
    if (!done) return;
    const frame = this.#frame;
    // the engine jumps into a loop that the position is outside of; the clock follows it
    if (this.#loopOn && (frame < loop.start * this.#rate || frame >= loop.end * this.#rate)) {
      this.#frame = Math.round(loop.start * this.#rate);
      this.#anchor = { frame: this.#frame, at: this.#ctx.currentTime };
      this.#held = true;
      this.#showPosition(loop.start);
    }
    this.#client.loop(this.#loopOn, Math.round(loop.start * this.#rate), Math.round(loop.end * this.#rate));
    this.#ui.loop.title = `Петля ${clock(loop.start)}–${clock(loop.end)} (L)`;
    this.#measureOriginal();
    if (this.#reference) this.#placeReference();
  }

  #setLoopOn(on: boolean): void {
    if (this.#client.dead) return;
    this.#loopOn = on;
    this.#ui.loop.setAttribute("aria-pressed", String(on));
    this.#setLoop(this.#loop, true);
  }

  #levels(l: Levels): void {
    this.#frame = l.frame;
    this.#anchor = { frame: l.frame, at: this.#ctx.currentTime };
    // the report of the stop before a play does not start the clock
    if (l.playing) this.#held = l.held;
    // a report from before the last press of play may still be on its way
    const settled = performance.now() - this.#toggledAt > 150;
    if (settled && this.#playing && !l.playing) {
      this.#playing = false;
      this.#showPlay();
    }
    this.#meters.show(l);
    if (l.master.gr !== null && l.playing) this.#wave.markRedline(l.frame / this.#rate, l.master.gr);
    if (Number.isFinite(l.lufs)) {
      const known = this.#lufs.result;
      if (known === null || Math.abs(known - l.lufs) >= RESULT_LUFS_STEP) {
        this.#lufs.result = l.lufs;
        this.#listen(this.#side);
      }
    }
  }

  // the clock between two level reports moves on from the last one
  #tick(): void {
    if (!this.#playing) return;
    const latency = this.#ctx.outputLatency || 0;
    const since = this.#held ? 0 : this.#ctx.currentTime - this.#anchor.at;
    let frame = this.#anchor.frame + (since - latency) * this.#rate;
    if (this.#loopOn && frame >= this.#loop.end * this.#rate) {
      const start = this.#loop.start * this.#rate;
      frame = start + ((frame - start) % ((this.#loop.end - this.#loop.start) * this.#rate));
    }
    this.#showPosition(Math.max(0, frame) / this.#rate);
    this.#scope.requestFrame(() => this.#tick());
  }

  #showPosition(seconds: number): void {
    this.#ui.time.textContent = clock(seconds);
    this.#wave.setPosition(seconds);
    this.#lanes.setPosition(seconds);
  }

  // --- A/B at equal loudness ---

  #span(): [number, number] {
    return this.#loopOn ? [Math.round(this.#loop.start * this.#rate), Math.round(this.#loop.end * this.#rate)] : [0, this.#original.frames];
  }

  #measureOriginal(): void {
    const [a, b] = this.#span();
    this.#lufs.original = integratedLufs(this.#original.data, this.#rate, a, b);
    if (this.#render) this.#lufs.render = integratedLufs(this.#render.data, this.#rate, a, b);
    this.#listen(this.#side);
  }

  #abAvailability(): void {
    const ref = this.#ab.button("reference");
    const render = this.#ab.button("render");
    if (ref) ref.disabled = !this.#reference;
    if (render) render.disabled = !this.#render;
  }

  #listen(side: Source): void {
    this.#side = side;
    this.#ab.set(side);
    const present: Partial<Record<Source, number | null>> = { result: this.#lufs.result, original: this.#lufs.original };
    if (this.#reference) present.reference = this.#lufs.reference;
    if (this.#render) present.render = this.#lufs.render;
    const db = abGains(present);
    const gains: Record<Source, number> = { result: 0, original: 0, reference: 0, render: 0 };
    gains[side] = dbToGain(db[side] ?? 0);
    this.#client.listen(gains);
    const offset = db[side] ?? 0;
    this.#ui.abGain.textContent = Math.abs(offset) < 0.05 ? "уровень как есть" : `тише на ${formatNumber(-offset, 1)} дБ для честного сравнения`;
  }

  // the first reference loads quietly at the start, so the status keeps its "ready"
  async #loadReference(index: number, quiet = false): Promise<void> {
    const chosen = this.#session.references.find((r) => r.index === index);
    if (!chosen) return;
    const seq = ++this.#referenceSeq;
    this.#refs.set(index);
    if (!quiet) this.#status(`Загружаю референс «${chosen.name}»…`, "busy");
    try {
      const audio = await this.#api.reference(index);
      // a reference may have another rate: the context resamples it to the track's
      const buffer = await this.#ctx.decodeAudioData(audio.bytes);
      if (seq !== this.#referenceSeq) return;
      const l = buffer.getChannelData(0);
      const r = buffer.numberOfChannels > 1 ? buffer.getChannelData(1) : l;
      const data = new Float32Array(2 * buffer.length);
      for (let n = 0; n < buffer.length; n++) {
        data[2 * n] = l[n] as number;
        data[2 * n + 1] = r[n] as number;
      }
      this.#reference = { index, name: chosen.name, data };
      this.#placeReference();
      this.#abAvailability();
      if (!quiet) this.#status(`Референс «${chosen.name}»: R или кнопка «Референс». Играет его самый громкий кусок длиной с петлю, на той же громкости.`, "done");
    } catch (error) {
      if (seq !== this.#referenceSeq) return;
      this.#status(`Референс не загрузился: ${message(error)}`, "error");
    }
  }

  #placeReference(): void {
    const ref = this.#reference;
    if (!ref) return;
    const [a, b] = this.#span();
    const seconds = Math.min((b - a) / this.#rate, ref.data.length / 2 / this.#rate);
    const span = loudestSpan(ref.data, this.#rate, seconds);
    const start = Math.round(span.start * this.#rate);
    const length = Math.max(1, Math.round((span.end - span.start) * this.#rate));
    this.#lufs.reference = integratedLufs(ref.data, this.#rate, start, start + length);
    this.#client.setSource("reference", ref.data.slice(), start, length);
    this.#listen(this.#side);
  }

  // --- exact render and save ---

  async #exactRender(): Promise<void> {
    this.#rendering?.abort();
    const control = new AbortController();
    this.#rendering = control;
    this.#renderSeq = Math.max(Date.now(), this.#renderSeq + 1);
    const settings = structuredClone(this.#settings);
    const began = performance.now();
    this.#root.dataset.rendering = "true";
    this.#ui.render.setAttribute("aria-busy", "true");
    const tick = this.#scope.setInterval(() => this.#status(`Точный рендер через Python: ${Math.round((performance.now() - began) / 1000)} с… Играть и крутить можно.`, "busy"), 250);
    try {
      const audio = await this.#api.render(settings, this.#renderSeq, control.signal);
      const wav = parseWav(audio.bytes);
      if (wav.rate !== this.#rate) throw new Error(`рендер пришёл в ${wav.rate} Гц, трек в ${this.#rate}`);
      const seconds = (performance.now() - began) / 1000;
      this.#render = { key: JSON.stringify(settings), data: wav.data, lufs: audio.lufs, seconds };
      const [a, b] = this.#span();
      this.#lufs.render = integratedLufs(wav.data, this.#rate, a, b);
      this.#client.setSource("render", wav.data.slice());
      this.#abAvailability();
      this.#listen(this.#side);
      const live = this.#lufs.result;
      const parity =
        audio.lufs !== null && Number.isFinite(audio.lufs) && live !== null && this.#lufs.render !== null
          ? ` На петле рендер ${formatNumber(this.#lufs.render, 1)} LUFS, вживую ${formatNumber(live, 1)} LUFS.`
          : "";
      this.#status(`Точный рендер готов за ${Math.round(seconds)} с.${parity} Его можно слушать (кнопка «Рендер») и сохранять.`, "done");
    } catch (error) {
      if (error instanceof Superseded || (error instanceof DOMException && error.name === "AbortError")) return;
      this.#status(`Рендер не прошёл: ${message(error)}`, "error");
    } finally {
      this.#scope.clearInterval(tick);
      if (this.#rendering === control) {
        this.#root.dataset.rendering = "false";
        this.#ui.render.removeAttribute("aria-busy");
        this.#showSave();
      }
    }
  }

  #showSave(): void {
    const exact = this.#render?.key === JSON.stringify(this.#settings);
    this.#ui.save.disabled = !exact;
    this.#ui.save.title = exact ? "Сохранить настройки этого рендера" : "Сначала точный рендер с этими настройками";
  }

  async #save(): Promise<void> {
    const saving = JSON.stringify(this.#settings);
    try {
      const { path } = await this.#api.save(this.#settings);
      this.#savedKey = saving;
      this.#status(`Сохранено: ${path}`, "done");
    } catch (error) {
      this.#status(error instanceof Superseded ? "Сначала точный рендер с этими настройками, потом сохранение." : `Не сохранилось: ${message(error)}`, "error");
    }
  }
}

function trackName(t: Track): string {
  return t === "vocal" ? "голос" : t === "beat" ? "бит" : "мастер";
}

export const mount: Mount = async (root, options) => {
  const scope = new Scope();
  const api = createApi({ base: options.base, token: options.token });
  const nodes = insertMarkup(root, studioMarkup);
  const flags = ["view", "state", "playing", "rendering"] as const;
  root.classList.add("studio-app");
  root.dataset.view = "quick";
  root.dataset.state = "loading";
  root.dataset.playing = "false";
  scope.add(() => {
    root.classList.remove("studio-app");
    if (!root.classList.length) root.removeAttribute("class");
    for (const flag of flags) delete root.dataset[flag];
    for (const node of nodes) node.remove();
  });
  const ui = lookups(nodes);
  const status = (text: string, tone: Tone): void => {
    ui.status.textContent = text;
    ui.status.dataset.tone = tone;
    ui.status.dataset.kind = "";
  };

  let studio: Studio | null = null;
  const keys = new KeySurface(root.ownerDocument, (e) => studio?.key(e));
  keys.setActive(options.active ?? true);
  let host: Host | null = null;
  let pending: { settings: Record<string, unknown>; label: string } | null = null;

  const boot = async (): Promise<void> => {
    status("Загружаю трек и считаю анализ. На длинном треке это до минуты.", "busy");
    const session = await api.session();
    const rate = session.track.rate;
    status("Загружаю стемы и движок…", "busy");
    const load = async (name: "vocal" | "beat" | "original"): Promise<Stem> => {
      const audio = await api.stem(name);
      const wav = parseWav(audio.bytes);
      if (wav.rate !== rate) throw new Error(`стем ${name} в ${wav.rate} Гц, трек в ${rate}`);
      return wav;
    };
    const started = startHost(rate, catalogueOf(session.schema.effects));
    let loaded: [Stem, Stem, Stem];
    try {
      loaded = await Promise.all([load("vocal"), load("beat"), load("original")]);
    } catch (error) {
      // the engine may still be coming up: it is not left running
      started.then((h) => h.dispose(), () => {});
      throw error;
    }
    host = await started;
    if (scope.closed) {
      await host.dispose();
      host = null;
      return;
    }
    const [vocal, beat, original] = loaded;
    if (host.ctx.sampleRate !== rate) throw new Error(`браузер не открыл звук на ${rate} Гц (дал ${host.ctx.sampleRate})`);
    studio = new Studio({ root, ui, api, scope }, session, host.client, host.ctx, original);
    host.client.loadStems(vocal.frames, vocal.data, beat.data, original.data.slice());
    studio.start();
    if (pending) studio.load(pending.settings, pending.label);
    pending = null;
    root.dataset.state = "ready";
  };

  void boot().catch((error: unknown) => {
    if (scope.closed) return;
    root.dataset.state = "failed";
    status(`Студия не загрузилась: ${message(error)}`, "error");
  });

  return {
    setActive: (on) => keys.setActive(on && !scope.closed),
    hold: audioHold(() => (scope.closed ? null : host?.ctx)),
    dirty: () => studio?.dirty ?? false,
    load: (settings, label) => {
      if (scope.closed) return;
      if (studio) studio.load(settings, label);
      else pending = { settings, label };
    },
    dispose: async () => {
      if (scope.closed) return;
      keys.setActive(false);
      api.close();
      closeDismissables(root);
      studio?.dispose();
      scope.dispose();
      const closing = host?.dispose();
      await closing;
    },
  };
};
