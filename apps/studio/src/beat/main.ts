// Wires the beat editor: the session to the strip, grid, lane and side panel, every edit to
// the history, the live engine and a debounced check, and the save, render and marks to the
// server.

import { formatNumber } from "../core/format.ts";
import { History } from "../core/history.ts";
import { byId, h } from "../ui/dom.ts";
import { Refused, api, type BeatSession, type Caution, type Mark } from "./api.ts";
import { startBeat, type BeatClient, type BeatPosition, type BeatResult } from "./client.ts";
import { beatKey, type Zone } from "./keys.ts";
import {
  STEPS_PER_BAR,
  barAtFrame,
  barFrame,
  glideCurves,
  isNoteRow,
  liveScore,
  locate,
  makeUnique,
  nextScoreName,
  playingAt,
  position,
  faultsOf,
  serialize,
  setGain,
  setPan,
  setRack,
  setSoundParam,
  songBars,
  splitIssue,
  stepFrames,
  targetOf,
  type Faults,
  type Glide,
  type InstrumentSpec,
  type PlayingRow,
  type Score,
} from "./model.ts";
import { Tables } from "./tables.ts";
import { pathText, sourceText, warningText, whereText } from "./warnings.ts";
import { Grid, type Selection } from "./ui/grid.ts";
import { StepLane } from "./ui/lane.ts";
import { BeatMeter } from "./ui/meter.ts";
import { SoundPanel } from "./ui/side.ts";
import { SongStrip, marksByBar } from "./ui/strip.ts";

const CHECK_WAIT_MS = 300;
const RENDER_POLL_MS = 500;

const ui = {
  seed: byId<HTMLElement>("seed-name"),
  status: byId<HTMLElement>("status"),
  play: byId<HTMLButtonElement>("play"),
  playFrom: byId<HTMLButtonElement>("play-from"),
  loop: byId<HTMLButtonElement>("loop"),
  pos: byId<HTMLElement>("pos"),
  bars: byId<HTMLElement>("bars"),
  tempo: byId<HTMLElement>("tempo"),
  key: byId<HTMLElement>("key"),
  meterSlot: byId<HTMLElement>("meter-slot"),
  check: byId<HTMLElement>("check-state"),
  undo: byId<HTMLButtonElement>("undo"),
  redo: byId<HTMLButtonElement>("redo"),
  render: byId<HTMLButtonElement>("render"),
  save: byId<HTMLButtonElement>("save"),
  issues: byId<HTMLElement>("issues"),
  renderOut: byId<HTMLElement>("render-out"),
  songSlot: byId<HTMLElement>("song-slot"),
  main: byId<HTMLElement>("work-main"),
  sideSlot: byId<HTMLElement>("side-slot"),
};

type Tone = "plain" | "busy" | "error" | "done";

function status(text: string, tone: Tone = "plain", kind = ""): void {
  if (ui.status.dataset.kind === "engine") return;
  ui.status.textContent = text;
  ui.status.dataset.tone = tone;
  ui.status.dataset.kind = kind;
}

// replaceChildren takes no nulls, unlike h()
const present = (...nodes: (Node | null)[]): Node[] => nodes.filter((n): n is Node => n !== null);

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function plural(n: number, one: string, few: string, many: string): string {
  const d = n % 10;
  const t = n % 100;
  if (d === 1 && t !== 11) return one;
  if (d >= 2 && d <= 4 && (t < 12 || t > 14)) return few;
  return many;
}

// sends only the newest of what piles up while one call is out
class Latest<T> {
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
}

