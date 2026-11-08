"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, KeyRound, RotateCcw } from "lucide-react";
import type {
  ThreadAssistantMessagePart,
  ThreadMessage,
  ThreadUserMessagePart,
  ToolCallMessagePartProps,
  ToolCallMessagePartStatus,
} from "@assistant-ui/react";
import { Button } from "@/components/ui/button";
import { TaskCard, type TaskPart } from "@/components/assistant-ui/elements/task-card.aui";
import { api, errorMessage } from "../api";
import type {
  AccountSummary,
  ImportTask,
  ImportTaskImport,
  ImportTaskMessage,
} from "../types";
import { displayText } from "./display-text";
import { ChatToolFallback } from "./parts";

const RECEIPT = /^Task ([0-9a-f]{32}) is running in the background\./;
const ACCOUNT_QUESTION = "Which account is this statement for?";
const STOPPED_NOTE = "The parser stopped before it finished. Ask me to continue.";
const ACCOUNT_KINDS = new Set(["bank", "card", "wallet"]);
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

/** Host-made: the arguments the bound card reads its label and meta from. */
const CARD_ARGS = { description: "Reading your statement", agent: "parser" };

function importLabel(status: string, stopped: boolean): string {
  if (stopped) return "Stopped";
  return IMPORT_LABEL[status] ?? status.replaceAll("_", " ");
}

function taskStatus(task: ImportTask): ToolCallMessagePartStatus {
  if (task.state === "running") {
    return task.question
      ? { type: "requires-action", reason: "interrupt" }
      : { type: "running" };
  }
  // A run that crashed after its imports reached review still did its work.
  const done =
    task.imports.length > 0 &&
    task.imports.every((item) => item.status === "review" || item.status === "committed");
  if (task.outcome === "ok" || done) return { type: "complete" };
  if (task.outcome === "cancelled") return { type: "incomplete", reason: "cancelled" };
  return { type: "incomplete", reason: "error" };
}

function threadMessage(message: ImportTaskMessage): ThreadMessage {
  const createdAt = new Date(message.createdAt);
  if (message.role === "user") {
    return {
      id: message.id,
      role: "user",
      createdAt,
      content: message.content as ThreadUserMessagePart[],
      attachments: [],
      metadata: { custom: {} },
    };
  }
  return {
    id: message.id,
    role: "assistant",
    createdAt,
    status: { type: "complete", reason: "stop" },
    content: message.content as ThreadAssistantMessagePart[],
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: {},
    },
  };
}

