// Wires the studio: the session to the panels, settings to the engine's racks, the engine's
// levels to the meters and the waveform, and the A/B at equal loudness.

import { Superseded, api, type SessionReply } from "./api.ts";
import { catalogueOf } from "./core/diff.ts";
import { clock, formatNumber } from "./core/format.ts";
import { History } from "./core/history.ts";
import { keyAction } from "./core/keys.ts";
import type { Loop } from "./core/loop.ts";
import { abGains, dbToGain, integratedLufs, loudestSpan } from "./core/loudness.ts";
import { buildLanes, buildRacks, parseRack } from "./core/racks.ts";
import { changedKeys, needsAnalysis, staleKeys } from "./core/stale.ts";
import { parseWav } from "./core/wav.ts";
import type { EngineClient } from "./engine/client.ts";
import { startHost } from "./engine/host.ts";
import type { Levels, Source } from "./engine/protocol.ts";
import { ChainPanels } from "./ui/chain.ts";
import { LanesView } from "./ui/lanes.ts";
import { Dropdown, Segmented } from "./ui/controls.ts";
import { byId, h } from "./ui/dom.ts";
import { Meters } from "./ui/meters.ts";
import { RackEditor } from "./ui/rack.ts";
import { Waveform } from "./ui/waveform.ts";
import type { Analysis, Lane, Racks, Settings, Track, UserEffect } from "./types.ts";

const LOOP_SECONDS = 20;
const ANALYSIS_WAIT_MS = 300;
// a measured loudness of the result moves the A/B level only when it moved this much
const RESULT_LUFS_STEP = 0.5;

const ui = {
  status: byId<HTMLElement>("status"),
  play: byId<HTMLButtonElement>("play"),
  loop: byId<HTMLButtonElement>("loop"),
  time: byId<HTMLElement>("time"),
  total: byId<HTMLElement>("total"),
  ab: byId<HTMLElement>("ab"),
  abGain: byId<HTMLElement>("ab-gain"),
  refPick: byId<HTMLElement>("ref-pick"),
  render: byId<HTMLButtonElement>("render"),
  save: byId<HTMLButtonElement>("save"),
  track: byId<HTMLElement>("track-name"),
  wave: byId<HTMLElement>("wave"),
  canvas: byId<HTMLCanvasElement>("wave-canvas"),
  lanes: byId<HTMLElement>("lanes"),
  tools: byId<HTMLElement>("tools"),
  desk: byId<HTMLElement>("desk"),
  stripSlot: byId<HTMLElement>("strip-slot"),
  analysis: byId<HTMLElement>("analysis-state"),
};

type Tone = "plain" | "busy" | "error" | "done";

