// The vocal, beat and master racks of a set of settings, as redline.engine.live.racks makes
// them; the page rebuilds them on its own between two analyses from the server.

import type { Analysis, Lane, RackSlot, Racks, Settings, UserEffect } from "../types.ts";

const GAIN_LOW_DB = -60;
const GAIN_HIGH_DB = 24;
const MASTER_GAIN_LIMIT_DB = 30;
const THROW_THRESHOLD_RANGE: [number, number] = [-60, -10];
const GATE_RANGE: [number, number] = [-120, 0];
const MAKEUP_RANGE: [number, number] = [-24, 48];
const PEAK_RANGE: [number, number] = [-60, 12];
const DOUBLER_MAX_DEPTH_MS = 5;
const TUNE_MAX_GLIDE_MS = 200;
const EQ_GAIN_LIMIT_DB = 24;
const EQ_Q = 1;
const SHELF_Q = 1 / Math.sqrt(2);
const CEILING_DB = -0.1;
const MIN_GAP_MS = 200;
const THROW_TAIL_MS = 300;
export const MAX_RACK_EFFECTS = 32;

export const EQ_BANDS: readonly { key: string; kind: string; freq: number }[] = [
  { key: "eq_200", kind: "lowshelf", freq: 200 },
  { key: "eq_400", kind: "peak", freq: 400 },
  { key: "eq_1000", kind: "peak", freq: 1000 },
  { key: "eq_2500", kind: "peak", freq: 2500 },
  { key: "eq_5000", kind: "peak", freq: 5000 },
  { key: "eq_8000", kind: "highshelf", freq: 8000 },
];

function num(s: Settings, key: string): number {
  const v = s[key];
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  throw new Error(`the settings have no ${key}`);
}

function on(s: Settings, key: string): boolean {
  const v = s[key];
  return v === true || (typeof v === "number" && v !== 0);
}

function need(numbers: Analysis, name: string): number {
  const v = numbers[name];
  if (typeof v !== "number") throw new Error(`the analysis has no ${name} for these settings; analyse them again`);
  return v;
}

const within = (v: number, [low, high]: [number, number]): number => Math.min(high, Math.max(low, v));

function slot(effect: string, params: Record<string, number | string>): RackSlot {
  return { effect, on: true, params };
}

// gain effects that add up to db; none for 0 dB
export function gains(db: number): RackSlot[] {
  const out: RackSlot[] = [];
  let rest = db;
  while (rest > GAIN_HIGH_DB || rest < GAIN_LOW_DB) {
    const step = rest > 0 ? GAIN_HIGH_DB : GAIN_LOW_DB;
    out.push(slot("gain", { db: step }));
    rest -= step;
  }
  if (rest) out.push(slot("gain", { db: Math.round(rest * 1e6) / 1e6 }));
  return out;
}

export function eqGains(fit: readonly number[], s: Settings): number[] {
  const offsets = EQ_BANDS.map((b) => num(s, b.key));
  if (!offsets.some((o) => o !== 0)) return [...fit];
  return fit.map((g, i) => within(g + (offsets[i] as number), [-EQ_GAIN_LIMIT_DB, EQ_GAIN_LIMIT_DB]));
}

function chain(s: Settings, n: Analysis): RackSlot[] {
  const out = gains(need(n, "pre_gain_db"));
  if (on(s, "chain_on")) {
    out.push(
      slot("chain_compressor", {
        threshold: num(s, "comp_threshold_db"),
        ratio: num(s, "comp_ratio"),
        attack: num(s, "comp_attack_ms"),
        release: num(s, "comp_release_ms"),
        gate: within(need(n, "gate_db"), GATE_RANGE),
        makeup: within(need(n, "makeup_db"), MAKEUP_RANGE),
      }),
    );
    if (num(s, "drive_db") > 0) {
      out.push(
        slot("chain_clip", {
          drive: num(s, "drive_db"),
          clip: on(s, "clip_on") ? "on" : "off",
          below: num(s, "clip_below_peak_db"),
          peak: within(need(n, "peak_db"), PEAK_RANGE),
        }),
      );
    }
  }
  let fit: number[];
  if (on(s, "eq_fit")) {
    if (!Array.isArray(n.eq_fit_db)) throw new Error("the analysis has no eq_fit_db; analyse again");
    fit = n.eq_fit_db;
  } else {
    fit = EQ_BANDS.map(() => 0);
  }
  eqGains(fit, s).forEach((gain, i) => {
    const band = EQ_BANDS[i];
    if (gain && band) {
      out.push(slot("eq", { kind: band.kind, freq: band.freq, gain, q: band.kind === "peak" ? EQ_Q : SHELF_Q }));
    }
  });
  out.push(...gains(need(n, "post_gain_db") + num(s, "vocal_db")));
  return out;
}

