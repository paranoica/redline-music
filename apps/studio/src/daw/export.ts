// What Export runs and what its button says. Only the stale steps run: an own beat with edits or
// no render, or a flip with edits, is committed first, a stale mix is redone, then the result is rendered.

import { commitPlan } from "./transport.ts";
import type { ScreenFacts } from "./view.ts";

export type ExportStep = "commit" | "mix" | "result";

export type ExportPhase =
  | { kind: "idle" }
  | { kind: "running"; step: ExportStep }
  | { kind: "done"; folder: string | null }
  | { kind: "failed"; message: string };

export function exportPlan(f: Pick<ScreenFacts, "beatKind" | "hasBeatAudio" | "beatDirty" | "mixStale">): ExportStep[] {
  const plan: ExportStep[] = [];
  if (commitPlan(f, "export").wait) plan.push("commit");
  if (f.mixStale) plan.push("mix");
  plan.push("result");
  return plan;
}

const RUNNING: Record<ExportStep, string> = { commit: "бит…", mix: "сведение…", result: "рендер…" };

export function exportText(phase: ExportPhase): string {
  if (phase.kind === "running") return RUNNING[phase.step];
  if (phase.kind === "done") return "готово";
  return "Экспорт";
}
