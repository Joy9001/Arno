import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  CheckCircle2,
  FileText,
  KeyRound,
  Loader2,
  RotateCcw,
  Undo2,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ImportRecord } from "../types";
import { api, errorMessage } from "../api";
import { displayText } from "./display-text";
import { formatRupees } from "./format";
import { ProjectionCard, type ProjectionPayload } from "../projection";
import { AnswerCard, ResultCard, Unavailable } from "./answer-ui";
import { ChartCard } from "./chart";
import { AnswerBoundary } from "./answer-boundary";
import { chartSchema } from "./answer-data";
import { projectionSchema } from "./projection-schema";
import { ImportTaskCard } from "./import-task-card";

function UndoButton({ undoId }: { undoId?: string | null }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  if (!undoId) return null;
  if (state === "done") return <span className="fine">Undone.</span>;
  return (
    <div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={state === "busy"}
        onClick={async () => {
          setState("busy");
          try {
            await api(`/api/undo/${undoId}`, { method: "POST" });
            setState("done");
          } catch (caught) {
            setError(errorMessage(caught));
            setState("error");
          }
        }}
      >
        <Undo2 size={14} aria-hidden="true" />
        {state === "busy" ? "Undoing…" : "Undo"}
      </Button>
      {error && <span className="error fine"> {error}</span>}
    </div>
  );
}

const IMPORT_POLL = new Set(["parsing", "needs_password", "needs_input"]);

const IMPORT_STATUS: Record<string, string> = {
  parsing: "Parsing statement…",
  needs_password: "Password required",
  needs_input: "One more detail needed",
  review: "Ready for review",
  not_a_statement: "Not a statement",
  failed: "Import failed",
  committed: "Committed",
  rejected: "Rejected",
};

function importAction(
  status: string,
  importId: string,
): { label: string; icon: typeof ArrowUpRight } | null {
  if (!importId) return null;
  if (status === "needs_password") return { label: "Enter password", icon: KeyRound };
  if (status === "needs_input") return { label: "Resolve details", icon: ArrowUpRight };
  if (status === "review") return { label: "Open review", icon: ArrowUpRight };
  if (["failed", "not_a_statement", "committed", "rejected"].includes(status)) {
    return { label: "Open import", icon: ArrowUpRight };
  }
  return null;
}

