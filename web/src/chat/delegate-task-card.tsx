"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, KeyRound, RotateCcw } from "lucide-react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "../api";
import { askAgent } from "../ask";
import { openRun } from "../evidence";
import { Orb } from "../Orb";
import { CONTINUE_PROMPT, runEnding, useRun } from "../run/runData";
import type { AccountSummary, ImportTask, ImportTaskImport } from "../types";
import { displayText } from "./display-text";
import { ChatToolFallback } from "./parts";

const RECEIPT = /^Task ([0-9a-f]{32}) is running in the background\./;
const ACCOUNT_QUESTION = "Which account is this statement for?";
const ACCOUNT_KINDS = new Set(["bank", "card", "wallet"]);
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

function ReviewLinks({ task }: { task: ImportTask }) {
  const ready = task.imports.filter((item) => item.status === "review");
  if (!ready.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {ready.map((item) => (
        <ImportLink key={item.import_id} item={item} />
      ))}
    </div>
  );
}

function RunCardBody({ task, taskId, reload }: { task: ImportTask; taskId: string; reload: () => void }) {
  const ending = runEnding(task);
  const stage = task.run.stages.findIndex((item) => item.status === "running");
  const pct = task.state === "running" ? Math.max(6, ((stage + 1) / task.run.stages.length) * 100) : 100;
  const glyph =
    ending.tone === "live" ? (
      <Orb size={38} live thinking />
    ) : (
      <span className="run-card-glyph" aria-hidden="true">
        {ending.tone === "done" ? "✓" : "!"}
      </span>
    );
  const waiting =
    Boolean(task.question) || task.imports.some((item) => item.status === "needs_password");
  return (
    <div className={`run-card ${ending.tone}`}>
      <div className="run-card-row">
        {glyph}
        <div className="run-card-body">
          <b>Reading your statement</b>
          <span>{displayText(ending.tone === "failed" ? ending.body : ending.line)}</span>
          {ending.tone === "live" && (
            <div className="run-card-bar" aria-hidden="true">
              <i style={{ width: `${pct}%` }} />
            </div>
          )}
        </div>
        <div className="run-card-actions">
          {ending.tone === "paused" && (
            <button
              type="button"
              className="run-primary"
              onClick={() => askAgent(CONTINUE_PROMPT, { send: true })}
            >
              Continue
            </button>
          )}
          <button type="button" className="pill-button" onClick={() => openRun(taskId)}>
            {ending.tone === "live" ? "Watch" : "Open run"}
          </button>
        </div>
      </div>
      {(waiting || task.imports.some((item) => item.status === "review")) && (
        <div className="run-card-extra">
          {waiting && <TaskActions task={task} taskId={taskId} reload={reload} />}
          <ReviewLinks task={task} />
        </div>
      )}
    </div>
  );
}

function ImportTaskCard({ taskId }: { taskId: string }) {
  const { task, error, reload } = useRun(taskId);
  if (task) return <RunCardBody task={task} taskId={taskId} reload={reload} />;
  return (
    <div className="run-card">
      {error ? (
        <div className="aui-import-error" role="alert">
          <span>{error}</span>
          <Button type="button" variant="link" size="xs" className="h-auto" onClick={reload}>
            <RotateCcw size={13} aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : (
        <div className="run-card-row">
          <Orb size={38} live thinking />
          <div className="run-card-body">
            <b>Reading your statement</b>
            <span>Starting…</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** A background `delegate_task`: its state and transcript come from the task API. */
export function DelegateTaskCard(props: ToolCallMessagePartProps) {
  const receipt =
    typeof props.result === "string" ? RECEIPT.exec(props.result) : null;
  if (receipt === null) return <ChatToolFallback {...props} />;
  return <ImportTaskCard taskId={receipt[1]!} />;
}
