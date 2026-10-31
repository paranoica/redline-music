// The flip surface: take a type beat apart into stems, swap its 808 and hats, rearrange its bars,
// paint effects on them, render a version and make it the song's beat.

import { Scope } from "../core/lifecycle.ts";
import type { Mount } from "../core/mount.ts";
import { ApiError } from "../song/api.ts";
import type { Job } from "../song/api.ts";
import { Poller } from "../song/poll.ts";
import { TakePlayer } from "../song/takes.ts";
import { finishedKinds, JOB_STATE, jobActive } from "../song/jobs.ts";
import { h } from "../ui/dom.ts";
import { playButton } from "../ui/play.ts";
import { createFlipApi, DraftError, effectRanges } from "./api.ts";
import type { FlipState, Peaks } from "./api.ts";
import {
  barCount,
  BPM_MAX,
  BPM_MIN,
  deleteBlock,
  draftProblem,
  duplicateBlock,
  finishedSince,
  fitEffects,
  MAX_BARS,
  mayAdoptDraft,
  moveBlock,
  nudgeDownbeat,
  paintEffect,
  playToSource,
  renderOutcome,
  sameBpm,
  scaleBpm,
  setRange,
  splitBlock,
  tapeCap,
  totalBars,
  useConfirm,
} from "./model.ts";
import type { BassMode, FlipDoc, HatsMode, Layout } from "./model.ts";
import { drawWave, numberField, seg, slider } from "./widgets.ts";

const BASS_PARAMS: [string, string, number, number, number, number, string][] = [
  ["tune", "Строй", -12, 12, 0.01, 0, "пт"],
  ["decay", "Затухание", 0.1, 8, 0.05, 1.5, "с"],
  ["drive", "Перегруз", 0, 1, 0.01, 0.2, ""],
  ["glide_ms", "Глайд", 10, 500, 1, 80, "мс"],
  ["click", "Щелчок", 0, 1, 0.01, 0.1, ""],
  ["tone", "Тон", 100, 8000, 10, 2000, "Гц"],
];
const PRESET_NAMES: Record<string, string> = {
  "8ths": "восьмые",
  "16ths": "шестнадцатые",
  trip_roll: "триольный ролл",
  roll32: "ролл 32-х",
};
const EFFECT_NAMES: [string, string][] = [
  ["reverse", "реверс"],
  ["tape_stop", "остановка ленты"],
  ["tape_start", "разгон ленты"],
  ["stutter", "статтер"],
  ["granular", "гранулярка"],
];
const PARAM_NAMES: Record<string, string> = {
  time: "старт",
  duration: "длительность",
  curve: "кривая",
  resume: "возврат",
  length: "длина",
  repeats: "повторы",
  fall: "спад",
  step: "шаг",
  size: "размер",
  density: "плотность",
  scatter: "разброс",
  jitter: "дрожание",
  stretch: "растяжение",
  seed: "seed",
};
const WHOLE = new Set(["repeats", "seed"]);
const SAVE_MS = 600;
const LICENCE =
  "Правка бита не меняет его лицензию: права на исходный бит остаются теми же, что были. Стемы разделены нейросетью и несут её артефакты; хэты, ушедшие в «other», остаются.";

const effectName = (id: string): string => EFFECT_NAMES.find(([k]) => k === id)?.[1] ?? id;

function defaultDoc(state: FlipState): FlipDoc | null {
  const a = state.analysis;
  if (!a) return null;
  return {
    format: 1,
    source: state.source,
    grid: { bpm: a.bpm, downbeat_s: a.downbeat_s, swing: 50 },
    bass808: { mode: "original", params: {} },
    hats: { mode: "original", remove: [], patterns: ["8ths"] },
    gains: { drums: 0, other: 0, bass: 0 },
    arrangement: [{ label: "Исходник", from_bar: 0, to_bar: Math.max(1, Math.min(a.bars, MAX_BARS)) }],
    effects: [],
  };
}

function keepPatterns(doc: FlipDoc): void {
  const want = doc.arrangement.length;
  const p = doc.hats.patterns;
  while (p.length < want) p.push(p[p.length - 1] ?? "8ths");
  p.length = want;
}

