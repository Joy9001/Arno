import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "../api";
import type { ImportTask } from "../types";

export const CONTINUE_PROMPT = "Continue reading my statement.";
export const WHY_PROMPT = "Why did my statement import stop?";
/** Asks an open thread to follow a reply the server started; detail is `{ threadId }`. */
export const FOLLOW_REPLY_EVENT = "arno:follow-reply";

/** Polls the task while it runs, and while the chat's turn about its report is pending. */
export function useRun(taskId: string) {
  const [task, setTask] = useState<ImportTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const reload = useCallback(() => setRetry((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await api<ImportTask>(`/api/tasks/${encodeURIComponent(taskId)}`);
        if (!active) return;
        setTask(next);
        setError(null);
        if (next.state === "running" || next.follow_up !== null) {
          timer = window.setTimeout(() => void load(), 2000);
        }
      } catch (caught) {
        if (active) setError(errorMessage(caught));
      }
    };
    void load();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [taskId, retry]);

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

/** Host-made: how the run ended, in one line and a sentence. */
export function runEnding(task: ImportTask): {
  tone: "live" | "paused" | "failed" | "done";
  line: string;
  title: string;
  body: string;
} {
  const failedStep = [...task.run.steps].reverse().find((step) => step.status === "failed");
  const reason = task.imports.find((item) => item.reason)?.reason ?? null;
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
  const why = reason ?? task.report?.reason ?? failedStep?.error ?? "It stopped before it finished.";
  return {
    tone: "failed",
    line: "Stopped",
    title: "Stopped before it finished",
    body: `${why} Nothing was written to your ledger.`,
  };
}

