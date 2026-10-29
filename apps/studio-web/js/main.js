// Wires the studio: schema to cards, settings to renders, renders to the player and the wave.
import { api, Superseded } from "./api.js";
import { Controls, STAGE_OF } from "./controls.js";
import { History } from "./history.js";
import { Player } from "./player.js";
import { Waveform, loudestSpan } from "./waveform.js";

const LOOP_SECONDS = 20;
const $ = (id) => document.getElementById(id);

const ui = {
  play: $("play"),
  loop: $("loop"),
  a: $("ab-a"),
  b: $("ab-b"),
  blind: $("blind"),
  time: $("time"),
  state: $("state"),
  ms: $("render-ms"),
  tuning: $("tuning"),
  target: $("target"),
  save: $("save"),
  build: $("build"),
  story: $("story"),
  progress: $("progress"),
  progressBar: $("progress-bar"),
  hint: $("hint"),
  hintClose: $("hint-close"),
  showBasic: $("show-basic"),
  showAll: $("show-all"),
  stages: [...document.querySelectorAll(".stage")],
  loopSpan: $("loop-span"),
  undo: $("undo"),
  redo: $("redo"),
  resetAll: $("reset-all"),
  presets: $("presets"),
  presetName: $("preset-name"),
  presetSave: $("preset-save"),
  note: $("note"),
};

const player = new Player();
let schema;
let controls;
let history;
let defaults = {};
let values = {};
let rendered = null; // the settings of the last exact render, as JSON
let exact = null; // that render's audio and loudness
let seq = 0;
let inflight = null;
let wholeSeconds = null; // how long the last whole render took, the next one's estimate
let ticking = null;
// blind mode: which lane each letter plays; null when names are shown
let blind = null;