export const mount: Mount = async (root, options) => {
  const scope = new Scope();
  const api = createFlipApi({ base: options.base, token: options.token });
  let state: FlipState | null = null;
  let doc: FlipDoc | null = null;
  let peaks: Peaks | null = null;
  let jobs: Job[] = [];
  let selected = 0;
  let paint = { from: 0, to: 1 };
  let anchor = 0;
  let paintEffectId = "reverse";
  let paintParams: Record<string, number> = {};
  let edited = false;
  let failed = false;
  let saving: Promise<void> | null = null;
  let saveTimer = 0;
  let noteTimer = 0;
  let problems: string[] = [];
  let pendingPlay: number | null = null;
  let building = false;
  let editSeq = 0;
  let closed = false;
  let parked = false;
  let refused = false;
  const player = new TakePlayer(
    (key) => api.audio(Number(key)),
    () => {
      if (!closed) markPlaying();
    },
  );

  root.classList.add("flip");
  const status = h("p", { class: "flip__status", role: "status" });
  const notice = h("p", { class: "notice flip__notice", role: "status", hidden: true });
  const errorList = h("ul", { class: "flip__errors", hidden: true });
  const body = h("div", { class: "flip__body" });
  const wave = h("canvas", { class: "flip-wave", "aria-label": "Исходный бит с сеткой тактов" });
  root.append(status, notice, errorList, body);

  const say = (text: string | null): void => {
    scope.clearTimeout(noteTimer);
    notice.hidden = text === null;
    notice.textContent = text ?? "";
    if (text !== null) noteTimer = scope.setTimeout(() => say(null), 8000);
  };

  // only a draft the server refused is lost by leaving: the rest is saved on the way out
  const dirty = (): boolean => failed;
  const unsaved = (): boolean => edited || saving !== null || failed;

  function paintStatus(): void {
    status.textContent = !doc ? "" : failed ? "Черновик не сохранён" : unsaved() ? "Сохраняю черновик…" : "Черновик сохранён";
    status.dataset["state"] = failed ? "failed" : unsaved() ? "saving" : "saved";
    errorList.hidden = problems.length === 0;
    errorList.replaceChildren(...problems.map((p) => h("li", {}, p)));
  }

  // a refusal the page can see itself: shown at once, and the draft counts as failed
  function refuse(): boolean {
    const problem = doc ? draftProblem(doc) : null;
    if (problem) {
      failed = true;
      problems = [problem];
      refused = true;
    } else if (refused) {
      failed = false;
      problems = [];
      refused = false;
    }
    return refused;
  }

  function touch(): void {
    edited = true;
    editSeq += 1;
    scope.clearTimeout(saveTimer);
    if (refuse()) {
      paintStatus();
      return;
    }
    saveTimer = scope.setTimeout(() => void flush(), SAVE_MS);
    paintStatus();
  }

  // true when the draft on the server is the one on the page
  async function flush(): Promise<boolean> {
    scope.clearTimeout(saveTimer);
    if (saving) await saving;
    if (!edited || !doc) return !failed;
    if (refuse()) {
      paintStatus();
      return false;
    }
    const snapshot = doc;
    edited = false;
    const request = api.saveDraft(snapshot).then(
      () => {
        failed = false;
        problems = [];
      },
      (error: unknown) => {
        edited = true;
        failed = true;
        problems = error instanceof DraftError ? error.errors : [error instanceof Error ? error.message : "Сервер не принял черновик."];
      },
    );
    saving = request;
    paintStatus();
    await request;
    saving = null;
    if (!closed) paintStatus();
    return !failed;
  }

  function edit(change: (d: FlipDoc) => void, redraw: () => void = drawAll): void {
    if (!doc) return;
    change(doc);
    touch();
    redraw();
  }

  const sourceBars = (): number => (doc && state?.analysis ? barCount(state.analysis.duration_s, doc.grid.downbeat_s, doc.grid.bpm) : 0);
  const layout = (): Layout => ({ blocks: doc?.arrangement ?? [], patterns: doc?.hats.patterns ?? [] });

  function applyLayout(next: Layout | null, refused: string): void {
    if (!next) {
      say(refused);
      return;
    }
    edit((d) => {
      d.arrangement = next.blocks;
      d.hats.patterns = next.patterns;
      d.effects = fitEffects(d.effects, totalBars(next.blocks));
    });
    selected = Math.min(selected, next.blocks.length - 1);
    paint = clampPaint(paint);
    drawAll();
  }

  function clampPaint(p: { from: number; to: number }): { from: number; to: number } {
    const total = doc ? totalBars(doc.arrangement) : 1;
    const from = Math.min(Math.max(0, p.from), Math.max(0, total - 1));
    return { from, to: Math.min(Math.max(from + 1, p.to), Math.max(1, total)) };
  }

  const resetArrangement = (d: FlipDoc): void => {
    d.arrangement = [{ label: "Исходник", from_bar: 0, to_bar: Math.max(1, Math.min(sourceBars(), MAX_BARS)) }];
    d.hats.patterns = ["8ths"];
    d.effects = [];
    selected = 0;
    paint = { from: 0, to: 1 };
  };

  function touchedArrangement(): boolean {
    if (!doc) return false;
    const [only] = doc.arrangement;
    return doc.arrangement.length !== 1 || doc.effects.length > 0 || !only || only.from_bar !== 0;
  }

  // a new tempo makes the source bars other bars: the arrangement starts over
  function setBpm(bpm: number): void {
    if (!doc) return;
    if (!(bpm >= BPM_MIN && bpm <= BPM_MAX)) {
      say(`Темп — от ${BPM_MIN} до ${BPM_MAX} BPM.`);
      drawAll();
      return;
    }
    if (sameBpm(bpm, doc.grid.bpm)) return;
    if (touchedArrangement() && !confirm("Новый темп меняет нарезку на такты: расстановка и эффекты начнутся заново. Продолжить?")) {
      drawAll();
      return;
    }
    edit((d) => {
      d.grid.bpm = bpm;
      resetArrangement(d);
    });
  }

  function nudge(step: { ms: number } | { beats: number }): void {
    if (!doc) return;
    const bpm = doc.grid.bpm;
    edit((d) => {
      d.grid.downbeat_s = nudgeDownbeat(d.grid.downbeat_s, bpm, step);
      const bars = sourceBars();
      for (const b of d.arrangement) b.to_bar = Math.min(b.to_bar, Math.max(1, bars));
      d.arrangement = d.arrangement.filter((b) => b.to_bar > b.from_bar);
      if (d.arrangement.length === 0) resetArrangement(d);
      keepPatterns(d);
      d.effects = fitEffects(d.effects, totalBars(d.arrangement));
    });
  }

  // ---- drawing

  function drawAll(): void {
    if (closed) return;
    paintStatus();
    if (!state) {
      body.replaceChildren(h("p", { class: "lede" }, "Читаю состояние флипа…"));
      return;
    }
    if (!state.analysis || !doc) {
      body.replaceChildren(stemsCard());
      return;
    }
    body.replaceChildren(sourceCard(), soundCard(), arrangementCard(), renderCard());
    scope.requestFrame(drawWaveNow);
  }

  function jobLine(kind: "flip_stems" | "flip_render"): HTMLElement | null {
    const job = jobs.find((j) => j.kind === kind);
    if (!job) return null;
    const text = job.state === "failed" ? `не вышло: ${job.reason ?? "причина не названа"}` : JOB_STATE[job.state];
    return h("span", { class: jobActive(job) ? "card__meta busy" : "card__meta", role: "status" }, text);
  }

  function stemsCard(): HTMLElement {
    const live = jobActive(jobs.find((j) => j.kind === "flip_stems"));
    const start = h("button", { class: "btn btn--solid", type: "button", disabled: live }, "Разобрать на стемы");
    start.addEventListener("click", () => void startStems());
    return h(
      "section",
      { class: "card", "aria-label": "Разбор бита" },
      h("header", { class: "card__head" }, h("h2", { class: "card__title" }, "Флип бита"), h("p", { class: "card__meta num" }, state?.source ?? "")),
      h(
        "div",
        { class: "card__body" },
        h("p", { class: "lede" }, "Бит ещё не разобран. Разбор делит его на стемы, находит сетку тактов и ноты 808. На видеокарте это около минуты."),
        h("div", { class: "card__end flip__start" }, start, jobLine("flip_stems")),
      ),
    );
  }

  async function startStems(): Promise<void> {
    try {
      await api.startStems();
      say(null);
    } catch (error) {
      say(error instanceof ApiError && error.code === "busy" ? "Сейчас занято другой задачей: дождись её конца." : errorText(error));
    }
    jobsPoller.setWanted(true);
    await tick();
  }

  const errorText = (error: unknown): string => (error instanceof Error && error.message ? error.message : "Не получилось.");

  function section(title: string, meta: string | null, ...content: (Node | string | null)[]): HTMLElement {
    return h(
      "section",
      { class: "card flip-card", "aria-label": title },
      h("header", { class: "card__head" }, h("h2", { class: "card__title" }, title), meta ? h("p", { class: "card__meta num" }, meta) : null),
      h("div", { class: "card__body flip-card__body" }, ...content),
    );
  }

  function sourceCard(): HTMLElement {
    const d = doc as FlipDoc;
    const a = state?.analysis as NonNullable<FlipState["analysis"]>;
    const bpm = h("input", { class: "field flip-num", type: "number", min: BPM_MIN, max: BPM_MAX, step: 0.01, value: Number(d.grid.bpm.toFixed(2)), "aria-label": "Темп, BPM" });
    bpm.addEventListener("change", () => setBpm(Number(bpm.value)));
    const scale = (factor: 0.5 | 2) => () => {
      const next = scaleBpm(d.grid.bpm, factor);
      if (next === null) say(`Темп вышел бы за ${BPM_MIN}..${BPM_MAX} BPM.`);
      else setBpm(next);
    };
    const button = (text: string, title: string, click: () => void): HTMLButtonElement => {
      const b = h("button", { class: "btn btn--small", type: "button", title }, text);
      b.addEventListener("click", click);
      return b;
    };
    const swing = slider({ label: "Свинг", value: d.grid.swing, min: 50, max: 75, step: 0.5, unit: "%", input: (v) => {
      d.grid.swing = v;
      touch();
    } });
    return section(
      "Исходник",
      `${a.bars} тактов · ${a.notes} нот 808, ${a.low_confidence} неуверенных`,
      h("div", { class: "flip-wave-wrap" }, wave),
      h(
        "div",
        { class: "flip-grid" },
        h("label", { class: "flip-field" }, h("span", {}, "BPM"), bpm),
        button("½×", "Вдвое медленнее", scale(0.5)),
        button("2×", "Вдвое быстрее", scale(2)),
        h("span", { class: "flip-grid__sep", "aria-hidden": "true" }),
        h("span", { class: "flip-grid__name" }, "Первая доля"),
        button("−1 доля", "На долю раньше", () => nudge({ beats: -1 })),
        button("−10 мс", "На 10 мс раньше", () => nudge({ ms: -10 })),
        h("output", { class: "flip-grid__at num" }, `${d.grid.downbeat_s.toFixed(3)} с`),
        button("+10 мс", "На 10 мс позже", () => nudge({ ms: 10 })),
        button("+1 доля", "На долю позже", () => nudge({ beats: 1 })),
        swing,
      ),
    );
  }

  function barLines(): number[] {
    if (!doc || !state?.analysis) return [];
    const bar = (60 / doc.grid.bpm) * 4;
    const out: number[] = [];
    for (let t = doc.grid.downbeat_s; t < state.analysis.duration_s; t += bar) out.push(t);
    return out;
  }

  function drawWaveNow(): void {
    if (closed || !doc || !state?.analysis || !wave.isConnected) return;
    const bar = (60 / doc.grid.bpm) * 4;
    const block = doc.arrangement[selected];
    drawWave(wave, {
      peaks,
      duration: state.analysis.duration_s,
      bars: barLines(),
      downbeat: doc.grid.downbeat_s,
      tint: block ? [doc.grid.downbeat_s + block.from_bar * bar, doc.grid.downbeat_s + block.to_bar * bar] : null,
    });
  }

  function soundCard(): HTMLElement {
    const d = doc as FlipDoc;
    const a = state?.analysis as NonNullable<FlipState["analysis"]>;
    const bassModes: [BassMode, string][] = [["original", "оригинал"], ["ours", "наш"], ["off", "выкл"]];
    const hatModes: [HatsMode, string][] = [["original", "оригинал"], ["removed", "убрать"], ["ours", "наши"]];
    const parts: Node[] = [h("h3", { class: "flip__sub" }, "808"), seg("808", bassModes, d.bass808.mode, (mode) => edit((x) => (x.bass808.mode = mode)))];
    if (d.bass808.mode === "ours") {
      const grid = h("div", { class: "flip-sliders" });
      for (const [name, label, min, max, step, def, unit] of BASS_PARAMS) {
        grid.append(slider({ label, value: d.bass808.params[name] ?? def, min, max, step, unit, input: (v) => {
          d.bass808.params[name] = v;
          touch();
        } }));
      }
      const preset = h("button", { class: "btn btn--small", type: "button", title: "Строй, снятый с исходного 808" }, `Строй как в исходнике (${a.detune_cents > 0 ? "+" : ""}${a.detune_cents} ¢)`);
      preset.addEventListener("click", () => edit((x) => (x.bass808.params["tune"] = Math.round(a.detune_cents) / 100)));
      parts.push(grid, preset);
    }
    parts.push(h("h3", { class: "flip__sub" }, "Хэты"), seg("Хэты", hatModes, d.hats.mode, (mode) => edit((x) => {
      x.hats.mode = mode;
      if (mode !== "original" && !x.hats.remove.includes("hh")) x.hats.remove.unshift("hh");
    })));
    if (d.hats.mode !== "original") {
      const row = h("div", { class: "flip-checks" });
      for (const [id, label] of [["hh", "hh"], ["ride", "+ ride"], ["crash", "+ crash"]] as const) {
        const box = h("input", { type: "checkbox", checked: d.hats.remove.includes(id), disabled: id === "hh" });
        box.addEventListener("change", () => edit((x) => {
          x.hats.remove = x.hats.remove.filter((r) => r !== id);
          if (box.checked) x.hats.remove.push(id);
        }));
        row.append(h("label", { class: "flip-check" }, box, label));
      }
      parts.push(row);
    }
    parts.push(h("h3", { class: "flip__sub" }, "Громкость, дБ"));
    const gains = h("div", { class: "flip-sliders" });
    for (const [key, label] of [["drums", "Драмы"], ["other", "Остальное"], ["bass", "Бас"]] as const) {
      gains.append(slider({ label, value: d.gains[key], min: -60, max: 24, step: 0.5, unit: "дБ", input: (v) => {
        d.gains[key] = v;
        touch();
      } }));
    }
    parts.push(gains);
    return section("Звук", null, ...parts);
  }

  function arrangementCard(): HTMLElement {
    const d = doc as FlipDoc;
    const total = totalBars(d.arrangement);
    const propose = h("button", { class: "btn btn--ghost", type: "button" }, "Предложить под текст");
    propose.addEventListener("click", () => void proposeNow());
    const block = d.arrangement[selected];
    return section(
      "Расстановка",
      `${total} из ${MAX_BARS} тактов · ${d.arrangement.length} блоков`,
      stripEl(total),
      block ? blockPanel(block) : null,
      effectPanel(total),
      h("div", { class: "card__end flip__propose" }, propose),
    );
  }

  function stripEl(total: number): HTMLElement {
    const d = doc as FlipDoc;
    const sections = h("div", { class: "flip-strip__sections", role: "toolbar", "aria-label": "Блоки расстановки" });
    const cells = h("div", { class: "flip-strip__bars", role: "toolbar", "aria-label": "Такты расстановки: клик выбирает такт для эффекта, Shift растягивает" });
    const fx = h("div", { class: "flip-strip__fx" });
    let at = 0;
    d.arrangement.forEach((b, i) => {
      const len = b.to_bar - b.from_bar;
      const el = h("button", { class: "flip-block", type: "button", "aria-pressed": String(i === selected), style: `grid-column: ${at + 1} / span ${len}`, title: `${b.label}: исходные такты ${b.from_bar + 1}–${b.to_bar}` }, h("span", { class: "flip-block__name" }, b.label), h("span", { class: "flip-block__range num" }, `${b.from_bar + 1}–${b.to_bar}`));
      el.addEventListener("click", () => {
        selected = i;
        paint = clampPaint({ from: at, to: at + len });
        anchor = at;
        drawAll();
      });
      sections.append(el);
      at += len;
    });
    for (let bar = 0; bar < total; bar += 1) {
      const src = playToSource(d.arrangement, bar);
      const first = d.arrangement.some((_, i) => d.arrangement.slice(0, i).reduce((n, x) => n + x.to_bar - x.from_bar, 0) === bar);
      const cell = h("button", { class: "flip-bar", type: "button", "aria-pressed": String(bar >= paint.from && bar < paint.to), "data-first": String(first), title: `Такт ${bar + 1}${src ? ` · исходный ${src.bar + 1}` : ""}` }, h("span", { class: "flip-bar__n num" }, String(bar + 1)));
      cell.addEventListener("click", (e) => {
        if (e.shiftKey) paint = { from: Math.min(anchor, bar), to: Math.max(anchor, bar) + 1 };
        else {
          anchor = bar;
          paint = { from: bar, to: bar + 1 };
        }
        if (src) selected = src.block;
        drawAll();
      });
      cells.append(cell);
    }
    const lanes: number[] = [];
    d.effects.forEach((e) => {
      let lane = lanes.findIndex((end) => end <= e.from_bar);
      if (lane < 0) lane = lanes.length;
      lanes[lane] = e.to_bar;
      fx.append(h("span", { class: "flip-fx", style: `grid-column: ${e.from_bar + 1} / ${e.to_bar + 1}; grid-row: ${lane + 1}`, title: `${effectName(e.effect)}, такты ${e.from_bar + 1}–${e.to_bar}` }, effectName(e.effect)));
    });
    const track = h("div", { class: "flip-strip__track", style: `--bars: ${total}` }, sections, cells, fx);
    return h("div", { class: "flip-strip" }, h("div", { class: "flip-strip__scroll" }, track));
  }

  function small(text: string, title: string, click: () => void, disabled = false): HTMLButtonElement {
    const b = h("button", { class: "btn btn--small", type: "button", title, disabled }, text);
    b.addEventListener("click", click);
    return b;
  }

  function blockPanel(block: FlipDoc["arrangement"][number]): HTMLElement {
    const d = doc as FlipDoc;
    const last = d.arrangement.length - 1;
    const label = h("input", { class: "field flip-label", type: "text", maxlength: 64, value: block.label, "aria-label": "Название блока" });
    label.addEventListener("change", () => {
      const v = label.value.trim();
      if (v === "") {
        label.value = block.label;
        return;
      }
      edit((x) => {
        const b = x.arrangement[selected];
        if (b) b.label = v.slice(0, 64);
      });
    });
    const from = numberField("с такта", block.from_bar + 1, 1, sourceBars(), 1, (v) =>
      applyLayout(setRange(layout(), selected, v - 1, block.to_bar, sourceBars()), `Диапазон блока должен лежать в исходнике (1–${sourceBars()}) и не выводить расстановку за ${MAX_BARS} тактов.`),
    );
    const to = numberField("по такт", block.to_bar, 1, sourceBars(), 1, (v) =>
      applyLayout(setRange(layout(), selected, block.from_bar, v, sourceBars()), `Диапазон блока должен лежать в исходнике (1–${sourceBars()}) и не выводить расстановку за ${MAX_BARS} тактов.`),
    );
    const cut = h("input", { class: "field flip-num", type: "number", min: block.from_bar + 2, max: block.to_bar, step: 1, value: Math.ceil((block.from_bar + block.to_bar + 1) / 2), "aria-label": "Разрезать с такта" });
    const rows: Node[] = [
      h(
        "div",
        { class: "flip-edit" },
        h("label", { class: "flip-field" }, h("span", {}, "Название"), label),
        from,
        to,
        small("←", "Блок раньше", () => applyLayout(moveBlock(layout(), selected, -1), "Дальше влево нельзя."), selected === 0),
        small("→", "Блок позже", () => applyLayout(moveBlock(layout(), selected, 1), "Дальше вправо нельзя."), selected === last),
        small("Дублировать", "Копия сразу после блока", () => applyLayout(duplicateBlock(layout(), selected), `Расстановка не вырастет больше ${MAX_BARS} тактов.`)),
        small("Удалить", "Убрать блок", () => applyLayout(deleteBlock(layout(), selected), "Последний блок не удалить.")),
        h("span", { class: "flip-grid__sep", "aria-hidden": "true" }),
        h("label", { class: "flip-field" }, h("span", {}, "Разрезать с такта"), cut),
        small("Разрезать", "Разрезать блок по исходному такту", () => applyLayout(splitBlock(layout(), selected, Number(cut.value) - 1), "Резать можно только внутри блока.")),
      ),
    ];
    if (d.hats.mode === "ours") {
      const pick = h("select", { class: "field flip-select", "aria-label": "Хэты блока" });
      for (const name of state?.presets ?? Object.keys(PRESET_NAMES)) pick.append(h("option", { value: name, selected: d.hats.patterns[selected] === name }, PRESET_NAMES[name] ?? name));
      pick.addEventListener("change", () => edit((x) => (x.hats.patterns[selected] = pick.value), () => {}));
      rows.push(h("div", { class: "flip-edit" }, h("label", { class: "flip-field" }, h("span", {}, "Хэты блока"), pick)));
    }
    return h("div", { class: "flip-panel" }, ...rows);
  }

  function effectPanel(total: number): HTMLElement {
    const d = doc as FlipDoc;
    const ranges = effectRanges(state?.effects ?? {}, paintEffectId);
    const pick = seg("Эффект", EFFECT_NAMES as [string, string][], paintEffectId, (id) => {
      paintEffectId = id;
      paintParams = {};
      drawAll();
    });
    const sliders = h("div", { class: "flip-sliders" });
    for (const [name, r] of ranges) {
      const step = WHOLE.has(name) ? 1 : (r.high - r.low) / 200;
      const base = r.def ?? r.low;
      sliders.append(slider({ label: PARAM_NAMES[name] ?? name, value: paintParams[name] ?? base, min: r.low, max: r.high, step, ...(r.unit ? { unit: r.unit } : {}), digits: WHOLE.has(name) ? 0 : step >= 0.1 ? 1 : 2, input: (v) => {
        paintParams[name] = v;
      } }));
    }
    const from = numberField("с такта", paint.from + 1, 1, total, 1, (v) => {
      paint = clampPaint({ from: v - 1, to: Math.max(paint.to, v) });
      drawAll();
    });
    const to = numberField("по такт", paint.to, 1, total, 1, (v) => {
      paint = clampPaint({ from: paint.from, to: v });
      drawAll();
    });
    const add = small("Нанести", "Эффект на выбранные такты", () => {
      const bars = Math.min(total, paint.to) - paint.from;
      const params = { ...paintParams };
      if (paintEffectId === "tape_stop" || paintEffectId === "tape_start") {
        const cap = tapeCap(d.grid.bpm, bars);
        const duration = params["duration"] ?? Math.min(0.5, cap);
        params["duration"] = Math.min(duration, cap);
        const rangeS = (bars * 4 * 60) / d.grid.bpm;
        if ((params["time"] ?? 0) >= rangeS) params["time"] = 0;
      }
      const next = paintEffect(d.effects, { effect: paintEffectId, from_bar: paint.from, to_bar: paint.to, params }, total);
      if (!next) {
        say("Выбери такты внутри расстановки.");
        return;
      }
      edit((x) => (x.effects = next));
    });
    const list = h("ul", { class: "flip-fxlist" });
    d.effects.forEach((e, i) => {
      const summary = Object.entries(e.params).map(([k, v]) => `${PARAM_NAMES[k] ?? k} ${Number(v.toFixed(3))}`).join(", ");
      list.append(h("li", {}, h("span", {}, `${effectName(e.effect)} · такты ${e.from_bar + 1}–${e.to_bar}${summary ? ` · ${summary}` : ""}`), small("Убрать", "Снять эффект", () => edit((x) => x.effects.splice(i, 1)))));
    });
    return h("div", { class: "flip-panel" }, h("h3", { class: "flip__sub" }, "Эффекты на тактах расстановки"), pick, sliders, h("div", { class: "flip-edit" }, from, to, add), list);
  }

  async function proposeNow(): Promise<void> {
    if (!doc) return;
    try {
      const blocks = await api.propose();
      if (closed || !doc) return;
      const bars = totalBars(blocks);
      if (!confirm(`Предложение заменит расстановку: ${blocks.length} блоков, ${bars} тактов. Эффекты на тактах останутся только там, где тактов хватит. Заменить?`)) return;
      applyLayout({ blocks, patterns: blocks.map(() => doc?.hats.patterns[0] ?? "8ths") }, "Предложение не подошло.");
    } catch (error) {
      say(error instanceof ApiError && error.code === "no_text" ? "Нужен текст песни: выбери его на шаге «Текст»." : errorText(error));
    }
  }

  // ---- render, play, use

  function renderCard(): HTMLElement {
    const s = state as FlipState;
    const live = jobActive(jobs.find((j) => j.kind === "flip_render"));
    const make = h("button", { class: "btn btn--solid", type: "button", disabled: live || building }, live || building ? "Собираю…" : "Собрать");
    make.addEventListener("click", () => void build());
    const versions = h("ul", { class: "flip-versions" });
    for (const n of [...s.versions].sort((a, b) => b - a)) {
      const has = s.renders.includes(n);
      const row = h("li", { class: "flip-version", "data-version": String(n) }, h("span", { class: "flip-version__name num" }, `Версия ${n}`), s.used === n ? h("span", { class: "card__meta" }, "бит песни") : null);
      if (has) {
        const on = player.name === String(n);
        row.append(playButton(`версию ${n}`, on, () => void player.toggle(String(n)), ""), h("span", { class: "flip-version__now card__meta", hidden: !on }, "Играет"));
        if (s.used !== n) row.append(small("Сделать битом песни", "Подставить версию битом песни", () => void useVersion(n)));
      } else row.append(h("span", { class: "card__meta" }, "нет записи"));
      versions.append(row);
    }
    return section(
      "Сборка",
      null,
      h("div", { class: "card__end flip__render" }, make, jobLine("flip_render")),
      h("p", { class: "flip__licence" }, LICENCE),
      s.versions.length ? versions : h("p", { class: "lede" }, "Версий пока нет."),
    );
  }

  async function build(): Promise<void> {
    if (!doc) return;
    if (!(await flush())) {
      say("Черновик не сохранён: поправь ошибки выше, потом собирай.");
      return;
    }
    building = true;
    drawAll();
    const stamp = jobs.find((j) => j.kind === "flip_render")?.finished ?? null;
    try {
      pendingPlay = await api.render();
      say(null);
    } catch (error) {
      building = false;
      say(error instanceof ApiError && error.code === "busy" ? "Сборка уже идёт: дождись её конца." : errorText(error));
      drawAll();
      return;
    }
    jobsPoller.setWanted(true);
    const render = jobs.find((j) => j.kind === "flip_render");
    if (finishedSince(stamp, render)) {
      building = false;
      await refresh();
      if (closed) return;
      settle(render);
    }
    await tick();
  }

  function settle(render: Job | undefined): void {
    if (pendingPlay === null) return;
    const n = pendingPlay;
    pendingPlay = null;
    const outcome = renderOutcome({ failed: render?.state === "failed", present: state?.renders.includes(n) ?? false, parked });
    if (outcome === "failed") say(`Сборка не вышла: ${render?.reason ?? "причина не названа"}`);
    else if (outcome === "play") void player.toggle(String(n));
  }

  function markPlaying(): void {
    for (const row of body.querySelectorAll<HTMLElement>("li[data-version]")) {
      const on = row.dataset["version"] === player.name;
      row.querySelector(".tplay")?.setAttribute("aria-pressed", String(on));
      const now = row.querySelector<HTMLElement>(".flip-version__now");
      if (now) now.hidden = !on;
    }
  }

  async function useVersion(n: number): Promise<void> {
    if (!state) return;
    if (!confirm(useConfirm(n, state.takes_over_other_beat))) return;
    try {
      await flush();
      await api.use(n, await api.rev());
      say(null);
      root.dispatchEvent(new CustomEvent("flip-used", { bubbles: true, detail: { n } }));
      await refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) say("Песня изменилась в другом месте. Повтори действие.");
      else if (error instanceof ApiError && error.code === "take_running") say("Идёт дубль: дождись конца.");
      else say(errorText(error));
    }
  }

  // ---- state and jobs

  async function refresh(): Promise<void> {
    const seqBefore = editSeq;
    const next = await api.state();
    if (closed) return;
    const fetched = next.analysis && !peaks ? await api.peaks().catch(() => null) : null;
    if (closed) return;
    if (fetched) peaks = fetched;
    state = next;
    if (!doc || mayAdoptDraft({ seqBefore, seqNow: editSeq, edited, saving: saving !== null, failed })) {
      doc = next.draft ?? defaultDoc(next);
      if (doc) keepPatterns(doc);
    }
    selected = Math.min(selected, Math.max(0, (doc?.arrangement.length ?? 1) - 1));
    paint = clampPaint(paint);
    drawAll();
  }

  const isFlipJob = (j: Job): boolean => j.kind === "flip_stems" || j.kind === "flip_render";

  async function step(): Promise<void> {
    const next = await api.jobs();
    if (closed) return;
    const before = jobs.filter(isFlipJob);
    const flipJobs = next.filter(isFlipJob);
    const same = JSON.stringify(flipJobs) === JSON.stringify(before);
    const done = finishedKinds(before, flipJobs);
    jobs = next;
    const render = flipJobs.find((j) => j.kind === "flip_render");
    if (jobActive(render) || done.includes("flip_render")) building = false;
    if (done.length > 0) {
      await refresh();
      if (closed) return;
      if (done.includes("flip_render")) settle(render);
    } else if (!same) drawAll();
    jobsPoller.setWanted(flipJobs.some(jobActive) || pendingPlay !== null || building);
  }

  // one read of the jobs at a time; a call made meanwhile earns one more read after it
  let ticking: Promise<void> | null = null;
  let again = false;
  function tick(): Promise<void> {
    if (ticking) {
      again = true;
      return ticking;
    }
    ticking = (async () => {
      try {
        do {
          again = false;
          await step();
        } while (again && !closed);
      } catch {
        // the next beat tries again
      } finally {
        ticking = null;
      }
    })();
    return ticking;
  }

  const jobsPoller = new Poller({ ms: 1000, tick });
  scope.add(() => jobsPoller.dispose());
  scope.listen(root.ownerDocument, "visibilitychange", () => jobsPoller.setVisible(root.ownerDocument.visibilityState !== "hidden"));
  const resize = typeof ResizeObserver === "function" ? new ResizeObserver(() => drawWaveNow()) : null;
  resize?.observe(wave);
  scope.add(() => resize?.disconnect());

  try {
    await refresh();
    jobs = await api.jobs();
    jobsPoller.setWanted(jobs.some((j) => isFlipJob(j) && jobActive(j)));
    drawAll();
  } catch (error) {
    body.replaceChildren(h("p", { class: "lede" }, `Флип не загрузился: ${errorText(error)}`));
  }

  return {
    dirty,
    setActive: () => {},
    hold: (on) => {
      parked = on;
      if (on) player.stop();
    },
    dispose: async () => {
      if (closed) return;
      scope.clearTimeout(saveTimer);
      await flush();
      closed = true;
      player.stop();
      scope.dispose();
      api.close();
      root.classList.remove("flip");
      if (!root.classList.length) root.removeAttribute("class");
      status.remove();
      notice.remove();
      errorList.remove();
      body.remove();
    },
  };
};
