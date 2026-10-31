// The screen's transport as a pure machine: a state, an event, the next state and the effects the
// screen carries out. An event that means nothing in a state leaves it as it is. Below it, the
// "next step" chip and what keeps record and export disabled.

export type RecordStep = "opening" | "calibrate" | "counting" | "recording" | "stopping";

// what waits for the beat's render to finish
export type Pending = "play" | "record" | "export";

export type Transport =
  | { mode: "idle"; error: string | null }
  | { mode: "playing" }
  | { mode: "recording"; step: RecordStep }
  | { mode: "rendering"; then: Pending | null };

// `render`: the newest score is not rendered yet, so the press commits it and waits; `held`: the
// recorder holds a take the screen does not show, so the press stops that take
export type TransportEvent =
  | { type: "play"; render: boolean }
  | { type: "pause" }
  | { type: "stop" }
  | { type: "record"; render: boolean; held?: boolean }
  | { type: "export"; render: boolean }
  | { type: "opened"; calibrate: boolean }
  | { type: "calibrated" }
  | { type: "recordStarted" }
  | { type: "recordStopped" }
  | { type: "renderStarted" }
  | { type: "renderDone" }
  | { type: "error"; message: string };

// "commit": save the newest score and render it; "stop": stop playback and any block audition and
// go back to the start; "watch-take": poll the recorder until it holds no take, then show the takes
export type Effect = "commit" | "play" | "pause" | "stop" | "open-engine" | "start-record" | "stop-record" | "watch-take" | "export";

export interface Step {
  state: Transport;
  effects: Effect[];
}

export const IDLE: Transport = { mode: "idle", error: null };

const to = (state: Transport, ...effects: Effect[]): Step => ({ state, effects });
const recording = (step: RecordStep): Transport => ({ mode: "recording", step });
const rendering = (then: Pending | null): Transport => ({ mode: "rendering", then });

// what a press does once the beat is rendered
function go(what: Pending): Step {
  if (what === "play") return to({ mode: "playing" }, "play");
  if (what === "record") return to(recording("opening"), "stop", "open-engine");
  return to(IDLE, "export");
}

function fromIdle(e: TransportEvent): Step | null {
  switch (e.type) {
    case "record":
      if (e.held) return to(recording("stopping"), "stop-record");
      return e.render ? to(rendering(e.type), "commit") : go(e.type);
    case "play":
    case "export":
      return e.render ? to(rendering(e.type), "commit") : go(e.type);
    case "stop":
      return to(IDLE, "stop");
    case "renderStarted":
      return to(rendering(null));
    default:
      return null;
  }
}

function fromPlaying(e: TransportEvent): Step | null {
  switch (e.type) {
    case "pause":
      return to(IDLE, "pause");
    case "stop":
      return to(IDLE, "stop");
    // the engine records over the beat on its own, so the page's playback stops first
    case "record":
      if (e.held) return to(recording("stopping"), "stop", "stop-record");
      return e.render ? to(rendering("record"), "stop", "commit") : go("record");
    case "export":
      return e.render ? to(rendering("export"), "stop", "commit") : to({ mode: "playing" }, "export");
    default:
      return null;
  }
}

function fromRendering(then: Pending | null, e: TransportEvent): Step | null {
  switch (e.type) {
    case "play":
    case "record":
    case "export":
      return to(rendering(e.type));
    case "pause":
      return then === "play" ? to(rendering(null)) : null;
    // the render goes on in the background; nothing waits for it any more
    case "stop":
      return to(IDLE, "stop");
    case "renderDone":
      return then === null ? to(IDLE) : go(then);
    default:
      return null;
  }
}

function fromRecording(step: RecordStep, e: TransportEvent): Step | null {
  if (step === "opening" && e.type === "opened") {
    return e.calibrate ? to(recording("calibrate")) : to(recording("counting"), "start-record");
  }
  if (step === "calibrate" && e.type === "calibrated") return to(recording("counting"), "start-record");
  if (step === "counting" && e.type === "recordStarted") return to(recording("recording"));
  if (e.type === "stop") {
    // nothing has been recorded before the start was asked for
    if (step === "opening" || step === "calibrate") return to(IDLE);
    if (step === "counting" || step === "recording") return to(recording("stopping"), "stop-record");
  }
  if (e.type === "recordStopped" && step !== "opening" && step !== "calibrate") return to(IDLE);
  return null;
}

export function step(state: Transport, e: TransportEvent): Step {
  if (e.type === "error") {
    const failed: Transport = { mode: "idle", error: e.message };
    if (state.mode === "playing") return to(failed, "stop");
    // from counting on, a take may be running or finishing in the engine
    if (state.mode === "recording" && state.step !== "opening" && state.step !== "calibrate") return to(failed, "watch-take");
    return to(failed);
  }
  let next: Step | null;
  if (state.mode === "idle") next = fromIdle(e);
  else if (state.mode === "playing") next = fromPlaying(e);
  else if (state.mode === "rendering") next = fromRendering(state.then, e);
  else next = fromRecording(state.step, e);
  return next ?? to(state);
}

// ---------- the next step and the disabled buttons ----------

export interface SongFacts {
  hasBeatAudio: boolean;
  // a type beat has been analysed; true for an own beat or a flip
  beatAnalysed: boolean;
  takes: number;
  chosen: string | null;
  voiceKind: "own" | "suno";
  // the chosen voice is prepared for the studio
  studioReady: boolean;
  // the result is missing or older than the beat, the voice or the settings
  mixStale: boolean;
  refsCount: number;
  exporting: boolean;
}

export type NextStep = "analyse" | "listen" | "record" | "pick" | "prepare-mix" | "export" | "done";

export function nextStep(f: SongFacts): NextStep {
  if (!f.beatAnalysed) return "analyse";
  // an own beat with no render yet: play renders it
  if (!f.hasBeatAudio) return "listen";
  if (f.voiceKind === "own") {
    if (f.takes === 0) return "record";
    if (f.chosen === null) return "pick";
  }
  if (!f.studioReady) return "prepare-mix";
  if (f.exporting || f.mixStale) return "export";
  return "done";
}

export type RecordBlocker = "suno" | "busy" | "rendering";
export type ExportBlocker = "no-refs" | "no-take";

export const BLOCKER_TEXT: Record<RecordBlocker | ExportBlocker, string> = {
  suno: "Голос — трек из Suno, записывать не нужно",
  busy: "Движок занят другой задачей",
  rendering: "Бит пересобирается",
  "no-refs": "Нет референсов в input/refs",
  "no-take": "Сначала выбери дубль ★",
};

export function recordBlockers(f: { voiceKind: SongFacts["voiceKind"]; engineBusy: boolean }, t: Transport): RecordBlocker[] {
  const out: RecordBlocker[] = [];
  if (f.voiceKind === "suno") out.push("suno");
  if (f.engineBusy) out.push("busy");
  if (t.mode === "rendering") out.push("rendering");
  return out;
}

export function exportBlockers(f: Pick<SongFacts, "refsCount" | "chosen" | "voiceKind">): ExportBlocker[] {
  const out: ExportBlocker[] = [];
  if (f.refsCount <= 0) out.push("no-refs");
  if (f.voiceKind === "own" && f.chosen === null) out.push("no-take");
  return out;
}
