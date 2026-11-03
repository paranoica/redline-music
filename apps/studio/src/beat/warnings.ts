// The check's warnings in Russian. The server writes them in English; the kinds the page knows
// are retold from their numbers, anything else is shown as it came.

export interface WarningLike {
  kind: string;
  where: string;
  message: string;
  source: string;
}

const DRUMS: Record<string, string> = { hat: "хэт", kick: "бочка" };

function times(n: number): string {
  const d = n % 10;
  const t = n % 100;
  if (d >= 2 && d <= 4 && (t < 12 || t > 14)) return "раза";
  return "раз";
}

export function warningText(w: WarningLike): string {
  let m: RegExpExecArray | null;
  switch (w.kind) {
    case "density":
      m = /^([\d.]+) (\w+) hits a bar; (.+) keeps ([\d.]+) to ([\d.]+)$/.exec(w.message);
      if (m) return `${DRUMS[m[2] as string] ?? m[2]}: ${m[1]} удара за такт, для ${m[3]} — от ${m[4]} до ${m[5]}`;
      break;
    case "repeat":
      m = /^the same bar plays (\d+) times in a row/.exec(w.message);
      if (m) return `один и тот же такт ${m[1]} ${times(Number(m[1]))} подряд — меняй что-нибудь раз в 4–8 тактов`;
      break;
    case "unchanged":
      m = /^the same sounds \((.*)\) play for (\d+) bars/.exec(w.message);
      if (m) return `одни и те же звуки (${m[1]}) ${m[2]} тактов подряд — вводи или убирай звук хотя бы раз в 8 тактов`;
      break;
    case "flat_sections":
      m = /^the verse plays the hook's drum pattern (.+); give the verse/.exec(w.message);
      if (m) return `куплет играет паттерн ударных хука (${m[1]}) — дай куплету свою бочку и хэты`;
      if (/^the hook has no melodic part of its own/.test(w.message)) {
        return "у хука нет своей мелодической партии — добавь фигуру хука, контрмелодию или слой октавой выше, которых нет в куплете";
      }
      break;
    case "busy_808":
      m = /^([\d.]+) 808 notes start a bar \(median\); keep 1 to (\d+)/.exec(w.message);
      if (m) return `808: ${m[1]} нот за такт — оставь 1–${m[2]} длинные`;
      break;
    case "off_kick":
      m = /^(\d+) of (\d+) kicks under the 808 start an 808 note/.exec(w.message);
      if (m) return `808 начинается с бочкой только ${m[1]} ${times(Number(m[1]))} из ${m[2]} — ставь ноту 808 на большинство бочек, дополнительные ноты — в паузы`;
      break;
  }
  return w.message;
}

export function sourceText(source: string): string {
  if (source === "blog, unverified") return "источник: блог, не проверено";
  if (source === "own choice") return "источник: своё решение";
  if (source === "measured on the owner's tracks") return "источник: замер твоих референсов";
  return `источник: ${source}`;
}

export function whereText(where: string): string {
  let m = /^bars (\d+)-(\d+)$/.exec(where);
  if (m) return `такты ${m[1]}–${m[2]}`;
  m = /^bars? (\d+)$/.exec(where);
  if (m) return `такт ${m[1]}`;
  m = /^bars (.+)$/.exec(where);
  if (m) return `такты ${m[1]}`;
  m = /^sections\[\d+\] \((.*)\)$/.exec(where);
  if (m) return `секция «${m[1]}»`;
  if (where === "hook and verse") return "хук и куплет";
  return where;
}

const LANES: Record<string, string> = { velocity: "громкость", pitch: "высота", decay: "затухание", chance: "шанс" };

/** An error's JSON path as the owner reads the score: pattern, sound, note or step. */
export function pathText(path: string): string {
  const row = /^patterns\.([a-z0-9_]+)\.rows\.([a-z0-9_]+)(.*)$/.exec(path);
  if (row) {
    const [, pattern, sound, rest] = row as unknown as [string, string, string, string];
    const head = `${pattern} · ${sound}`;
    const note = /^\[(\d+)\]/.exec(rest);
    if (note) return `${head} · нота ${Number(note[1]) + 1}`;
    const lane = /^\.(velocity|pitch|decay|chance)\[(\d+)\]/.exec(rest);
    if (lane) return `${head} · шаг ${Number(lane[2]) + 1}, ${LANES[lane[1]!]}`;
    return rest ? `${head}${rest}` : head;
  }
  const section = /^sections\[(\d+)\](?:\.(.+))?$/.exec(path);
  if (section) return `секция ${Number(section[1]) + 1}${section[2] ? ` · ${section[2]}` : ""}`;
  return path;
}
