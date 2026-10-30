// The four pads of the mix station: A is the auto result, B to D move one setting a step.
// The numbers say which is closer to the references, never which is better.

import type { VariantsReply } from "./api.ts";
import { h } from "../ui/dom.ts";

export const LETTERS = ["A", "B", "C", "D"] as const;

const AXES: Record<string, string> = {
  vocal_db: "уровень голоса",
  comp_threshold_db: "порог компрессора",
  drive_db: "драйв",
};

export type Relation = "closer" | "further" | "same";

export interface Pad {
  index: number;
  letter: string;
  kind: "авто" | "вариант";
  axis: string;
  distance: number | null;
  // the distance against A's: only for B to D
  relation: Relation | null;
  delta: number | null;
  // 0..1 for the bar, the longest distance on the page is 1
  bar: number | null;
  kept: boolean;
}

export function decimal(value: number, digits = 2): string {
  return value.toFixed(digits).replace(".", ",").replace("-", "−");
}

function plain(value: number): string {
  return decimal(value, Math.abs(value % 1) < 1e-9 ? 0 : 1);
}

export function axisLabel(key: string): string {
  return AXES[key] ?? key;
}

export function relationText(relation: Relation, delta: number): string {
  if (relation === "same") return "так же близко";
  return `${relation === "closer" ? "ближе" : "дальше"} на ${decimal(Math.abs(delta))}`;
}

// A plus up to three variants; `kept` is the index the song keeps, null for none
export function padModel(reply: VariantsReply | null, kept: number | null): Pad[] {
  if (!reply) return [];
  const base = reply.auto.distance_after ?? null;
  const rows = reply.variants.slice(0, 3);
  const distances = [base, ...rows.map((v) => v.distance)].filter((d): d is number => typeof d === "number");
  const longest = Math.max(...distances, 0);
  const bar = (d: number | null): number | null => (d === null || longest <= 0 ? null : Math.max(0.04, d / longest));
  const pads: Pad[] = [
    {
      index: 0,
      letter: "A",
      kind: "авто",
      axis: "как нашёл поиск",
      distance: base,
      relation: null,
      delta: null,
      bar: bar(base),
      kept: kept === 0,
    },
  ];
  rows.forEach((v, i) => {
    const before = reply.auto[v.axis];
    const from = typeof before === "number" ? `${plain(before)} → ` : "";
    const delta = v.distance !== null && base !== null ? v.distance - base : null;
    const relation: Relation | null =
      delta === null ? null : Math.abs(delta) < 0.005 ? "same" : delta < 0 ? "closer" : "further";
    pads.push({
      index: i + 1,
      letter: LETTERS[i + 1] ?? String(i + 1),
      kind: "вариант",
      axis: `${axisLabel(v.axis)}: ${from}${plain(v.value)} дБ`,
      distance: v.distance,
      relation,
      delta,
      bar: bar(v.distance),
      kept: kept === i + 1,
    });
  });
  return pads;
}

export const PADS_KEY = "Полоса — расстояние до референсов: короче — ближе.";
export const PADS_LEDE =
  "A — ближайшее к референсам, что нашёл поиск. B, C и D сдвигают одну настройку на шаг от A, поэтому по числам обычно дальше. Их стоит послушать: числа говорят, что ближе, а не что звучит приятнее.";

export interface PadsView {
  el: HTMLElement;
  update(pads: readonly Pad[], selected: number): void;
}

export function createPads(onSelect: (index: number) => void): PadsView {
  const group = h("div", { class: "pads", role: "radiogroup", "aria-label": "Варианты сведения" });
  const marker = h("span", { class: "pads__marker", "aria-hidden": "true" });
  const el = h("div", { class: "pads-wrap" }, group, h("p", { class: "pads__key" }, PADS_KEY));
  const update = (pads: readonly Pad[], selected: number): void => {
    group.replaceChildren();
    group.style.setProperty("--at", String(Math.max(0, selected)));
    for (const pad of pads) {
      const near =
        pad.relation === null || pad.delta === null
          ? pad.kept
            ? h("span", { class: "pad__kept" }, "оставлен")
            : h("span", {}, "")
          : h("span", {}, relationText(pad.relation, pad.delta));
      const button = h(
        "button",
        { class: "pad", type: "button", role: "radio", "aria-checked": String(pad.index === selected) },
        h("span", { class: "pad__letter", "aria-hidden": "true" }, pad.letter),
        h("span", { class: "pad__kind" }, h("span", { class: "vh" }, `${pad.letter}, `), pad.kind),
        h("span", { class: "pad__axis num" }, pad.axis),
        h(
          "span",
          { class: "pad__dist" },
          h("span", {}, "до референсов ", h("span", { class: "num" }, pad.distance === null ? "—" : decimal(pad.distance))),
          pad.relation !== null && pad.kept ? h("span", { class: "pad__kept" }, "оставлен") : near,
          pad.bar === null ? null : h("span", { class: "distbar", style: `--v: ${pad.bar.toFixed(2)}`, "aria-hidden": "true" }),
        ),
      );
      button.addEventListener("click", () => onSelect(pad.index));
      group.append(button);
    }
    group.append(marker);
  };
  return { el, update };
}

// the settings a pad stands for, as the mix loads them; A carries the search's numbers too
export function padSettings(reply: VariantsReply | null, index: number): Record<string, unknown> | null {
  if (!reply) return null;
  if (index === 0) {
    const { distance_before: _b, distance_after: _a, at_edge: _e, ...settings } = reply.auto;
    return settings;
  }
  return reply.variants[index - 1]?.settings ?? null;
}