function status(text: string, tone: Tone = "plain", kind = ""): void {
  // a stopped engine stays on screen: nothing else matters until the page is reloaded
  if (ui.status.dataset.kind === "engine") return;
  ui.status.textContent = text;
  ui.status.dataset.tone = tone;
  ui.status.dataset.kind = kind;
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
  #numbers: Analysis;
  #presetList: Record<string, Settings>;
  #analysing: AbortController | null = null;
  #analysisTimer = 0;
  #loop: Loop;
  #loopOn = true;
  #playing = false;
  #toggledAt = 0;
  #anchor = { frame: 0, at: 0 };
  #frame = 0;
  #side: Source = "result";
  readonly #lufs: Record<Source, number | null> = { result: null, original: null, reference: null, render: null };
  #reference: { index: number; name: string; data: Float32Array } | null = null;
  #referenceSeq = 0;
  #render: { key: string; data: Float32Array; lufs: number | null; seconds: number } | null = null;
  #renderSeq = 0;
  #rendering: AbortController | null = null;

  constructor(session: SessionReply, client: EngineClient, ctx: AudioContext, original: Stem) {
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
    this.#history = new History(this.#settings);

    ui.track.textContent = session.track.name;
    ui.total.textContent = clock(session.track.seconds);
    this.#wave = new Waveform(ui.wave, ui.canvas, {
      seek: (s) => this.#seek(s),
      loop: (loop, done) => this.#setLoop(loop, done),
    });
    this.#wave.setAudio(original.data, this.#rate);
    ui.wave.setAttribute("aria-valuemax", session.track.seconds.toFixed(1));

    this.#chain = new ChainPanels(session.schema.settings, session.schema.groups, {
      input: (key, v) => this.#change({ ...this.#settings, [key]: v }, false),
      commit: (key, v) => this.#change({ ...this.#settings, [key]: v }, true),
    });
    this.#rack = new RackEditor(session.schema.effects, {
      input: (rack) => this.#change({ ...this.#settings, rack }, false),
      commit: (rack) => this.#change({ ...this.#settings, rack }, true),
    });
    this.#lanes = new LanesView(ui.lanes, session.schema.effects, original.frames / this.#rate, {
      input: (rack) => this.#change({ ...this.#settings, rack }, false),
      commit: (rack) => this.#change({ ...this.#settings, rack }, true),
    });
    const panels = this.#chain.panels;
    const grid = h("div", { class: "panels" }, ...panels.slice(0, 4), this.#rack.root, ...panels.slice(4));
    ui.desk.append(this.#chain.flow, grid);
    ui.stripSlot.replaceWith(this.#meters.root);
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
    ui.ab.prepend(this.#ab.root);
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
    ui.refPick.append(this.#refs.root);

    const view = new Segmented<"quick" | "full">(
      "Какие настройки показывать",
      [
        { value: "quick", label: "Быстро" },
        { value: "full", label: "Всё" },
      ],
      "quick",
      (v) => {
        document.body.dataset.view = v;
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
    ui.tools.append(
      h("div", { class: "tools__group" }, view.root, this.#undo, this.#redo, reset),
      h("div", { class: "tools__group" }, this.#presets.root, presetName, presetSave),
    );

    this.#loop = loudestSpan(original.data, this.#rate, Math.min(LOOP_SECONDS, session.track.seconds));
    this.#wire();
    this.#refresh();
  }

  start(): void {
    const s = this.#session;
    if (s.racks) {
      const racks = { vocal: parseRack(s.racks.vocal), beat: parseRack(s.racks.beat), master: parseRack(s.racks.master) };
      this.#pushRacks(racks);
    } else {
      status(`Живые рэки не собрались: ${s.racks_error ?? "без причины"}. Поправь настройки — звук пойдёт без обработки, пока рэк не соберётся.`, "error", "rack");
    }
    this.#setLoop(this.#loop, true);
    this.#measureOriginal();
    this.#listen("result");
    if (s.racks) status("Готово. Пробел — играть. Крути — звук меняется сразу.", "done");
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
      if (ui.status.dataset.tone === "error" && ui.status.dataset.kind === "rack") status("Рэк снова собрался.", "done");
    } catch (error) {
      status(`Рэк с этими настройками не собрать: ${message(error)}`, "error", "rack");
    }
  }

  #scheduleAnalysis(): void {
    const analysed = this.#numbers.settings;
    if (analysed && !needsAnalysis(changedKeys(analysed, this.#settings), this.#stale)) return;
    clearTimeout(this.#analysisTimer);
    this.#analysisTimer = window.setTimeout(() => void this.#analyse(), ANALYSIS_WAIT_MS);
  }

  async #analyse(): Promise<void> {
    this.#analysing?.abort();
    const control = new AbortController();
    this.#analysing = control;
    const asked = structuredClone(this.#settings);
    const key = JSON.stringify(asked);
    ui.analysis.dataset.state = "busy";
    ui.analysis.textContent = "пересчитываю анализ";
    try {
      const reply = await api.analysis(asked, control.signal);
      if (control.signal.aborted) return;
      this.#numbers = reply.analysis;
      const lanes = reply.lanes.map((l) => (Array.isArray(l) ? { slot: l[0], param: l[1], points: l[2] } : l));
      if (JSON.stringify(this.#settings) === key) {
        const r = reply.racks;
        this.#pushRacks({ vocal: parseRack(r.vocal), beat: parseRack(r.beat), master: parseRack(r.master) }, lanes);
      } else {
        this.#pushRacks();
      }
      ui.analysis.dataset.state = "done";
      ui.analysis.textContent = "анализ свежий";
    } catch (error) {
      if (control.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) return;
      ui.analysis.dataset.state = "error";
      ui.analysis.textContent = "анализ не прошёл";
      status(`Анализ не прошёл: ${message(error)}. Звук идёт с прошлыми числами.`, "error");
    }
  }

  #applyPreset(name: string): void {
    const preset = this.#presetList[name];
    if (!preset) return;
    this.#change({ ...structuredClone(this.#defaults), ...structuredClone(preset) }, true);
    status(`Пресет «${name}».`, "done");
  }

  #showPresets(): void {
    this.#presets.setOptions(Object.keys(this.#presetList).map((n) => ({ value: n, label: n })));
  }

  async #savePreset(input: HTMLInputElement): Promise<void> {
    const name = input.value.trim();
    if (!name) {
      status("Дай пресету имя.", "error");
      input.focus();
      return;
    }
    try {
      await api.preset(name, this.#settings);
      const differs = Object.fromEntries(changedKeys(this.#defaults, this.#settings).map((k) => [k, this.#settings[k]]));
      this.#presetList = { ...this.#presetList, [name]: differs };
      this.#showPresets();
      input.value = "";
      status(`Пресет «${name}» сохранён.`, "done");
    } catch (error) {
      status(`Пресет не сохранён: ${message(error)}`, "error");
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
      status(`Движок звука остановился: ${reason}. Убери тяжёлые эффекты и обнови страницу.`, "error", "engine");
    };
    c.onError = (e) => status(`Движок: ${e.track ? `${trackName(e.track)} — ` : ""}${e.message}`, "error");
    ui.play.addEventListener("click", () => void this.#togglePlay());
    ui.loop.addEventListener("click", () => this.#setLoopOn(!this.#loopOn));
    ui.render.addEventListener("click", () => void this.#exactRender());
    ui.save.addEventListener("click", () => void this.#save());
    document.addEventListener("keydown", (e) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.isContentEditable || (t instanceof HTMLInputElement && t.type !== "range") || t instanceof HTMLTextAreaElement);
      const action = keyAction(e, typing);
      if (!action) return;
      // a focused button takes Space as its own click
      if (action === "play" && t instanceof HTMLButtonElement && t !== ui.play) return;
      e.preventDefault();
      if (action === "play") void this.#togglePlay();
      else if (action === "loop") this.#setLoopOn(!this.#loopOn);
      else if (action === "ab") this.#listen(this.#side === "original" ? "result" : "original");
      else if (action === "reference" && this.#reference) this.#listen(this.#side === "reference" ? "result" : "reference");
      else if (action === "undo") this.#undo.click();
      else if (action === "redo") this.#redo.click();
    });
  }

  async #togglePlay(): Promise<void> {
    await this.#ctx.resume();
    this.#playing = !this.#playing;
    this.#toggledAt = performance.now();
    this.#anchor = { frame: this.#frame, at: this.#ctx.currentTime };
    this.#client.play(this.#playing);
    this.#showPlay();
    if (this.#playing) this.#tick();
  }

  #showPlay(): void {
    ui.play.setAttribute("aria-pressed", String(this.#playing));
    ui.play.setAttribute("aria-label", this.#playing ? "Пауза (пробел)" : "Играть (пробел)");
    document.body.dataset.playing = String(this.#playing);
  }

  #seek(seconds: number): void {
    const frame = Math.round(seconds * this.#rate);
    this.#frame = frame;
    this.#anchor = { frame, at: this.#ctx.currentTime };
    this.#client.seek(frame);
    this.#showPosition(frame / this.#rate);
  }

  #setLoop(loop: Loop, done: boolean): void {
    this.#loop = loop;
    this.#wave.setLoop(loop, this.#loopOn);
    this.#lanes.setLoop(loop, this.#loopOn);
    if (!done) return;
    const frame = this.#frame;
    // the engine jumps into a loop that the position is outside of; the clock follows it
    if (this.#loopOn && (frame < loop.start * this.#rate || frame >= loop.end * this.#rate)) {
      this.#frame = Math.round(loop.start * this.#rate);
      this.#anchor = { frame: this.#frame, at: this.#ctx.currentTime };
      this.#showPosition(loop.start);
    }
    this.#client.loop(this.#loopOn, Math.round(loop.start * this.#rate), Math.round(loop.end * this.#rate));
    ui.loop.title = `Петля ${clock(loop.start)}–${clock(loop.end)} (L)`;
    this.#measureOriginal();
    if (this.#reference) this.#placeReference();
  }

  #setLoopOn(on: boolean): void {
    this.#loopOn = on;
    ui.loop.setAttribute("aria-pressed", String(on));
    this.#setLoop(this.#loop, true);
  }

  #levels(l: Levels): void {
    this.#frame = l.frame;
    this.#anchor = { frame: l.frame, at: this.#ctx.currentTime };
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
    let frame = this.#anchor.frame + (this.#ctx.currentTime - this.#anchor.at - latency) * this.#rate;
    if (this.#loopOn && frame >= this.#loop.end * this.#rate) {
      const start = this.#loop.start * this.#rate;
      frame = start + ((frame - start) % ((this.#loop.end - this.#loop.start) * this.#rate));
    }
    this.#showPosition(Math.max(0, frame) / this.#rate);
    requestAnimationFrame(() => this.#tick());
  }

  #showPosition(seconds: number): void {
    ui.time.textContent = clock(seconds);
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
    ui.abGain.textContent = Math.abs(offset) < 0.05 ? "уровень как есть" : `тише на ${formatNumber(-offset, 1)} дБ для честного сравнения`;
  }

  // the first reference loads quietly at the start, so the status keeps its "ready"
  async #loadReference(index: number, quiet = false): Promise<void> {
    const chosen = this.#session.references.find((r) => r.index === index);
    if (!chosen) return;
    const seq = ++this.#referenceSeq;
    this.#refs.set(index);
    if (!quiet) status(`Загружаю референс «${chosen.name}»…`, "busy");
    try {
      const audio = await api.reference(index);
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
      if (!quiet) status(`Референс «${chosen.name}»: R или кнопка «Референс». Играет его самый громкий кусок длиной с петлю, на той же громкости.`, "done");
    } catch (error) {
      if (seq !== this.#referenceSeq) return;
      status(`Референс не загрузился: ${message(error)}`, "error");
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
    document.body.dataset.rendering = "true";
    ui.render.setAttribute("aria-busy", "true");
    const tick = window.setInterval(() => status(`Точный рендер через Python: ${Math.round((performance.now() - began) / 1000)} с… Играть и крутить можно.`, "busy"), 250);
    try {
      const audio = await api.render(settings, this.#renderSeq, control.signal);
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
      status(`Точный рендер готов за ${Math.round(seconds)} с.${parity} Его можно слушать (кнопка «Рендер») и сохранять.`, "done");
    } catch (error) {
      if (error instanceof Superseded || (error instanceof DOMException && error.name === "AbortError")) return;
      status(`Рендер не прошёл: ${message(error)}`, "error");
    } finally {
      window.clearInterval(tick);
      if (this.#rendering === control) {
        document.body.dataset.rendering = "false";
        ui.render.removeAttribute("aria-busy");
        this.#showSave();
      }
    }
  }

  #showSave(): void {
    const exact = this.#render?.key === JSON.stringify(this.#settings);
    ui.save.disabled = !exact;
    ui.save.title = exact ? "Сохранить настройки этого рендера" : "Сначала точный рендер с этими настройками";
  }

  async #save(): Promise<void> {
    try {
      const { path } = await api.save(this.#settings);
      status(`Сохранено: ${path}`, "done");
    } catch (error) {
      status(error instanceof Superseded ? "Сначала точный рендер с этими настройками, потом сохранение." : `Не сохранилось: ${message(error)}`, "error");
    }
  }
}

function trackName(t: Track): string {
  return t === "vocal" ? "голос" : t === "beat" ? "бит" : "мастер";
}

async function loadStem(name: "vocal" | "beat" | "original", rate: number): Promise<Stem> {
  const audio = await api.stem(name);
  const wav = parseWav(audio.bytes);
  if (wav.rate !== rate) throw new Error(`стем ${name} в ${wav.rate} Гц, трек в ${rate}`);
  return wav;
}

async function boot(): Promise<void> {
  document.body.dataset.state = "loading";
  status("Загружаю трек и считаю анализ. На длинном треке это до минуты.", "busy");
  const session = await api.session();
  const rate = session.track.rate;
  status("Загружаю стемы и движок…", "busy");
  const [host, vocal, beat, original] = await Promise.all([
    startHost(rate, catalogueOf(session.schema.effects)),
    loadStem("vocal", rate),
    loadStem("beat", rate),
    loadStem("original", rate),
  ]);
  if (host.ctx.sampleRate !== rate) throw new Error(`браузер не открыл звук на ${rate} Гц (дал ${host.ctx.sampleRate})`);
  const studio = new Studio(session, host.client, host.ctx, original);
  host.client.loadStems(vocal.frames, vocal.data, beat.data, original.data.slice());
  studio.start();
  document.body.dataset.state = "ready";
}

boot().catch((error: unknown) => {
  document.body.dataset.state = "failed";
  status(`Студия не загрузилась: ${message(error)}`, "error");
});