function ImportCard({ data }: { data: Record<string, unknown> }) {
  const importId = String(data.import_id ?? "");
  const [detail, setDetail] = useState<ImportRecord | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (!importId) return;
    let active = true;
    let timer: number | undefined;
    const load = async () => {
      try {
        const next = await api<ImportRecord>(
          `/api/imports/${encodeURIComponent(importId)}`,
        );
        if (!active) return;
        setDetail(next);
        setPollError(null);
        if (IMPORT_POLL.has(next.status)) {
          timer = window.setTimeout(() => void load(), 2500);
        }
      } catch (caught) {
        if (!active) return;
        setPollError(errorMessage(caught));
      }
    };
    void load();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [importId, retry]);

  const status = detail?.status ?? String(data.status ?? "parsing");
  const reason = detail?.reason
    ? displayText(detail.reason)
    : data.reason
      ? displayText(String(data.reason))
      : null;
  const visibleReason = status === "needs_password" ? null : reason;
  const filename =
    detail?.attachment?.filename
      ? displayText(detail.attachment.filename)
      : data.filename
        ? displayText(String(data.filename))
        : "Statement";
  const account = detail?.account?.name ? displayText(detail.account.name) : undefined;
  const action = importAction(status, importId);
  const ActionIcon = action?.icon;
  const undone = status === "rejected" && Boolean(detail?.undone_at);
  const label = undone ? "Undone" : (IMPORT_STATUS[status] ?? status.replaceAll("_", " "));
  const settled = ["committed", "review"].includes(status) || undone;
  const failed = ["failed", "not_a_statement"].includes(status);

  return (
    <div className="chat-card aui-import-card">
      <div className="chat-card-head">
        <strong>Statement import</strong>
        <span
          className={cn(
            "aui-import-status",
            settled && "aui-import-status-settled",
            failed && "aui-import-status-failed",
          )}
        >
          {status === "parsing" && (
            <Loader2 size={13} strokeWidth={2} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
          )}
          {status === "review" || status === "committed" ? (
            <CheckCircle2 size={13} strokeWidth={2} aria-hidden="true" />
          ) : failed ? (
            <XCircle size={13} strokeWidth={2} aria-hidden="true" />
          ) : null}
          <span aria-live="polite">{label}</span>
        </span>
      </div>
      <p className="aui-import-file">
        <FileText size={14} strokeWidth={1.8} aria-hidden="true" />
        <span>{filename}</span>
        {account ? <span className="fine"> · {account}</span> : null}
      </p>
      {visibleReason ? <p className="aui-import-reason">{visibleReason}</p> : null}
      {status === "needs_password" ? (
        <p className="fine">
          Enter the password only on the import screen. It never goes in chat or to the model.
        </p>
      ) : null}
      {pollError ? (
        <div className="aui-import-error" role="alert">
          <span>{pollError}</span>
          <Button
            type="button"
            variant="link"
            size="xs"
            className="h-auto"
            onClick={() => setRetry((value) => value + 1)}
          >
            <RotateCcw size={13} aria-hidden="true" />
            Retry
          </Button>
        </div>
      ) : null}
      {action && ActionIcon ? (
        <div className="aui-import-actions">
          <Button variant="outline" size="sm" asChild>
            <a href={`#activity/imports/${encodeURIComponent(importId)}`}>
              <ActionIcon size={14} strokeWidth={1.8} aria-hidden="true" />
              {action.label}
            </a>
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function Card({
  data,
  onRetryCapture,
  onEvidence,
}: {
  data: Record<string, unknown>;
  onRetryCapture?: (kind: string, attachmentId: string) => void;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const card = String(data.card ?? "card");
  if (card === "answer") return <AnswerCard data={data} onEvidence={onEvidence} />;
  if (card === "result") return <ResultCard data={data} onEvidence={onEvidence} />;
  if (card === "chart") {
    const parsed = chartSchema.safeParse(data);
    const fallback = <Unavailable what="This chart" />;
    return <AnswerBoundary resetKey={data} fallback={fallback}>
      {parsed.success ? <ChartCard data={parsed.data} /> : fallback}
    </AnswerBoundary>;
  }
  if (card === "projection") {
    const parsed = projectionSchema.safeParse(data);
    const fallback = <Unavailable what="This receipt" />;
    return <AnswerBoundary resetKey={data} fallback={fallback}>
      {parsed.success ? <ProjectionCard data={parsed.data} onEvidence={onEvidence} /> : fallback}
    </AnswerBoundary>;
  }

  if (card === "capture") {
    const attachmentId = String(data.attachment_id ?? "");
    const amount = Number(data.amount_paise ?? 0);
    const method = data.method ? String(data.method).toUpperCase() : "unknown";
    return (
      <div className="chat-card capture-card">
        <div className="capture-image">
          <img
            src={String(data.url ?? `/api/attachments/${attachmentId}`)}
            alt="The shared receipt or payment screenshot"
          />
        </div>
        <div className="capture-fields">
          <div className="chat-card-head">
            <strong>{displayText(String(data.title ?? "Photo logged"))}</strong>
            <span className="money">{formatRupees(amount)}</span>
          </div>
          <dl>
            <dt>Merchant</dt>
            <dd>{displayText(String(data.merchant ?? "unknown"))}</dd>
            <dt>Date</dt>
            <dd>{displayText(String(data.date ?? "unknown"))}</dd>
            <dt>Method</dt>
            <dd>{method.toLowerCase()}</dd>
            <dt>UPI ref</dt>
            <dd>{data.upi_ref ? displayText(String(data.upi_ref)) : "not shown"}</dd>
          </dl>
          <UndoButton undoId={data.undo_id as string | undefined} />
        </div>
      </div>
    );
  }

  if (card === "capture-retry") {
    const kind = String(data.kind ?? "");
    const attachmentId = String(data.attachment_id ?? "");
    const url = data.url ? String(data.url) : `/api/attachments/${attachmentId}`;
    return (
      <div className="chat-card capture-card retry">
        {kind === "photo" && (
          <div className="capture-image">
            <img src={url} alt="The photo that could not be read" />
          </div>
        )}
        <div className="capture-fields">
          <div className="chat-card-head">
            <strong>{displayText(String(data.title ?? "Capture failed"))}</strong>
          </div>
          <p>{displayText(String(data.note ?? "The file is saved. Retry it."))}</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onRetryCapture?.(kind, attachmentId)}
          >
            <RotateCcw size={14} aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  if (card === "movement" || card === "correction" || card === "undo") {
    const title = displayText(String(data.title ?? "Logged"));
    const summary = data.summary ? displayText(String(data.summary)) : "";
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{title}</strong>
          {Boolean(data.amount && typeof data.amount === "object") && (
            <span className="money">{String((data.amount as Record<string, unknown>).text ?? "")}</span>
          )}
        </div>
        {summary && <p>{summary}</p>}
        {Boolean(data.rule) && <p className="fine">Rule: {displayText(String(data.rule))}</p>}
        {typeof data.rows_changed === "number" && data.rows_changed > 0 && (
          <p className="fine">{data.rows_changed} past row(s) changed.</p>
        )}
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "import") return <ImportCard data={data} />;
  if (card === "import-task") return <ImportTaskCard taskId={String(data.task_id ?? "")} />;

  if (card === "preference") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{displayText(String(data.title ?? "Remembered"))}</strong>
        </div>
        <p>{displayText(String(data.text ?? ""))}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "setup") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{displayText(String(data.title ?? "Setup saved"))}</strong>
        </div>
        <p>{displayText(String(data.summary ?? ""))}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "query") {
    return (
      <details className="chat-card">
        <summary>{String(data.row_count ?? 0)} row(s) from read-only SQL</summary>
        <pre className="sql">{displayText(String(data.sql ?? ""))}</pre>
      </details>
    );
  }

  if (card === "amortize") {
    const emi = data.emi as { text?: string } | undefined;
    const interest = data.total_interest as { text?: string } | undefined;
    return (
      <div className="chat-card">
        <strong>Loan schedule</strong>
        <p>
          EMI {emi?.text ?? "—"} · total interest {interest?.text ?? "—"} ·{" "}
          {String(data.months ?? "—")} months
        </p>
        {Boolean(data.prepay && typeof data.prepay === "object") && (
          <p className="fine">
            Prepay saves {(data.prepay as Record<string, { text?: string }>).interest_saved?.text ??
              "—"}
          </p>
        )}
      </div>
    );
  }

  if (card === "compound") {
    const final = data.final as { text?: string } | undefined;
    return (
      <div className="chat-card">
        <strong>Growth at an assumed rate</strong>
        <p>{final?.text ?? "—"} at the assumed rate.</p>
        <p className="fine">{Array.isArray(data.assumptions) ? data.assumptions.join(" · ") : ""}</p>
      </div>
    );
  }

  return (
    <details className="chat-card">
      <summary>{displayText(card)}</summary>
      <pre>{displayText(JSON.stringify(data, null, 2))}</pre>
    </details>
  );
}

export function NumberFlags({ amounts }: { amounts: string[] }) {
  if (!amounts.length) return null;
  return (
    <p className="note">
      Not verified against a ledger row: {amounts.map(displayText).join(", ")}. Check them in the
      ledger before you rely on them.
    </p>
  );
}
