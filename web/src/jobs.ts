import { useEffect, useState } from "react";
import { create } from "zustand";
import { formatDuration } from "./format";
import { goTo } from "./nav";
import type { JobActivity, JobView } from "./types";

type JobCopy = { title: string; rail: string };

/** The few jobs worth a place in the rail and a toast when they finish. */
export const USER_FACING_KINDS = new Set([
  "categorize_import",
  "collect_batch",
  "scheduled_question",
  "weekly_briefing",
]);

export const JOB_COPY: Record<string, JobCopy> = {
  categorize_import: { title: "Categorizing transactions", rail: "Categorizing…" },
  collect_batch: { title: "Waiting on batch categorization", rail: "Collecting results…" },
  scheduled_question: { title: "Answering your scheduled question", rail: "Answering…" },
  weekly_briefing: { title: "Writing your weekly briefing", rail: "Writing briefing…" },
  recompute_baselines: { title: "Updating spending baselines", rail: "Updating baselines…" },
  pattern_detection: { title: "Looking for recurring charges", rail: "Looking for patterns…" },
  risk_check: { title: "Checking your projection", rail: "Checking…" },
  reminders: { title: "Preparing reminders", rail: "Preparing reminders…" },
  backup_check: { title: "Checking backups", rail: "Checking backups…" },
  recaps: { title: "Preparing recaps", rail: "Preparing recaps…" },
};

