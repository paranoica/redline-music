// The server's jobs and refusals in words, and the job list helpers.

import type { ApiError, Job, JobKind } from "./api.ts";

export const JOB_NAMES: Record<JobKind, string> = {
  separate: "отделение голоса",
  rank: "оценка дублей",
  auto: "авто-сведение",
  result: "сборка итога",
  beat_render: "рендер бита",
  flip_stems: "разбор на стемы",
  flip_render: "сборка флипа",
  mix: "сведение",
};

export const JOB_STATE: Record<Job["state"], string> = {
  queued: "в очереди",
  running: "идёт",
  cancelling: "отменяется",
  done: "готово",
  failed: "не вышло",
  cancelled: "отменено",
};

export function jobActive(job: Job | undefined): boolean {
  return job !== undefined && (job.state === "queued" || job.state === "running" || job.state === "cancelling");
}

export function runningText(jobs: readonly Job[]): string {
  const live = jobs.filter(jobActive).map((j) => `${JOB_NAMES[j.kind]} · ${JOB_STATE[j.state]}`);
  return live.length ? live.join(", ") : "ничего не идёт";
}

// the kinds whose job has finished between two reads of the job list
export function finishedKinds(before: readonly Job[], next: readonly Job[]): JobKind[] {
  const done: JobKind[] = [];
  for (const job of next) {
    if (jobActive(job) || job.finished === null) continue;
    const was = before.find((j) => j.kind === job.kind);
    if (!was || jobActive(was) || was.finished !== job.finished) done.push(job.kind);
  }
  return done;
}

const REFUSALS: Record<string, string> = {
  take_running: "Идёт дубль: дождись конца.",
  no_voice: "Голос песни не выбран: отметь лучший дубль ★.",
  no_takes: "Дублей нет: сначала запиши хотя бы один.",
  no_beat_seed: "Бит взят из файла, у него нет seed: редактировать его здесь нельзя.",
  not_ready: "Движок записи ещё не готов.",
  out_of_sync: "Движок записи не отвечает: закрой его и открой снова.",
  closed: "Движок записи закрыт: запись (R) откроет его.",
  engine_busy: "Движок записи открывается или калибруется: подожди немного.",
};

// what a refusal from the server says to the person, by its code
export function refusalText(error: Pick<ApiError, "code" | "message">, jobs: readonly Job[]): string {
  if (error.code === "busy") return `Сейчас занято: ${runningText(jobs)}. Подожди, пока закончится.`;
  return REFUSALS[error.code] ?? (error.message || "Не получилось.");
}
