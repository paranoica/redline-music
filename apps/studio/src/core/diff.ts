// What it takes to go from one rack to the next: nothing, params set in place, or a rebuild.

import type { EffectSpec, ParamSpec, ParamValue, RackSlot } from "../types.ts";

export type Catalogue = ReadonlyMap<string, EffectSpec>;

export interface ParamSet {
  slot: number;
  param: string;
  value: number;
}

export type RackPlan = { kind: "same" } | { kind: "set"; sets: ParamSet[] } | { kind: "rebuild" };

const EPSILON = 1e-9;

export function catalogueOf(effects: readonly EffectSpec[]): Catalogue {
  return new Map(effects.map((e) => [e.effect, e]));
}

// a param's value as a number: a choice by name becomes its index
export function paramNumber(spec: ParamSpec, value: ParamValue | undefined): number {
  if (value === undefined) return spec.default;
  if (typeof value === "string") {
    const i = spec.choices?.indexOf(value) ?? -1;
    return i < 0 ? Number.NaN : i;
  }
  return value;
}

function differs(a: number, b: number): boolean {
  if (Number.isNaN(a) || Number.isNaN(b)) return true;
  return Math.abs(a - b) > EPSILON * Math.max(1, Math.abs(a), Math.abs(b));
}

export function planRack(before: readonly RackSlot[], after: readonly RackSlot[], catalogue: Catalogue): RackPlan {
  if (before.length !== after.length) return { kind: "rebuild" };
  const sets: ParamSet[] = [];
  for (let slot = 0; slot < after.length; slot++) {
    const a = before[slot] as RackSlot;
    const b = after[slot] as RackSlot;
    if (a.effect !== b.effect || a.on !== b.on) return { kind: "rebuild" };
    const spec = catalogue.get(b.effect);
    if (!spec) {
      if (JSON.stringify(a.params) !== JSON.stringify(b.params)) return { kind: "rebuild" };
      continue;
    }
    for (const p of spec.params) {
      const x = paramNumber(p, a.params[p.name]);
      const y = paramNumber(p, b.params[p.name]);
      if (!differs(x, y)) continue;
      if (p.choices || p.fixed_while_playing) return { kind: "rebuild" };
      sets.push({ slot, param: p.name, value: y });
    }
  }
  return sets.length ? { kind: "set", sets } : { kind: "same" };
}
