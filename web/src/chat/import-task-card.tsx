"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, KeyRound, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AgentPlan } from "@/components/assistant-ui/elements/agent-plan.aui";
import { AgentStatus } from "@/components/assistant-ui/elements/agent-status.aui";
import { JobProgress } from "@/components/assistant-ui/elements/job-progress.aui";
import { OptionList } from "@/components/assistant-ui/elements/option-list.aui";
import { TaskCard, type TaskCardState } from "@/components/assistant-ui/elements/task-card.aui";
import { api, errorMessage } from "../api";
import type { AccountSummary, ImportTask, ImportTaskImport } from "../types";
import { displayText } from "./display-text";

const ACCOUNT_QUESTION = "Which account is this statement for?";
const SETTLED = new Set([
  "review",
  "committed",
  "rejected",
  "failed",
  "not_a_statement",
]);

const IMPORT_LABEL: Record<string, string> = {
  parsing: "Reading",
  needs_password: "Locked",
  needs_input: "Waiting on you",
  review: "Ready for review",
  not_a_statement: "Not a statement",
  failed: "Failed",
  committed: "Committed",
  rejected: "Rejected",
};

function importLabel(status: string): string {
  return IMPORT_LABEL[status] ?? status.replaceAll("_", " ");
}

function duration(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(whole / 60);
  return minutes ? `${minutes}m ${whole % 60}s` : `${whole}s`;
}