function useImportTask(taskId: string) {
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

  return { task, error, reload };
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

function AccountChoices({
  task,
  taskId,
  onAnswered,
}: {
  task: ImportTask;
  taskId: string;
  onAnswered: () => void;
}) {
  const [accounts, setAccounts] = useState<AccountSummary["accounts"] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api<AccountSummary>("/api/accounts")
      .then((summary) => {
        if (active) setAccounts(summary.accounts.filter((account) => account.active));
      })
      .catch(() => {
        if (active) setAccounts([]);
      });
    return () => {
      active = false;
    };
  }, []);

  const choices = (accounts ?? []).filter((account) =>
    ACCOUNT_KINDS.has(account.kind),
  );
  if (accounts === null) return null;
  if (choices.length === 0) {
    return (
      <p className="fine">
        No bank, card, or wallet account to choose. Add one, then answer in chat.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="fine">{displayText(task.question?.text ?? ACCOUNT_QUESTION)}</span>
      <div className="flex flex-wrap gap-2">
        {choices.map((account) => (
          <Button
            key={account.id}
            type="button"
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={async () => {
              if (!task.question?.question_id) return;
              setBusy(account.id);
              setError(null);
              try {
                await api(`/api/tasks/${encodeURIComponent(taskId)}/answer`, {
                  method: "POST",
                  body: JSON.stringify({
                    question_id: task.question.question_id,
                    account_id: account.id,
                  }),
                });
                onAnswered();
              } catch (caught) {
                setError(errorMessage(caught));
              } finally {
                setBusy(null);
              }
            }}
          >
            {displayText(account.name)}
            {account.institution || account.last4 ? (
              <span className="fine">
                {[account.institution, account.last4]
                  .filter(Boolean)
                  .map((value) => displayText(String(value)))
                  .join(" · ")}
              </span>
            ) : null}
          </Button>
        ))}
      </div>
      {error && (
        <span className="error fine" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

function TaskActions({
  task,
  taskId,
  reload,
}: {
  task: ImportTask;
  taskId: string;
  reload: () => void;
}) {
  const locked = task.imports.filter((item) => item.status === "needs_password");
  const accountQuestion = Boolean(
    task.state === "running" &&
      task.question?.question_id &&
      task.imports.some(
        (item) =>
          item.status === "needs_input" &&
          (item.reason ?? "").includes(ACCOUNT_QUESTION),
      ),
  );

  if (locked.length === 0 && !task.question) return undefined;

  return (
    <div className="flex flex-col gap-2">
      {locked.map((item) => (
        <LockedImport key={item.import_id} importId={item.import_id} onUnlocked={reload} />
      ))}
      {task.question ? (
        accountQuestion ? (
          <AccountChoices task={task} taskId={taskId} onAnswered={reload} />
        ) : (
          <div className="flex flex-col gap-0.5">
            <p>{displayText(task.question.text)}</p>
            <p className="fine">Answer in chat.</p>
          </div>
        )
      ) : null}
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
  if (task.state === "running") return undefined;
  const stopped = task.imports.some((item) => !SETTLED.has(item.status));
  if (task.imports.length === 0 && !stopped) return undefined;
  return (
    <div className="flex flex-col gap-1.5">
      {task.imports.map((item) => (
        <div key={item.import_id} className="flex items-center justify-between gap-2">
          <span className="min-w-0 truncate">
            {displayText(item.filename ?? "Statement")} ·{" "}
            {importLabel(item.status, !SETTLED.has(item.status))}
          </span>
          {item.status === "review" && <ImportLink item={item} />}
        </div>
      ))}
      {stopped && <span>{STOPPED_NOTE}</span>}
    </div>
  );
}

function ImportTaskCard({ taskId, toolCallId, toolName, argsText }: {
  taskId: string;
  toolCallId: string;
  toolName: string;
  argsText: string;
}) {
  const { task, error, reload } = useImportTask(taskId);
  const part: TaskPart = {
    type: "tool-call",
    toolCallId,
    toolName,
    args: CARD_ARGS,
    argsText,
    status: task
      ? taskStatus(task)
      : error
        ? { type: "incomplete", reason: "error" }
        : { type: "running" },
    messages: task ? task.messages.map(threadMessage) : [],
    ...(task && {
      timing: {
        startedAt: task.started_at * 1000,
        ...(task.finished_at !== null && { completedAt: task.finished_at * 1000 }),
      },
    }),
  };
  const waiting = task ? <TaskActions task={task} taskId={taskId} reload={reload} /> : undefined;

  return (
    <TaskCard
      part={part}
      actions={
        waiting || error ? (
          <div className="flex flex-col gap-2">
            {waiting}
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
        ) : undefined
      }
      result={
        task ? (
          <TaskResult task={task} />
        ) : error ? (
          error
        ) : undefined
      }
    />
  );
}

/** A background `delegate_task`: its state and transcript come from the task API. */
export function DelegateTaskCard(props: ToolCallMessagePartProps) {
  const receipt =
    typeof props.result === "string" ? RECEIPT.exec(props.result) : null;
  if (receipt === null) return <ChatToolFallback {...props} />;
  return (
    <ImportTaskCard
      taskId={receipt[1]!}
      toolCallId={props.toolCallId}
      toolName={props.toolName}
      argsText={JSON.stringify(CARD_ARGS)}
    />
  );
}