function clock(seconds) {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${String(m).padStart(2, "0")}:${s.toFixed(1).padStart(4, "0")}`;
}

function setState(kind, text) {
  ui.state.dataset.state = kind;
  ui.state.textContent = text;
  ui.save.disabled = kind !== "exact";
}

// the plain-words line under the deck; bold parts are passed as [text]
function story(...parts) {
  ui.story.replaceChildren(
    ...parts.map((part) => {
      if (!Array.isArray(part)) return document.createTextNode(part);
      const b = document.createElement("b");
      b.textContent = part[0];
      return b;
    }),
  );
}

function loopName() {
  return `${clock(player.loop.start)}–${clock(player.loop.end)}`;
}

function remember(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null;
  }
  return null;
}

function note(text, command) {
  ui.note.textContent = text;
  if (command) {
    const code = document.createElement("code");
    code.textContent = command;
    ui.note.append(" ", code);
  }
}

function markStages(from, state) {
  for (const stage of ui.stages) {
    const n = Number(stage.dataset.stage);
    if (state === "clear") delete stage.dataset.run;
    else if (n >= from) stage.dataset.run = state;
  }
}

function firstStage(before, after) {
  if (!before) return 0;
  let first = 5;
  for (const spec of schema.settings) {
    if (before[spec.key] !== after[spec.key]) first = Math.min(first, STAGE_OF[spec.group]);
  }
  return first;
}

const wave = new Waveform(
  $("wave-canvas"),
  (seconds) => {
    player.seek(seconds);
    wave.setPosition(seconds);
  },
  (loop) => setLoop(loop),
);

function setLoop(loop) {
  player.setLoop(loop);
  wave.setLoop(loop);
  ui.loopSpan.textContent = `петля ${clock(loop.start)}–${clock(loop.end)}`;
  // a preview only covers the loop it was made for
  if (ui.state.dataset.state !== "exact") preview(firstStage(null, values));
}

async function decode(bytes) {
  return player.decode(bytes);
}

function nextRequest() {
  // the server keeps the newest number it saw, across page reloads too, so numbers grow with time
  seq = Math.max(Date.now(), seq + 1);
  inflight?.abort();
  inflight = new AbortController();
  stopProgress();
  return { mine: seq, signal: inflight.signal };
}

function stopProgress() {
  clearInterval(ticking);
  ticking = null;
  ui.progress.hidden = true;
  ui.build.disabled = false;
}

function failed(error) {
  if (error instanceof Superseded || error.name === "AbortError") return;
  stopProgress();
  markStages(0, "clear");
  setState("error", "ошибка");
  story("Сервер ответил ошибкой: ", [error.message]);
}

// a loop with the new settings, in a couple of seconds
async function preview(fromStage = 0) {
  const { mine, signal } = nextRequest();
  const settings = { ...values };
  markStages(0, "clear");
  markStages(fromStage, "busy");
  setState("busy", "считаю кусок");
  story("Считаю кусок ", [loopName()], " с новыми настройками…");
  try {
    const { start, end } = player.loop;
    const piece = await api.preview(settings, start, end, mine, signal);
    const buffer = await decode(piece.bytes);
    if (mine !== seq) return;
    player.setResult(buffer, start, piece.lufs, piece.originalLufs);
    wave.setResult(buffer, start);
    const took = (piece.ms / 1000).toFixed(1);
    ui.ms.textContent = `${took} с`;
    markStages(fromStage, "done");
    setState("approx", "кусок");
    story(
      "Слушаешь ",
      ["кусок " + loopName()],
      ` с новыми настройками (посчитан за ${took} с). Весь трек ещё не собран: для сохранения — «Собрать весь трек».`,
    );
  } catch (error) {
    failed(error);
  }
}

// the whole track, exactly as the final will be; takes about a minute
async function build() {
  const { mine, signal } = nextRequest();
  const settings = { ...values };
  const key = JSON.stringify(settings);
  const began = performance.now();
  const estimate = wholeSeconds || 60;
  setState("busy", "собираю трек");
  markStages(0, "busy");
  ui.build.disabled = true;
  ui.progress.hidden = false;
  const update = () => {
    const passed = (performance.now() - began) / 1000;
    const share = Math.min(0.95, passed / estimate);
    ui.progressBar.style.transform = `scaleX(${share})`;
    story(
      "Собираю ",
      ["весь трек"],
      `: ${Math.round(passed)} с из ~${Math.round(estimate)} с. Можно крутить дальше — тогда сборка отменится.`,
    );
  };
  update();
  ticking = setInterval(update, 250);
  try {
    const whole = await api.render(settings, mine, signal);
    const buffer = await decode(whole.bytes);
    if (mine !== seq) return;
    wholeSeconds = (performance.now() - began) / 1000;
    stopProgress();
    player.setResult(buffer, 0, whole.lufs, schema.track.original_lufs);
    wave.setResult(buffer, 0);
    rendered = key;
    exact = { buffer, lufs: whole.lufs };
    ui.ms.textContent = `${wholeSeconds.toFixed(0)} с`;
    markStages(0, "done");
    setState("exact", "весь трек");
    story(["Весь трек собран"], ` за ${Math.round(wholeSeconds)} с — это ровно то, что выйдет в финале. Можно сохранять.`);
  } catch (error) {
    failed(error);
  }
}

function apply(next, record = true) {
  const before = values;
  values = { ...next };
  if (record) history.push(values);
  controls.show(values);
  ui.undo.disabled = !history.past.length;
  ui.redo.disabled = !history.future.length;
  if (JSON.stringify(values) === rendered) {
    // back to what was rendered last: play that render, drop whatever is still running
    nextRequest();
    player.setResult(exact.buffer, 0, exact.lufs, schema.track.original_lufs);
    wave.setResult(exact.buffer, 0);
    markStages(0, "done");
    setState("exact", "весь трек");
    story("Вернулся к уже собранному треку. Можно сохранять.");
    return;
  }
  preview(firstStage(before, values));
}

function setView(view) {
  document.querySelector(".desk").dataset.view = view;
  ui.showBasic.setAttribute("aria-checked", String(view === "basic"));
  ui.showAll.setAttribute("aria-checked", String(view === "all"));
  remember("studio-view", view);
}

function sideFor(button) {
  const letter = button === ui.a ? "a" : "b";
  if (blind) return blind[letter];
  return letter === "a" ? "original" : "result";
}

function showSide() {
  ui.a.setAttribute("aria-pressed", String(sideFor(ui.a) === player.side));
  ui.b.setAttribute("aria-pressed", String(sideFor(ui.b) === player.side));
}

function toggleSide() {
  player.setSide(player.side === "result" ? "original" : "result");
  showSide();
}

function setBlind(on) {
  ui.blind.setAttribute("aria-pressed", String(on));
  ui.blind.textContent = on ? "Показать" : "Вслепую";
  if (on) {
    const flip = crypto.getRandomValues(new Uint8Array(1))[0] % 2 === 0;
    blind = flip ? { a: "result", b: "original" } : { a: "original", b: "result" };
    ui.a.textContent = "A";
    ui.b.textContent = "B";
  } else {
    blind = null;
    ui.a.textContent = "Оригинал";
    ui.b.textContent = "Результат";
  }
  showSide();
}

async function togglePlay() {
  if (player.playing) {
    player.pause();
  } else {
    await player.play();
  }
  ui.play.dataset.playing = String(player.playing);
}

function tick() {
  const position = player.position();
  ui.time.textContent = clock(position);
  wave.setPosition(position);
  requestAnimationFrame(tick);
}

async function loadPresets() {
  const presets = await api.presets();
  ui.presets.replaceChildren(new Option("— выбрать —", ""));
  for (const name of Object.keys(presets)) ui.presets.append(new Option(name, name));
  ui.presets.onchange = () => {
    const chosen = presets[ui.presets.value];
    if (chosen) apply({ ...defaults, ...chosen });
  };
}

function wire() {
  ui.play.addEventListener("click", togglePlay);
  player.onEnd = () => (ui.play.dataset.playing = "false");
  ui.loop.addEventListener("click", () => {
    const on = ui.loop.getAttribute("aria-pressed") !== "true";
    ui.loop.setAttribute("aria-pressed", String(on));
    player.setLoop({ on });
    // without the loop the result must cover the whole track
    if (!on && ui.state.dataset.state !== "exact") build();
  });
  for (const button of [ui.a, ui.b]) {
    button.addEventListener("click", () => {
      player.setSide(sideFor(button));
      showSide();
    });
  }
  ui.blind.addEventListener("click", () => {
    if (blind) {
      const name = (lane) => (lane === "original" ? "оригинал" : "результат");
      note(`A — ${name(blind.a)}, B — ${name(blind.b)}.`);
    }
    setBlind(!blind);
  });
  ui.undo.addEventListener("click", () => {
    const back = history.undo();
    if (back) apply(back, false);
  });
  ui.redo.addEventListener("click", () => {
    const forward = history.redo();
    if (forward) apply(forward, false);
  });
  ui.resetAll.addEventListener("click", () => apply({ ...defaults }));
  ui.build.addEventListener("click", build);
  ui.showBasic.addEventListener("click", () => setView("basic"));
  ui.showAll.addEventListener("click", () => setView("all"));
  ui.hintClose.addEventListener("click", () => {
    ui.hint.hidden = true;
    remember("studio-hint", "closed");
  });
  ui.save.addEventListener("click", async () => {
    try {
      const { path } = await api.save(values);
      note("Сохранено. Финал с этими настройками:", `redline run --track <трек> --ref <референсы> --settings ${path}`);
    } catch (error) {
      note(error instanceof Superseded ? "Сначала «Собрать весь трек», потом сохраняй." : `Не сохранилось: ${error.message}`);
    }
  });
  ui.presetSave.addEventListener("click", async () => {
    const name = ui.presetName.value.trim();
    if (!name) return note("Дай пресету имя.");
    try {
      await api.savePreset(name, values);
      await loadPresets();
      note(`Пресет «${name}» сохранён.`);
    } catch (error) {
      note(`Пресет не сохранён: ${error.message}`);
    }
  });
  document.addEventListener("keydown", (event) => {
    const input = event.target instanceof HTMLInputElement ? event.target.type : null;
    const undoKey = event.code === "KeyZ" && (event.ctrlKey || event.metaKey);
    // a number field's own undo would show a value the settings do not hold
    if (input === "number" && undoKey) {
      event.preventDefault();
      event.target.blur();
      (event.shiftKey ? ui.redo : ui.undo).click();
      return;
    }
    if ((input && input !== "range") || event.target instanceof HTMLSelectElement) return;
    if (event.code === "Space") {
      event.preventDefault();
      togglePlay();
    } else if (event.code === "KeyL" && !event.ctrlKey) {
      ui.loop.click();
    } else if (event.code === "KeyB" && !event.ctrlKey) {
      toggleSide();
    } else if (event.code === "KeyZ" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      (event.shiftKey ? ui.redo : ui.undo).click();
    }
  });
}

async function start() {
  setState("busy", "загрузка");
  schema = await api.schema();
  defaults = Object.fromEntries(schema.settings.map((s) => [s.key, s.default]));
  values = { ...schema.current };
  history = new History(values);
  controls = new Controls($("cards"), schema, $("tip"), (next) => apply(next));
  controls.show(values);
  const tuning = schema.track.tuning_cents;
  ui.tuning.textContent = `${tuning > 0 ? "+" : ""}${Math.round(tuning)} ц`;
  ui.target.textContent = `${schema.track.master_lufs.toFixed(1)} LUFS`;
  ui.undo.disabled = true;
  ui.redo.disabled = true;
  setView(remember("studio-view") === "all" ? "all" : "basic");
  ui.hint.hidden = remember("studio-hint") === "closed";
  wire();
  const original = await api.original();
  const buffer = await decode(original.bytes);
  player.setOriginal(buffer);
  wave.setOriginal(buffer);
  setLoop(loudestSpan(buffer, Math.min(LOOP_SECONDS, buffer.duration)));
  await loadPresets();
  tick();
}

start().catch((error) => {
  setState("error", "ошибка");
  note(`Студия не загрузилась: ${error.message}`);
});