class Editor {
  readonly #session: BeatSession;
  readonly #client: BeatClient;
  readonly #ctx: AudioContext;
  readonly #tables: Tables | null;
  readonly #rate: number;
  readonly #instruments: Map<string, InstrumentSpec>;
  readonly #history: History<Score>;
  readonly #strip: SongStrip;
  readonly #grid: Grid;
  readonly #lane: StepLane;
  readonly #side: SoundPanel;
  readonly #meter = new BeatMeter();
  readonly #live: Latest<string>;
  readonly #racks = new Map<string, Latest<string>>();
  #score: Score;
  #savedKey: string;
  #base: string;
  #marks: Mark[];
  #bar = 0;
  #sel: Selection | null = null;
  #column = 0;
  #note: number | null = null;
  #muted: ReadonlySet<string> = new Set();
  #solo: ReadonlySet<string> = new Set();
  #glides: readonly Glide[] = [];
  #tableQueued = false;
  #checkTimer = 0;
  #checkSeq = 0;
  #errors: string[] = [];
  #faults: Faults = { rows: new Set(), notes: new Map(), steps: new Map() };
  #warnings: Caution[] = [];
  #checking = true;
  #liveError = false;
  #playing = false;
  #loopOn = false;
  #anchor = { frame: 0, at: 0 };
  #frame = 0;
  #toggledAt = 0;
  #renderUrl: string | null = null;
  #rendering = false;