function LockedImport({
  importId,
  onUnlocked,
}: {
  importId: string;
  onUnlocked: () => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-1.5">
      <span className="fine">
        {displayText("This file is locked. The password never goes to the model or chat.")}
      </span>
      <div className="flex items-center gap-2">
        <input
          type="password"
          autoComplete="off"
          aria-label="Statement password"
          placeholder="Statement password"
          value={password}
          disabled={busy}
          onChange={(event) => setPassword(event.target.value)}
          className="min-w-0 flex-1 rounded-md border border-border/60 bg-transparent px-2 py-1 text-xs"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!password || busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await api(`/api/imports/${encodeURIComponent(importId)}/password`, {
                method: "POST",
                body: JSON.stringify({ password }),
              });
              setPassword("");
              onUnlocked();
            } catch (caught) {
              setError(errorMessage(caught));
            } finally {
              setBusy(false);
            }
          }}
        >
          <KeyRound size={13} aria-hidden="true" />
          {busy ? "Unlocking…" : "Unlock"}
        </Button>
      </div>
      {error && (
        <span className="error fine" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

function ImportLink({ item }: { item: ImportTaskImport }) {
  return (
    <Button variant="outline" size="sm" asChild>
      <a href={`#activity/imports/${encodeURIComponent(item.import_id)}`}>
        <ArrowUpRight size={14} strokeWidth={1.8} aria-hidden="true" />
        Open review
      </a>
    </Button>
  );
}

function TaskResult({ task }: { task: ImportTask }) {
  const report = task.report;
  if (!report) return null;
  const rows = (report.imports ?? []).reduce((total, item) => total + item.rows, 0);
  const debits = (report.imports ?? []).reduce((total, item) => total + item.debits, 0);
  const credits = (report.imports ?? []).reduce((total, item) => total + item.credits, 0);
  const files = report.imports?.length ?? 0;
  const detail = report.blocking ?? report.reason;
  return (
    <div className="flex flex-col gap-0.5">
      <span>
        {report.status.replaceAll("_", " ")} · {files} {files === 1 ? "file" : "files"} ·{" "}
        {rows} {rows === 1 ? "row" : "rows"} · {debits} debit · {credits} credit
      </span>
      {detail ? <span>{displayText(detail)}</span> : null}
    </div>
  );
}

export function ImportTaskCard({ taskId }: { taskId: string }) {
  const [task, setTask] = useState<ImportTask | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const reload = useCallback(() => setRetry((value) => value + 1), []);

  useEffect(() => {
    if (!taskId) return;
    let active = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await api<ImportTask>(`/api/tasks/${encodeURIComponent(taskId)}`);
        if (!active) return;
        setTask(next);
        setError(null);
        if (next.state === "running") {
          timer = window.setTimeout(() => void load(), 2000);
        }
      } catch (caught) {
        if (!active) return;
        setError(errorMessage(caught));
      }
    };
    void load();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [taskId, retry]);

  const running = task?.state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const accountQuestion = Boolean(
    task?.question &&
      task.imports.some(
        (item) =>
          item.status === "needs_input" && (item.reason ?? "").includes(ACCOUNT_QUESTION),
      ),
  );
  const [accounts, setAccounts] = useState<AccountSummary["accounts"]>([]);
  useEffect(() => {
    if (!accountQuestion) return;
    let active = true;
    api<AccountSummary>("/api/accounts")
      .then((summary) => {
        if (active) setAccounts(summary.accounts);
      })
      .catch(() => {
        if (active) setAccounts([]);
      });
    return () => {
      active = false;
    };
  }, [accountQuestion]);

  if (!task) {
    return (
      <div className="chat-card aui-import-card">
        <div className="chat-card-head">
          <strong>Parser task</strong>
        </div>
        {error ? (
          <div className="aui-import-error" role="alert">
            <span>{error}</span>
            <Button
              type="button"
              variant="link"
              size="xs"
              className="h-auto"
              onClick={reload}
            >
              <RotateCcw size={13} aria-hidden="true" />
              Retry
            </Button>
          </div>
        ) : (
          <p className="fine">Loading the parser&apos;s work…</p>
        )}
      </div>
    );
  }

  const state: TaskCardState = !running
    ? task.outcome === "ok"
      ? "done"
      : task.outcome === "cancelled"
        ? "cancelled"
        : "failed"
    : task.question
      ? "waiting"
      : "working";
  const label = !running
    ? task.outcome === "ok"
      ? "Parser finished"
      : task.outcome === "cancelled"
        ? "Parser cancelled"
        : "Parser failed"
    : task.question
      ? "Waiting on an answer"
      : "Reading the statements";
  const elapsed = duration((task.finished_at ?? now) - task.started_at);
  const current = task.imports.find((item) => !SETTLED.has(item.status));
  const firstOpen = task.imports.findIndex((item) => !SETTLED.has(item.status));
  const stageIndex = firstOpen === -1 ? task.imports.length : firstOpen;
  const progressItem = running ? task.imports.find((item) => item.progress) : undefined;
  const locked = task.imports.filter((item) => item.status === "needs_password");
  const reviews = task.imports.filter((item) => item.status === "review");
  const activeIndex = (() => {
    const index = task.plan.findIndex((item) => item.status !== "completed");
    return index === -1 ? task.plan.length : index;
  })();
  const statusState =
    !running ? (task.outcome === "ok" ? "done" : "failed") : task.question ? "waiting" : "working";
  const progressLabel = progressItem?.progress
    ? displayText(progressItem.progress)
    : current
      ? importLabel(current.status)
      : "Files settled";
  const files = task.imports.map((item) => ({
    name: `${displayText(item.filename ?? "Statement")} · ${importLabel(item.status)}`,
    weight: 1,
  }));

  return (
    <div className="flex w-full max-w-sm flex-col gap-2">
      <TaskCard
        label={label}
        meta="parser"
        state={state}
        elapsed={elapsed}
        result={<TaskResult task={task} />}
        actions={
          reviews.length || locked.length ? (
            <div className="flex flex-col gap-2">
              {locked.map((item) => (
                <LockedImport key={item.import_id} importId={item.import_id} onUnlocked={reload} />
              ))}
              {reviews.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {reviews.map((item) => (
                    <ImportLink key={item.import_id} item={item} />
                  ))}
                </div>
              )}
            </div>
          ) : undefined
        }
      />

      {task.plan.length > 0 && (
        <AgentPlan
          steps={task.plan.map((item) => ({
            id: item.id,
            label: displayText(item.content),
            description: item.active_form ? displayText(item.active_form) : undefined,
          }))}
          activeIndex={activeIndex}
        />
      )}

      <AgentStatus
        state={statusState}
        label={progressLabel}
        elapsed={running ? elapsed : undefined}
      />

      {files.length > 0 && (
        <JobProgress
          title="Files"
          stages={files}
          stageIndex={stageIndex}
          stageProgress={0}
          eta="working"
          elapsedMs={task.finished_at ? (task.finished_at - task.started_at) * 1000 : undefined}
          outcome={
            running
              ? undefined
              : {
                  status:
                    task.outcome === "ok"
                      ? "success"
                      : task.outcome === "cancelled"
                        ? "cancelled"
                        : "failed",
                  summary: `${reviews.length} of ${files.length} ${
                    files.length === 1 ? "file is" : "files are"
                  } ready for review.`,
                }
          }
        />
      )}

      {task.question ? (
        accountQuestion && accounts.length > 0 && task.question.question_id ? (
          <OptionList
            options={accounts
              .filter(
                (account) =>
                  account.active && ["bank", "card", "wallet"].includes(account.kind),
              )
              .map((account) => ({
                id: account.id,
                label: displayText(account.name),
                description: [account.institution, account.last4]
                  .filter(Boolean)
                  .map((value) => displayText(String(value)))
                  .join(" · "),
              }))}
            onConfirm={async (ids) => {
              await api(`/api/tasks/${encodeURIComponent(taskId)}/answer`, {
                method: "POST",
                body: JSON.stringify({
                  question_id: task.question?.question_id,
                  account_id: ids[0],
                }),
              });
              reload();
            }}
          />
        ) : (
          <div className="chat-card aui-import-card">
            <p className="aui-import-reason">{displayText(task.question.text)}</p>
            <p className="fine">Answer in chat.</p>
          </div>
        )
      ) : null}

      {error && (
        <div className="aui-import-error" role="alert">
          <span>{error}</span>
          <Button type="button" variant="link" size="xs" className="h-auto" onClick={reload}>
            <RotateCcw size={13} aria-hidden="true" />
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}
