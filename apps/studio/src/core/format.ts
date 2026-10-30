// Numbers as the page shows them: Russian units, a real minus sign, tabular-friendly text.

const NB = " ";
const MINUS = "−";

const UNITS: Record<string, string> = {
  dB: "дБ",
  dBFS: "дБFS",
  Hz: "Гц",
  ms: "мс",
  s: "с",
  x: "×",
  st: "пт",
  cents: "ц",
  bits: "бит",
  samples: "сэмпл.",
  voices: "гол.",
  degrees: "ступ.",
  midi: "MIDI",
  "/s": "/с",
  bpm: "BPM",
  q: "Q",
  octaves: "окт",
  notes: "нот",
  bands: "полос",
  choice: "",
};

// units written against the number, with no space
const GLUED = new Set([":1", "×"]);

export function unitLabel(unit: string): string {
  return UNITS[unit] ?? unit;
}

function decimals(value: number, step: number | undefined): number {
  if (step !== undefined && step > 0) return Math.min(3, Math.max(0, -Math.floor(Math.log10(step) + 1e-9)));
  const size = Math.abs(value);
  return size >= 100 ? 0 : size >= 10 ? 1 : 2;
}

export function formatNumber(value: number, places: number): string {
  const text = value.toFixed(places);
  const zero = Number(text) === 0;
  return zero ? text.replace("-", "") : text.replace("-", MINUS);
}

export function formatValue(value: number, unit: string, step?: number): string {
  let shown = unitLabel(unit);
  let v = value;
  let places = decimals(value, step);
  if (shown === "Гц" && Math.abs(value) >= 1000) {
    shown = "кГц";
    v = value / 1000;
    places = 1;
  }
  const number = formatNumber(v, places);
  if (!shown) return number;
  return GLUED.has(shown) ? `${number}${shown}` : `${number}${NB}${shown}`;
}

// no level yet reads as a dash; silence heard while playing is a real −∞
export function peakReadout(db: number, playing: boolean): string {
  if (Number.isFinite(db)) return formatNumber(db, 1);
  return playing ? `${MINUS}∞` : "—";
}

export function clock(seconds: number): string {
  const tenths = Math.max(0, Math.round(seconds * 10));
  const m = Math.floor(tenths / 600);
  const rest = tenths - m * 600;
  const s = Math.floor(rest / 10);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${rest % 10}`;
}
