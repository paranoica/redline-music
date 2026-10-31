// An edit of a pattern that several sections play changes all of them. The editor asks once per
// pattern «изменить во всех N местах / только здесь»; "only here" copies the pattern for the
// section being edited.

import { makeUnique, usage, type Score } from "../beat/model.ts";

export type Gate = { kind: "go" } | { kind: "ask"; count: number };

export type ShareChoice = "all" | "here";

// `answered`: the patterns the question was answered for in this editing
export function shareGate(score: Score, _section: number, pattern: string, answered: ReadonlySet<string>): Gate {
  if (answered.has(pattern)) return { kind: "go" };
  const count = usage(score, pattern).length;
  return count > 1 ? { kind: "ask", count } : { kind: "go" };
}

// the score and the pattern the edit goes to
export function chooseShare(score: Score, section: number, pattern: string, choice: ShareChoice): { score: Score; pattern: string } {
  if (choice === "all") return { score, pattern };
  const made = makeUnique(score, section, pattern);
  return { score: made.score, pattern: made.name };
}