function voice(s: Settings, n: Analysis): RackSlot[] {
  const out: RackSlot[] = [];
  const tune = num(s, "hard_tune");
  if (tune > 0) {
    const strength = Math.min(tune / 100, 1);
    out.push(
      slot("tune", {
        key: "C",
        scale: "chromatic",
        reference: Math.min(100, Math.max(-100, need(n, "tuning_cents"))),
        speed: (1 - strength) * TUNE_MAX_GLIDE_MS,
        humanize: 0,
        mix: 1,
      }),
    );
  }
  const formant = num(s, "formant");
  if (formant !== 1) out.push(slot("formant", { semitones: 0, factor: formant }));
  const octave = num(s, "voice_octave");
  if (octave) out.push(slot("pitch", { semitones: 12 * octave, preserve: "on" }));
  if (on(s, "nightcore")) out.push(slot("pitch", { semitones: 12, preserve: "off" }));
  return out;
}

function layers(s: Settings, n: Analysis): RackSlot[] {
  const out: RackSlot[] = [];
  if (on(s, "octave_down_on")) {
    out.push(slot("octaver", { down: 10 ** (num(s, "octave_down_db") / 20), up: 0, dry: 1 }));
  }
  if (on(s, "octave_up_on")) {
    out.push(slot("octaver", { down: 0, up: 10 ** (num(s, "octave_up_db") / 20), dry: 1 }));
  }
  if (on(s, "doubles_on")) {
    out.push(
      slot("doubler", {
        level: num(s, "doubles_db"),
        delay: num(s, "doubles_left_ms"),
        depth: Math.min(num(s, "doubles_wander_ms"), DOUBLER_MAX_DEPTH_MS),
      }),
    );
  }
  if (on(s, "bitcrush_on")) {
    const peak = need(n, "bitcrush_peak_db");
    out.push(...gains(-peak));
    out.push(slot("bitcrush", { bits: num(s, "bitcrush_bits"), hold: num(s, "bitcrush_hold") }));
    out.push(...gains(peak));
  }
  return out;
}

function fx(s: Settings, n: Analysis): RackSlot[] {
  const out: RackSlot[] = [];
  if (on(s, "throw_on")) {
    out.push(
      slot("throws", {
        time: num(s, "throw_ms"),
        feedback: num(s, "throw_feedback"),
        tone: num(s, "throw_tone_hz"),
        level: num(s, "throw_db"),
        threshold: within(need(n, "throw_threshold_db"), THROW_THRESHOLD_RANGE),
        gap: MIN_GAP_MS,
        tail: THROW_TAIL_MS,
      }),
    );
  }
  if (on(s, "reverb_on")) {
    // the engine adds the wet on top of the dry; the room blends them, so the sum is raised back
    const wet = 10 ** (num(s, "reverb_mix_db") / 20);
    out.push(
      slot("room", {
        mix: wet / (1 + wet),
        predelay: num(s, "reverb_predelay_ms"),
        width: 1,
        decay: num(s, "reverb_decay_s"),
        damping: num(s, "reverb_tone_hz"),
      }),
    );
    out.push(...gains(20 * Math.log10(1 + wet)));
  }
  return out;
}

export function builtIn(s: Settings, n: Analysis): RackSlot[] {
  return [...chain(s, n), ...voice(s, n), ...layers(s, n), ...fx(s, n)];
}

function userRack(s: Settings): UserEffect[] {
  return Array.isArray(s.rack) ? s.rack : [];
}

export function buildRacks(s: Settings, n: Analysis): Racks {
  const vocal = builtIn(s, n);
  for (const e of userRack(s)) vocal.push({ effect: e.effect, on: e.on, params: { ...e.params } });
  if (vocal.length > MAX_RACK_EFFECTS) {
    throw new Error(`the vocal rack needs ${vocal.length} effects with the user's rack; a rack takes ${MAX_RACK_EFFECTS}`);
  }
  const beat = gains(num(s, "beat_db"));
  if (num(s, "bass_drive_db") > 0) {
    beat.push(slot("bass_drive", { freq: num(s, "bass_freq_hz"), drive: num(s, "bass_drive_db") }));
  }
  const master: RackSlot[] = [];
  if (on(s, "master_on")) {
    master.push(
      slot("master", {
        gain: within(need(n, "master_gain_db"), [-MASTER_GAIN_LIMIT_DB, MASTER_GAIN_LIMIT_DB]),
        trim: need(n, "master_trim_db"),
        ceiling: CEILING_DB,
      }),
    );
  }
  return { vocal, beat, master };
}

// the user rack's lanes at their slots in the vocal rack
export function buildLanes(s: Settings, n: Analysis): Lane[] {
  const first = builtIn(s, n).length;
  return userRack(s).flatMap((e, i) =>
    (e.automation ?? []).map((lane) => ({ slot: first + i, param: lane.param, points: lane.points })),
  );
}

export function rackJson(slots: readonly RackSlot[]): string {
  return JSON.stringify({ effects: slots });
}

export function parseRack(json: string): RackSlot[] {
  const data: unknown = JSON.parse(json);
  const effects = (data as { effects?: unknown }).effects;
  if (!Array.isArray(effects)) throw new Error("a rack is {effects: [...]}");
  return effects.map((e: { effect: string; on?: boolean; params?: Record<string, number | string> }) => ({
    effect: e.effect,
    on: e.on !== false,
    params: { ...(e.params ?? {}) },
  }));
}