  constructor(session: BeatSession, client: BeatClient, ctx: AudioContext, tables: Tables | null) {
    this.#session = session;
    this.#client = client;
    this.#ctx = ctx;
    this.#tables = tables;
    this.#rate = session.rate;
    this.#score = session.score;
    this.#savedKey = serialize(session.score);
    this.#base = session.score_file;
    this.#marks = [...session.marks];
    this.#instruments = new Map(session.instruments.map((i) => [i.instrument, i]));
    this.#history = new History(session.score);

    this.#live = new Latest((text) => this.#sendLive(text));
    this.#strip = new SongStrip({
      select: (bar) => this.#select(bar),
      playFrom: (bar) => void this.#playFrom(bar),
      mark: (bar, verdict, note) => this.#mark(bar, verdict, note),
    });
    this.#grid = new Grid({
      select: (sel, column, note) => {
        this.#sel = sel;
        if (column !== undefined) this.#column = column;
        if (note !== undefined) this.#note = note;
        this.#refresh();
      },
      edit: (next, commit) => this.#change(next, commit),
      mute: (sound) => this.#toggleLive("mute", sound),
      solo: (sound) => this.#toggleLive("solo", sound),
      unique: (pattern) => this.#makeUnique(pattern),
      bar: (bar) => this.#select(bar),
    });
    this.#lane = new StepLane({
      edit: (next, commit) => this.#change(next, commit),
      column: (column) => {
        this.#column = column;
        this.#refresh();
      },
    });
    this.#side = new SoundPanel(session.effects, {
      param: (sound, name, value, commit) => this.#change(setSoundParam(this.#score, sound, name, value), commit),
      gain: (sound, db, commit) => this.#change(setGain(this.#score, sound, db), commit),
      pan: (sound, pan, commit) => this.#change(setPan(this.#score, sound, pan), commit),
      rack: (sound, rack, commit) => {
        this.#rackSender(sound).push(JSON.stringify({ effects: rack }));
        this.#change(setRack(this.#score, sound, rack), commit, false);
      },
    });
    ui.songSlot.replaceWith(this.#strip.root);
    ui.main.append(this.#grid.root, this.#lane.root);
    ui.sideSlot.append(this.#side.root);
    ui.meterSlot.replaceWith(this.#meter.root);
    this.#meter.show(0, 0, false);
    ui.seed.textContent = `бит · сид ${session.seed}`;
    if (session.events) this.#glides = glideCurves(session.events, session.score, session.instruments);
    this.#select(0);
    this.#wire();
  }

  async start(): Promise<void> {
    const loaded = await this.#client.load(serialize(this.#heard()));
    this.#showLive(loaded, "Партитура не играет");
    await this.#loadZones();
    this.#queueTable();
    this.#scheduleCheck(0);
    if (loaded.ok) status("Готово. Пробел — играть. Клик по шагу меняет его, звук слышен сразу.", "done");
  }

  // ---------- edits ----------

  #heard(): Score {
    return liveScore(this.#score, this.#muted, this.#solo);
  }

  #change(next: Score, commit: boolean, live = true): void {
    if (next === this.#score && !commit) return;
    this.#score = next;
    if (commit) this.#history.push(next);
    this.#refresh();
    if (live) this.#live.push(serialize(this.#heard()));
    this.#queueTable();
    this.#scheduleCheck(CHECK_WAIT_MS);
  }

  #step(to: Score | null): void {
    if (!to) return;
    this.#score = to;
    this.#keepSelection();
    this.#refresh();
    this.#live.push(serialize(this.#heard()));
    // an undone rack edit reaches the engine through the update's own rack swaps
    this.#queueTable();
    this.#scheduleCheck(CHECK_WAIT_MS);
  }

  #toggleLive(kind: "mute" | "solo", sound: string): void {
    const set = new Set(kind === "mute" ? this.#muted : this.#solo);
    if (set.has(sound)) set.delete(sound);
    else set.add(sound);
    if (kind === "mute") this.#muted = set;
    else this.#solo = set;
    this.#refresh();
    this.#live.push(serialize(this.#heard()));
  }

  #makeUnique(pattern: string): void {
    const at = locate(this.#score, this.#bar);
    if (!at) return;
    try {
      const made = makeUnique(this.#score, at.section, pattern);
      if (this.#sel?.pattern === pattern) this.#sel = { ...this.#sel, pattern: made.name };
      this.#change(made.score, true);
      status(`Секция «${this.#score.sections[at.section]?.name ?? ""}» теперь играет свою копию: ${made.name}. Другие секции остались на ${pattern}.`, "done");
    } catch (error) {
      status(`Копия не сделана: ${message(error)}`, "error");
    }
  }

  #rackSender(sound: string): Latest<string> {
    let sender = this.#racks.get(sound);
    if (!sender) {
      sender = new Latest(async (rack) => {
        this.#showLive(await this.#client.setRack(sound, rack), `Рэк ${sound} не собрался`);
      });
      this.#racks.set(sound, sender);
    }
    return sender;
  }

  async #sendLive(text: string): Promise<void> {
    this.#showLive(await this.#client.update(text), "Живой звук не обновился");
  }

  #showLive(result: BeatResult, what: string): void {
    if (!result.ok) {
      this.#liveError = true;
      status(`${what}: ${result.errors.slice(0, 3).join("; ")}`, "error", "live");
      return;
    }
    if (this.#liveError) {
      this.#liveError = false;
      status(result.warnings.length ? `Звук снова идёт; ${result.warnings.join("; ")}` : "Звук снова идёт.", "done");
    } else if (result.warnings.length) {
      status(result.warnings.join("; "), "plain");
    }
  }

  // ---------- event table ----------

  #queueTable(): void {
    if (this.#tableQueued || !this.#tables) return;
    this.#tableQueued = true;
    requestAnimationFrame(() => {
      this.#tableQueued = false;
      const tables = this.#tables;
      if (!tables) return;
      const made = tables.events(serialize(this.#score));
      // a score the core refuses keeps the last glides drawn; the check lists why
      if (!made.ok) return;
      this.#glides = glideCurves(made.table, this.#score, this.#session.instruments);
      this.#refresh();
    });
  }

  // ---------- check ----------

  #scheduleCheck(wait: number): void {
    clearTimeout(this.#checkTimer);
    this.#checking = true;
    this.#showCheck();
    this.#checkTimer = window.setTimeout(() => void this.#check(), wait);
  }

  async #check(): Promise<void> {
    const seq = ++this.#checkSeq;
    const score = this.#score;
    try {
      const reply = await api.check(score);
      if (seq !== this.#checkSeq) return;
      this.#warnings = reply.warnings;
      this.#checking = false;
      this.#setErrors(reply.errors);
    } catch (error) {
      if (seq !== this.#checkSeq) return;
      this.#checking = false;
      this.#warnings = [];
      this.#setErrors([`проверка не прошла: ${message(error)}`]);
    }
    this.#showCheck();
    this.#showIssues();
  }

  // a new faults object only when the errors change, so the grid redraws only then
  #setErrors(errors: string[]): void {
    const same = errors.length === this.#errors.length && errors.every((e, i) => e === this.#errors[i]);
    this.#errors = errors;
    if (same) return;
    this.#faults = faultsOf(errors, this.#score);
    this.#refresh();
  }

  #showCheck(): void {
    const el = ui.check;
    const e = this.#errors.length;
    const w = this.#warnings.length;
    this.#showSave();
    if (this.#checking) {
      el.dataset.state = "busy";
      el.textContent = "проверяю";
      el.title = "";
      return;
    }
    el.dataset.state = e ? "error" : "done";
    el.title = e || w ? "Показать список" : "";
    el.textContent = e
      ? `${e} ${plural(e, "ошибка", "ошибки", "ошибок")}${w ? ` · ${w} ${plural(w, "предупр.", "предупр.", "предупр.")}` : ""}`
      : w
        ? `ошибок нет · ${w} ${plural(w, "предупреждение", "предупреждения", "предупреждений")}`
        : "ошибок нет";
  }

  #showIssues(): void {
    const box = ui.issues;
    if (!this.#errors.length && !this.#warnings.length) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const item = (path: string, text: string, kind: "error" | "warning", shown = pathText(path)) => {
      const target = path ? targetOf(path, this.#score) : null;
      const body = [h("span", { class: "issue__path num", title: path }, shown || "—"), h("span", { class: "issue__text" }, text)];
      if (!target) return h("li", { class: "issue", "data-kind": kind }, ...body);
      const b = h("button", { class: "issue__go", type: "button", title: "Показать место" }, ...body);
      b.addEventListener("click", () => this.#goTo(path));
      return h("li", { class: "issue", "data-kind": kind }, b);
    };
    const errors = this.#errors.map((line) => {
      const { path, message: text } = splitIssue(line);
      return item(path, text, "error");
    });
    const warnings = this.#warnings.map((w) => item(w.where, `${warningText(w)} · ${sourceText(w.source)}`, "warning", whereText(w.where)));
    box.replaceChildren(
      ...present(
      errors.length ? h("div", { class: "issues__group" }, h("h2", { class: "issues__title" }, `Ошибки: ${errors.length}`), h("ul", { class: "issues__list" }, ...errors)) : null,
      warnings.length
        ? h(
            "details",
            { class: "issues__group", open: errors.length === 0 && warnings.length <= 4 },
            h("summary", { class: "issues__title" }, `Предупреждения: ${warnings.length}`, h("span", { class: "issues__why" }, " — не мешают сохранить")),
            h("ul", { class: "issues__list" }, ...warnings),
          )
        : null,
      ),
    );
    box.hidden = false;
  }

  #goTo(path: string): void {
    const target = targetOf(path, this.#score);
    if (!target) return;
    if (target.kind === "sound") {
      const bar = this.#barPlaying(target.sound);
      if (bar !== null) this.#select(bar, { sound: target.sound });
      else {
        this.#sel = { pattern: "", sound: target.sound };
        this.#refresh();
      }
      this.#side.root.querySelector<HTMLElement>("button, [tabindex='0']")?.focus();
      return;
    }
    if (target.kind === "bar") {
      this.#select(target.bar);
      this.#strip.focusBar(target.bar);
      return;
    }
    this.#select(target.bar, { pattern: target.pattern, sound: target.sound });
    if (target.step !== null) this.#column = target.step % STEPS_PER_BAR;
    this.#refresh();
    this.#grid.focusSelected();
  }

  #barPlaying(sound: string): number | null {
    const total = songBars(this.#score);
    for (let bar = 0; bar < total; bar++) if (playingAt(this.#score, bar).some((r) => r.sound === sound)) return bar;
    return null;
  }

  // ---------- selection ----------

  #rows(): PlayingRow[] {
    return playingAt(this.#score, this.#bar);
  }

  #select(bar: number, want?: { pattern?: string; sound: string }): void {
    const total = songBars(this.#score);
    if (total <= 0) return;
    const before = locate(this.#score, this.#bar)?.section;
    this.#bar = Math.min(total - 1, Math.max(0, bar));
    if (want) this.#sel = { pattern: want.pattern ?? "", sound: want.sound };
    this.#keepSelection();
    const after = locate(this.#score, this.#bar)?.section;
    if (this.#loopOn && before !== after) void this.#applyLoop();
    this.#refresh();
  }

  // the selection follows its sound to the row that plays it at the new bar
  #keepSelection(): void {
    const rows = this.#rows();
    const sel = this.#sel;
    const exact = sel && rows.find((r) => r.pattern === sel.pattern && r.sound === sel.sound);
    const bySound = sel && rows.find((r) => r.sound === sel.sound && !r.replaced);
    const pick = exact ?? bySound ?? rows.find((r) => !r.replaced) ?? rows[0] ?? null;
    if (!pick) return;
    if (!exact) this.#note = null;
    this.#sel = { pattern: pick.pattern, sound: pick.sound };
  }

  #refresh(): void {
    const score = this.#score;
    const dirty = serialize(score) !== this.#savedKey;
    const at = locate(score, this.#bar);
    const rows = this.#rows();
    this.#strip.show(score, marksByBar(this.#marks), this.#bar, dirty, this.#base);
    this.#grid.show({
      score,
      bar: this.#bar,
      rate: this.#rate,
      rows,
      section: at?.section ?? 0,
      selected: this.#sel,
      column: this.#column,
      note: this.#note,
      muted: this.#muted,
      solo: this.#solo,
      instruments: this.#instruments,
      glides: this.#glides,
      faults: this.#faults,
    });
    const row = rows.find((r) => r.pattern === this.#sel?.pattern && r.sound === this.#sel.sound);
    const raw = row ? score.patterns[row.pattern]?.rows[row.sound] : undefined;
    const drum = row && raw !== undefined && !isNoteRow(raw);
    this.#lane.show(drum && row ? { score, pattern: row.pattern, sound: row.sound, patternBar: row.patternBar, column: this.#column } : null);
    this.#side.show(score, this.#sel?.sound ?? null, this.#instruments, this.#session.libraries);
    ui.undo.disabled = !this.#history.canUndo;
    ui.redo.disabled = !this.#history.canRedo;
    const next = nextScoreName(this.#base) ?? "новый файл";
    ui.save.disabled = !dirty;
    this.#showSave();
    ui.save.textContent = dirty ? `Сохранить → ${next}` : "Сохранено";
    ui.save.title = dirty ? `Проверить и сохранить как ${next}; ${this.#base} останется как был` : `Правок нет: открыт ${this.#base}`;
    ui.render.title = dirty ? `Рендер сохранённого ${this.#base}: несохранённых правок в нём не будет` : `Рендер ${this.#base} через Python, точно как выйдет файл`;
    document.body.dataset.dirty = String(dirty);
    ui.tempo.textContent = `${formatNumber(score.bpm, 0)} BPM`;
    ui.key.textContent = score.key;
    ui.bars.textContent = String(songBars(score));
  }

  // with errors the save is not the page's main action: the server would refuse it
  #showSave(): void {
    const blocked = !this.#checking && this.#errors.length > 0;
    ui.save.classList.toggle("btn--signal", !blocked);
        if (blocked && !ui.save.disabled) ui.save.title = `Не сохранится, пока есть ошибки (${this.#errors.length}): список под строкой статуса`;
  }

  // ---------- transport ----------

  #wire(): void {
    const c = this.#client;
    c.subscribe((p) => this.#position(p));
    c.onEnded = () => {
      this.#playing = false;
      this.#showPlay();
    };
    c.onFatal = (reason) => {
      this.#playing = false;
      this.#showPlay();
      ui.play.disabled = true;
      ui.playFrom.disabled = true;
      status(`Движок звука остановился: ${reason}. Обнови страницу; несохранённые правки пропадут — сначала сохрани.`, "error", "engine");
    };
    ui.play.addEventListener("click", () => void this.#togglePlay());
    ui.playFrom.addEventListener("click", () => void this.#playFrom(this.#bar));
    ui.loop.addEventListener("click", () => {
      this.#loopOn = !this.#loopOn;
      ui.loop.setAttribute("aria-pressed", String(this.#loopOn));
      void this.#applyLoop();
    });
    ui.undo.addEventListener("click", () => this.#step(this.#history.undo()));
    ui.redo.addEventListener("click", () => this.#step(this.#history.redo()));
    ui.save.addEventListener("click", () => void this.#save());
    ui.render.addEventListener("click", () => void this.#exactRender());
    ui.check.addEventListener("click", () => {
      if (ui.issues.hidden) return;
      ui.issues.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      const first = ui.issues.querySelector<HTMLElement>('.issue[data-kind="error"] .issue__go') ?? ui.issues.querySelector<HTMLElement>(".issue__go, summary");
      (first ?? ui.issues).focus({ preventScroll: true });
    });
    document.addEventListener("keydown", (e) => this.#key(e));
  }

  #key(e: KeyboardEvent): void {
    const t = e.target as HTMLElement | null;
    const typing = !!t && (t.isContentEditable || t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement);
    let zone: Zone = "other";
    if (t?.closest(".song__bars")) zone = "strip";
    else if (t?.closest(".steps")) zone = "grid";
    else if (t?.closest(".roll")) zone = "roll";
    const action = beatKey(e, zone, typing);
    if (!action) return;
    // a focused button takes Space as its own press, but steps and bars are played over
    if (action.type === "play" && t instanceof HTMLButtonElement && t !== ui.play && zone === "other") return;
    if (action.type === "play" && t?.closest(".drop__list, .adder")) return;
    switch (action.type) {
      case "play":
        e.preventDefault();
        void this.#togglePlay();
        return;
      case "undo":
        e.preventDefault();
        ui.undo.click();
        return;
      case "redo":
        e.preventDefault();
        ui.redo.click();
        return;
      case "bar": {
        e.preventDefault();
        this.#select(this.#bar + action.by);
        if (zone === "strip") this.#strip.focusBar(this.#bar);
        return;
      }
      case "move":
        if (zone === "strip") {
          e.preventDefault();
          this.#select(this.#bar + action.dx * (action.wide ? 4 : 1));
          this.#strip.focusBar(this.#bar);
          return;
        }
        break;
      case "mark":
        e.preventDefault();
        this.#strip.openMark(action.verdict);
        return;
      case "playFrom":
        e.preventDefault();
        void this.#playFrom(this.#bar);
        return;
      default:
        break;
    }
    if ((zone === "grid" || zone === "roll") && this.#grid.key(action)) {
      e.preventDefault();
      this.#grid.focusSelected();
    }
  }

  async #togglePlay(): Promise<void> {
    if (this.#client.dead) return;
    await this.#ctx.resume();
    this.#toggledAt = performance.now();
    if (this.#playing) {
      this.#playing = false;
      this.#showPlay();
      await this.#client.stop();
      return;
    }
    this.#playing = true;
    this.#anchor = { frame: this.#frame, at: this.#ctx.currentTime };
    this.#showPlay();
    await this.#client.play();
    this.#tick();
  }

  async #playFrom(bar: number): Promise<void> {
    if (this.#client.dead) return;
    this.#select(bar);
    await this.#ctx.resume();
    const frame = barFrame(this.#bar, this.#score.bpm, this.#rate);
    this.#frame = frame;
    this.#anchor = { frame, at: this.#ctx.currentTime };
    this.#toggledAt = performance.now();
    await this.#client.seek(frame);
    if (!this.#playing) {
      this.#playing = true;
      this.#showPlay();
      await this.#client.play();
      this.#tick();
    }
  }

  async #applyLoop(): Promise<void> {
    if (!this.#loopOn) {
      ui.loop.title = "Крутить выбранную секцию по кругу";
      await this.#client.loop(null);
      return;
    }
    const at = locate(this.#score, this.#bar);
    const s = at ? this.#score.sections[at.section] : undefined;
    if (!at || !s) return;
    const start = barFrame(at.start, this.#score.bpm, this.#rate);
    const end = barFrame(at.start + s.bars, this.#score.bpm, this.#rate);
    ui.loop.title = `По кругу: «${s.name}», такты ${at.start + 1}–${at.start + s.bars}`;
    await this.#client.loop({ start, end });
  }

  #showPlay(): void {
    ui.play.setAttribute("aria-pressed", String(this.#playing));
    ui.play.setAttribute("aria-label", this.#playing ? "Стоп (пробел)" : "Играть (пробел)");
    document.body.dataset.playing = String(this.#playing);
  }

  #position(p: BeatPosition): void {
    this.#frame = p.frame;
    this.#anchor = { frame: p.frame, at: this.#ctx.currentTime };
    // a report from before the last press may still be on its way
    if (performance.now() - this.#toggledAt > 150 && this.#playing !== p.playing) {
      this.#playing = p.playing;
      this.#showPlay();
      if (p.playing) this.#tick();
    }
    this.#meter.show(p.peakLeft, p.peakRight, p.playing);
    if (!p.playing) this.#showPosition(p.frame);
  }

  #tick(): void {
    if (!this.#playing) return;
    const since = this.#ctx.currentTime - this.#anchor.at;
    this.#showPosition(this.#anchor.frame + Math.max(0, since) * this.#rate);
    requestAnimationFrame(() => this.#tick());
  }

  #showPosition(frame: number): void {
    const bpm = this.#score.bpm;
    const total = songBars(this.#score);
    const at = position(frame, bpm, this.#rate);
    ui.pos.textContent = `${Math.min(at.bar, Math.max(1, total))}:${at.beat}`;
    const bars = frame / (STEPS_PER_BAR * stepFrames(bpm, this.#rate));
    this.#strip.setPlayhead(this.#playing || frame > 0 ? bars : null, total);
    const bar = barAtFrame(frame, bpm, this.#rate);
    const column = Math.floor(frame / stepFrames(bpm, this.#rate)) % STEPS_PER_BAR;
    this.#grid.setPlayStep(this.#playing && bar === this.#bar ? column : -1);
  }

  // ---------- samplers ----------

  async #loadZones(): Promise<void> {
    const libs = this.#session.libraries.filter((l) => l.fetched && l.zones.length);
    const missing = this.#session.libraries.filter((l) => !l.fetched);
    if (!libs.length) {
      if (missing.length) status(`Сэмплер без звука: библиотека ${missing.map((l) => l.name).join(", ")} не скачана — команда в панели звука.`, "error");
      return;
    }
    const all = libs.flatMap((l) => l.zones);
    let done = 0;
    const problems: string[] = [];
    for (const zone of all) {
      status(`Загружаю сэмплы: ${done + 1} из ${all.length}…`, "busy");
      try {
        const data = await api.zone(zone.library, zone.file);
        const result = await this.#client.loadZone({
          library: zone.library,
          root: data.root,
          velLo: zone.vel[0],
          velHi: zone.vel[1],
          rate: data.rate,
          samples: new Int16Array(data.pcm),
          channels: data.channels === 2 ? 2 : 1,
        });
        if (!result.ok) problems.push(`${zone.library} ${zone.file}: ${result.errors.join("; ")}`);
      } catch (error) {
        problems.push(`${zone.library} ${zone.file}: ${message(error)}`);
      }
      done++;
    }
    const rebuilt = await this.#client.rebuildSamplers();
    if (!rebuilt.ok) problems.push(...rebuilt.errors);
    if (problems.length) status(`Не все сэмплы загрузились: ${problems.slice(0, 3).join("; ")}`, "error");
  }

  // ---------- save, render, marks ----------

  async #save(): Promise<void> {
    const score = this.#score;
    ui.save.disabled = true;
    status("Проверяю и сохраняю…", "busy");
    try {
      const { file, warnings } = await api.save(score, this.#base);
      this.#base = file;
      this.#savedKey = serialize(score);
      this.#warnings = warnings;
      this.#setErrors([]);
      this.#showCheck();
      this.#showIssues();
      status(`Сохранено: ${file}.${warnings.length ? ` Предупреждений: ${warnings.length} — список выше.` : ""}`, "done");
    } catch (error) {
      if (error instanceof Refused && error.status === 422) {
        this.#checking = false;
        this.#setErrors(error.errors);
        this.#showCheck();
        this.#showIssues();
        status(`Не сохранено: ошибок ${error.errors.length}. Клик по ошибке покажет её место.`, "error");
        ui.issues.querySelector<HTMLElement>(".issue__go")?.focus();
      } else if (error instanceof Refused && error.status === 409) {
        status(`Не сохранено: на диске есть сохранение новее ${this.#base}. Обнови страницу (правки отсюда пропадут).`, "error");
      } else {
        status(`Не сохранено: ${message(error)}`, "error");
      }
    } finally {
      this.#refresh();
    }
  }

  async #mark(bar: number, verdict: "good" | "bad", note: string): Promise<boolean> {
    try {
      const mark = await api.mark(bar + 1, verdict, note, this.#base);
      this.#marks = [...this.#marks, mark];
      this.#refresh();
      status(`Такт ${bar + 1}: ${verdict === "good" ? "хорошо" : "плохо"} — отметка в ${mark.score}.`, "done");
      return true;
    } catch (error) {
      if (error instanceof Refused && error.status === 409) {
        status(`Отметка не легла: на диске есть сохранение новее ${this.#base}. Обнови страницу.`, "error");
      } else {
        status(`Отметка не легла: ${message(error)}`, "error");
      }
      return false;
    }
  }

  async #exactRender(): Promise<void> {
    if (this.#rendering) return;
    const file = this.#base;
    this.#rendering = true;
    document.body.dataset.rendering = "true";
    ui.render.setAttribute("aria-busy", "true");
    const began = performance.now();
    const out = ui.renderOut;
    const line = h("p", { class: "render-out__line" });
    out.replaceChildren(line);
    out.hidden = false;
    const dirtyNote = serialize(this.#score) !== this.#savedKey ? " Несохранённых правок в нём нет." : "";
    try {
      let s = await api.render(file);
      while (s.state === "running") {
        line.textContent = `Рендер ${file}: ${Math.round((performance.now() - began) / 1000)} с…${dirtyNote}`;
        await new Promise((r) => setTimeout(r, RENDER_POLL_MS));
        s = await api.renderStatus();
      }
      if (s.state !== "done") throw new Error(s.error ?? "рендер не закончился");
      const blob = await api.renderFile("beat.wav");
      if (this.#renderUrl) URL.revokeObjectURL(this.#renderUrl);
      this.#renderUrl = URL.createObjectURL(blob);
      const seconds = s.seconds ?? 0;
      const audio = h("audio", { class: "render-out__audio", controls: true, src: this.#renderUrl, "aria-label": `Точный рендер ${file}` });
      const link = h("a", { class: "btn btn--small", href: this.#renderUrl, download: `beat-${file.replace(/\.json$/, "")}.wav` }, "Скачать beat.wav");
      line.replaceChildren(
        h("span", {}, "Рендер ", h("span", { class: "num" }, file), ": ", h("span", { class: "num" }, `${formatNumber(seconds, 1)} с`), ` за ${Math.round((performance.now() - began) / 1000)} с.`),
      );
      out.replaceChildren(
        ...present(
        line,
        audio,
        link,
        s.non_commercial?.length ? h("p", { class: "render-out__warn" }, `Не для коммерции: ${s.non_commercial.join(", ")}.`) : null,
        ),
      );
      status(`Точный рендер ${file} готов. Сравнивай с живым звуком на одной громкости.`, "done");
    } catch (error) {
      line.textContent = `Рендер ${file} не прошёл: ${message(error)}`;
      status(`Рендер не прошёл: ${message(error)}`, "error");
    } finally {
      this.#rendering = false;
      document.body.dataset.rendering = "false";
      ui.render.removeAttribute("aria-busy");
    }
  }
}

async function boot(): Promise<void> {
  document.body.dataset.state = "loading";
  status("Загружаю бит…", "busy");
  const session = await api.session();
  status("Запускаю движок…", "busy");
  const [host, tables] = await Promise.all([
    startBeat(session.rate),
    Tables.start(session.rate).catch((error: unknown) => {
      console.warn(`event tables are off: ${message(error)}`);
      return null;
    }),
  ]);
  const editor = new Editor(session, host.client, host.ctx, tables);
  if (session.events_error) status(`Партитура не проходит проверку движка: ${session.events_error}`, "error");
  await editor.start();
  document.body.dataset.state = "ready";
}

boot().catch((error: unknown) => {
  document.body.dataset.state = "failed";
  status(`Редактор не загрузился: ${message(error)}`, "error");
});
