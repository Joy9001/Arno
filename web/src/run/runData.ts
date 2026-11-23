import { useCallback, useEffect, useSyncExternalStore } from "react";
import { api, errorMessage } from "../api";
import type { ImportTask } from "../types";

export const CONTINUE_PROMPT = "Continue reading my statement.";
export const WHY_PROMPT = "Why did my statement import stop?";
/** Asks an open thread to follow a reply the server started; detail is `{ threadId }`. */
export const FOLLOW_REPLY_EVENT = "arno:follow-reply";

const POLL_MS = 2000;
const MAX_BACKOFF_MS = 30_000;

type RunSnapshot = { task: ImportTask | null; error: string | null };

type RunEntry = {
  task: ImportTask | null;
  error: string | null;
  /** The value `useSyncExternalStore` reads; a new object on every change. */
  snapshot: RunSnapshot;
  timer: number | undefined;
  loading: boolean;
  failures: number;
  started: boolean;
  listeners: Set<() => void>;
};

// One entry per task id, shared by every card that mounts it: a finished task
// is read once, and a running one has a single poller however many cards show it.
const runs = new Map<string, RunEntry>();

function entryFor(taskId: string): RunEntry {
  let entry = runs.get(taskId);
  if (!entry) {
    entry = {
      task: null,
      error: null,
      snapshot: { task: null, error: null },
      timer: undefined,
      loading: false,
      failures: 0,
      started: false,
      listeners: new Set(),
    };
    runs.set(taskId, entry);
  }
  return entry;
}

function notify(entry: RunEntry) {
  entry.snapshot = { task: entry.task, error: entry.error };
  for (const listener of [...entry.listeners]) listener();
}

/** The task still changes on the server: it runs, or its report's chat turn is pending. */
const pollDue = (task: ImportTask | null): boolean =>
  task === null || task.state === "running" || task.follow_up !== null;

function schedule(taskId: string, entry: RunEntry, delay: number) {
  if (entry.timer !== undefined) window.clearTimeout(entry.timer);
  entry.timer = window.setTimeout(() => {
    entry.timer = undefined;
    void load(taskId, entry);
  }, delay);
}

async function load(taskId: string, entry: RunEntry) {
  if (entry.loading) return;
  entry.loading = true;
  try {
    const next = await api<ImportTask>(`/api/tasks/${encodeURIComponent(taskId)}`);
    entry.task = next;
    entry.error = null;
    entry.failures = 0;
    notify(entry);
    if (pollDue(next)) schedule(taskId, entry, POLL_MS);
  } catch (caught) {
    entry.error = errorMessage(caught);
    notify(entry);
    // A network blip must not end a run's updates; back off, then keep asking.
    if (pollDue(entry.task)) {
      entry.failures += 1;
      schedule(
        taskId,
        entry,
        Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** Math.min(entry.failures, 4)),
      );
    }
  } finally {
    entry.loading = false;
  }
}

function startRun(taskId: string) {
  const entry = entryFor(taskId);
  if (entry.started) return;
  entry.started = true;
  if (entry.timer === undefined && !entry.loading) void load(taskId, entry);
}

function reloadRun(taskId: string) {
  const entry = entryFor(taskId);
  entry.failures = 0;
  if (entry.timer !== undefined) {
    window.clearTimeout(entry.timer);
    entry.timer = undefined;
  }
  void load(taskId, entry);
}

/** Polls the task while it runs, and while the chat's turn about its report is pending. */
export function useRun(taskId: string) {
  const entry = entryFor(taskId);
  const { task, error } = useSyncExternalStore(
    useCallback(
      (listener: () => void) => {
        entry.listeners.add(listener);
        return () => entry.listeners.delete(listener);
      },
      [entry],
    ),
    () => entry.snapshot,
  );
  useEffect(() => {
    startRun(taskId);
  }, [taskId]);
  const reload = useCallback(() => reloadRun(taskId), [taskId]);
  return { task, error, reload };
}

export function clock(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(Math.round(seconds % 60)).padStart(2, "0")}`;
}

export function stepTime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds > 0 && seconds < 0.001) return "<1 ms";
  if (seconds < 1) return `${Math.max(0, Math.round(seconds * 1000))} ms`;
  return clock(seconds);
}

export function elapsed(task: ImportTask): number {
  const end = task.finished_at ?? Date.now() / 1000;
  return end - task.started_at;
}

/** A model or tool failure, which is never the card's sentence. */
const INTERNAL_DETAIL =
  /^(The parser stopped with an error|Traceback)|ModelRetry|ModelServerError|ModelHTTPError|ModelAPIError|status_code|model_name|open_[a-z_]+|[A-Za-z]+_[A-Za-z]+/i;

/** The first reason that is a host-written sentence rather than a raw error. */
function plainReason(...candidates: (string | null | undefined)[]): string | null {
  for (const text of candidates) {
    const line = text?.trim();
    if (line && !INTERNAL_DETAIL.test(line)) return line;
  }
  return null;
}

const MODEL_FAILED =
  /ModelServerError|ModelHTTPError|ModelAPIError|status_code|model_name|connection reset|crashed/i;

/** Host-made: how the run ended, in one line and a sentence. */
export function runEnding(task: ImportTask): {
  tone: "live" | "paused" | "failed" | "done";
  line: string;
  title: string;
  body: string;
} {
  const failedStep = [...task.run.steps].reverse().find((step) => step.status === "failed");
  const importReasons = task.imports.map((item) => item.reason);
  const report = task.report;
  if (task.state === "running") {
    const step = task.run.steps.at(-1);
    const progress = task.imports.find((item) => item.progress)?.progress;
    const stageIndex = task.run.stages.findIndex((stage) => stage.status === "running");
    return {
      tone: "live",
      line: `Step ${Math.max(1, stageIndex + 1)} of ${task.run.stages.length} · ${
        step?.status === "running" ? step.title : progress ?? "Working"
      }`,
      title: "",
      body: "",
    };
  }
  if (task.run.stop === "budget" || task.imports.some((item) => item.status === "paused")) {
    return {
      tone: "paused",
      line: "Paused · out of steps",
      title: "Paused: it ran out of steps",
      body: `The parser used all ${task.run.limit} of its steps before it finished. Continue gives it another ${task.run.limit}; nothing was written to your ledger.`,
    };
  }
  const ready = task.imports.filter((item) => item.status === "review" || item.status === "committed");
  if (task.outcome === "ok" && ready.length === task.imports.length && ready.length > 0) {
    return { tone: "done", line: "Ready for review", title: "Ready for review", body: "" };
  }
  const raw = [report?.reason, ...importReasons, failedStep?.error].filter(Boolean).join(" ");
  const why =
    report?.status === "not_a_statement"
      ? (plainReason(...importReasons) ??
        "This file isn't a statement, so nothing was imported.")
      : report?.status === "blocked" || report?.status === "gave_up"
        ? (plainReason(report.blocking, report.reason, ...importReasons) ??
          "Something stopped it before it could read the statement.")
        : (plainReason(report?.blocking, ...importReasons, report?.reason) ??
          (task.outcome === "timeout"
            ? "The parser took too long to read this statement."
            : task.outcome === "cancelled"
              ? "The parser was stopped before it finished."
              : MODEL_FAILED.test(raw)
                ? "The model service failed before the statement was read."
                : "The parser could not read this statement."));
  return {
    tone: "failed",
    line: "Stopped",
    title: "Stopped before it finished",
    body: `${why} Nothing was saved.`,
  };
}