function sentenceCase(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function jobKindTitle(kind: string): string {
  return JOB_COPY[kind]?.title ?? sentenceCase(kind);
}

export function jobRailLabel(kind: string): string {
  return JOB_COPY[kind]?.rail ?? `${sentenceCase(kind)}…`;
}

/** The task's name plus its subject, when the server knew one: "Categorizing transactions · HDFC…". */
export function jobTitle(job: JobView): string {
  return job.subject ? `${jobKindTitle(job.kind)} · ${job.subject}` : jobKindTitle(job.kind);
}

/** Queued, Running, Retrying, Done, or Failed: the badge in a list. */
export function jobStatusLabel(job: JobView): string {
  if (job.status === "running") return "Running";
  if (job.status === "done") return "Done";
  if (job.status === "failed") return "Failed";
  return job.attempts > 0 ? "Retrying" : "Queued";
}

/** One line of live status, with the wait or elapsed time. */
export function jobStatusText(job: JobView, now: number): string {
  if (job.status === "pending") {
    const seconds = (new Date(job.run_at).getTime() - now) / 1000;
    if (job.attempts > 0) {
      const attempt = `attempt ${job.attempt} of ${job.max_attempts}`;
      return seconds > 1 ? `Retrying in ${formatDuration(seconds)} · ${attempt}` : `Retrying · ${attempt}`;
    }
    if (seconds > 1) return `Starts in ${formatDuration(seconds)}`;
    return "Queued";
  }

  if (job.status === "running") {
    const started = job.started_at ? new Date(job.started_at).getTime() : now;
    return `Running · ${formatDuration((now - started) / 1000)}`;
  }

  if (job.status === "done") {
    const took =
      job.started_at && job.finished_at
        ? (new Date(job.finished_at).getTime() - new Date(job.started_at).getTime()) / 1000
        : null;
    return took === null ? "Done" : `Done · took ${formatDuration(took)}`;
  }

  return job.attempts > 0
    ? `Failed after ${job.attempts} ${job.attempts === 1 ? "attempt" : "attempts"}`
    : "Failed";
}

type CategorizeResult = {
  status: "done" | "partial" | "nothing_to_do" | "batch_submitted";
  labeled: number;
  uncategorized: number;
  failed_payees: number;
};

function categorizeResult(job: JobView): CategorizeResult | null {
  const result = job.result;
  if (!result || typeof result.status !== "string") return null;
  const number = (value: unknown) => (typeof value === "number" ? value : 0);
  return {
    status: result.status as CategorizeResult["status"],
    labeled: number(result.labeled),
    uncategorized: number(result.uncategorized),
    failed_payees: number(result.failed_payees),
  };
}

/** "120 labeled · 14 need a category", shared by the jobs list, a toast, and an import. */
export function categorizeSummary(
  labeled: number,
  uncategorized: number,
  failedPayees = 0,
): string {
  const need =
    uncategorized === 1 ? "1 needs a category" : `${uncategorized} need a category`;
  const unanswered = failedPayees > 0 ? ` · ${failedPayees} got no answer` : "";
  return `${labeled} labeled · ${need}${unanswered}`;
}

/** The one-line human result of a finished job, or null when there is nothing to say. */
export function jobOutcome(job: JobView): string | null {
  if (job.kind !== "categorize_import") return null;
  const result = categorizeResult(job);
  if (!result) return null;
  if (result.status === "nothing_to_do") return "All rows matched your rules";
  if (result.status === "batch_submitted") return "Sent as a batch; results in a few minutes";
  return categorizeSummary(result.labeled, result.uncategorized, result.failed_payees);
}

/** A backend error, cut to fit a line of UI. */
export function shortJobError(message: string | null, limit = 120): string {
  const text = message?.trim();
  if (!text) return "Something went wrong.";
  return text.length > limit ? `${text.slice(0, limit - 3)}…` : text;
}

/** The active jobs a person asked for, in the order the server sent them. */
export function userFacingJobs(active: JobView[]): JobView[] {
  return active.filter((job) => USER_FACING_KINDS.has(job.kind));
}

/** An accessible summary of what is running; it changes only when the set of jobs changes. */
export function activeJobSummary(active: JobView[], now: number): string {
  return userFacingJobs(active)
    .map((job) => {
      const status = jobStatusText(job, now).toLowerCase().replace(" · ", ", ");
      return `${jobKindTitle(job.kind)}${job.subject ? `, ${job.subject}` : ""}, ${status}`;
    })
    .join("; ");
}

/** Go to the ledger's Uncategorized list, where a categorization result is reviewed. */
export function openUncategorized(): void {
  goTo("activity", "ledger");
}

export type JobToast = {
  key: string;
  job: JobView;
  tone: "good" | "danger";
};

function toastFor(job: JobView): JobToast | null {
  const key = `${job.id}:${job.finished_at ?? job.attempt}`;
  if (job.status === "failed") return { key, job, tone: "danger" };
  if (job.status !== "done" || !USER_FACING_KINDS.has(job.kind)) return null;
  // A batch poll that only re-queued itself is not news.
  if (job.kind === "collect_batch" && job.result?.status !== "collected") return null;
  return { key, job, tone: "good" };
}

/** Finishes already announced. The activity cursor overlaps on purpose, so a finish can repeat. */
const SEEN_LIMIT = 200;

type JobStore = {
  seen: string[];
  active: JobView[];
  lastFinished: JobView[];
  /** Bumped whenever a job finishes, so views that read the database know to refetch. */
  version: number;
  /** The server's clock at the last poll; the next poll asks for what finished after it. */
  since: string | null;
  kick: number;
  toasts: JobToast[];
  applyActivity: (activity: JobActivity) => void;
  dismissToast: (key: string) => void;
  kickActivity: () => void;
};

export const useJobs = create<JobStore>()((set) => ({
  seen: [],
  active: [],
  lastFinished: [],
  version: 0,
  since: null,
  kick: 0,
  toasts: [],
  applyActivity: (activity) =>
    set((state) => {
      const seen = new Set(state.seen);
      const finished = activity.finished.filter(
        (job) => !seen.has(`${job.id}:${job.finished_at ?? ""}`),
      );
      const fresh = finished
        .map(toastFor)
        .filter((toast): toast is JobToast => toast !== null);
      return {
        seen: [
          ...state.seen,
          ...finished.map((job) => `${job.id}:${job.finished_at ?? ""}`),
        ].slice(-SEEN_LIMIT),
        active: activity.active,
        lastFinished: finished.length ? finished : state.lastFinished,
        version: state.version + (finished.length ? 1 : 0),
        since: activity.now,
        toasts: fresh.length ? [...state.toasts, ...fresh] : state.toasts,
      };
    }),
  dismissToast: (key) =>
    set((state) => ({ toasts: state.toasts.filter((toast) => toast.key !== key) })),
  kickActivity: () => set((state) => ({ kick: state.kick + 1 })),
}));

/** Ask the shell's poller to check now, for a write that just started a job. */
export function kickActivity(): void {
  useJobs.getState().kickActivity();
}

export function dismissJobToast(key: string): void {
  useJobs.getState().dismissToast(key);
}

/** A clock that ticks only while something is running. */
export function useNow(ticking = true, intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [ticking, intervalMs]);
  return now;
}
