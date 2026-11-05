import { AlertCircle, Ban, Check, Clock, Loader2, ShieldCheck, X } from "lucide-react";
import { getToolName } from "ai";
import type { DynamicToolUIPart, ToolUIPart } from "ai";

type ToolPartData = ToolUIPart | DynamicToolUIPart;

function asText(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2) ?? String(value);
}

function Bounded({ label, value }: { label: string; value: unknown }) {
  const text = asText(value);
  if (!text) return null;
  return (
    <div className="tool-section">
      <span className="tool-section-label">{label}</span>
      <pre className="sql tool-code">
        {text}
      </pre>
    </div>
  );
}

function ConfirmFacts({ input }: { input: unknown }) {
  if (!input || typeof input !== "object") return <p className="fine">{String(input)}</p>;
  const entries = Object.entries(input as Record<string, unknown>);
  if (!entries.length) return null;
  return (
    <dl className="confirm-facts">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt>
          <dd>
            {typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function StatusIcon({ part }: { part: ToolPartData }) {
  switch (part.state) {
    case "output-available":
      return <Check size={13} strokeWidth={2} aria-hidden="true" />;
    case "output-error":
      return <AlertCircle size={13} strokeWidth={2} aria-hidden="true" />;
    case "output-denied":
      return <Ban size={13} strokeWidth={2} aria-hidden="true" />;
    case "approval-requested":
      return part.approval.isAutomatic ? (
        <ShieldCheck size={13} strokeWidth={2} aria-hidden="true" />
      ) : (
        <Clock size={13} strokeWidth={2} aria-hidden="true" />
      );
    case "approval-responded":
      return part.approval.approved ? (
        <Check size={13} strokeWidth={2} aria-hidden="true" />
      ) : (
        <Ban size={13} strokeWidth={2} aria-hidden="true" />
      );
    case "input-available":
      return <Loader2 size={13} strokeWidth={2} className="tool-spin" aria-hidden="true" />;
    default:
      return <Clock size={13} strokeWidth={2} aria-hidden="true" />;
  }
}

function statusLabel(part: ToolPartData): string {
  switch (part.state) {
    case "input-streaming":
      return "pending";
    case "input-available":
      return "running";
    case "approval-requested":
      return part.approval.isAutomatic ? "approved automatically" : "needs your approval";
    case "approval-responded":
      return part.approval.approved ? "approved — running" : "denied";
    case "output-available":
      return part.preliminary ? "partial result" : "done";
    case "output-error":
      return "failed";
    case "output-denied":
      return "denied";
  }
}

export function ToolPart({
  part,
  busy,
  onApprove,
}: {
  part: ToolPartData;
  busy: boolean;
  onApprove: (approvalId: string, approved: boolean) => void;
}) {
  const toolName = getToolName(part);

  if (part.state === "approval-requested") {
    const automatic = part.approval.isAutomatic === true;
    return (
      <div className="chat-card confirm">
        <strong>
          {automatic ? "Approved automatically" : "Confirm"}: {toolName}
        </strong>
        <p>
          {automatic
            ? "Policy allows this tool, so it runs without asking you."
            : (part.approval.requestReason ?? "This change lasts. Confirm it?")}
        </p>
        <ConfirmFacts input={part.input} />
        {!automatic && (
          <div className="button-row">
            <button
              type="button"
              onClick={() => onApprove(part.approval.id, true)}
              disabled={busy}
            >
              <Check size={14} aria-hidden="true" />
              Confirm
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => onApprove(part.approval.id, false)}
              disabled={busy}
            >
              <X size={14} aria-hidden="true" />
              Cancel
            </button>
          </div>
        )}
      </div>
    );
  }

  const approval = part.approval;
  return (
    <>
      <details className="tool-line">
        <summary>
          <StatusIcon part={part} />
          <span>
            {toolName} · {statusLabel(part)}
          </span>
        </summary>
        <div className="tool-body">
          {approval && (
            <p className={`fine ${approval.approved ? "" : "error"}`}>
              {approval.approved
                ? approval.isAutomatic
                  ? "Approved automatically."
                  : "Approved by you."
                : `Denied${approval.reason ? `: ${approval.reason}` : "."}`}
            </p>
          )}
          <Bounded label="Input" value={part.input} />
          {part.state === "output-available" && <Bounded label="Output" value={part.output} />}
          {part.state === "output-denied" && <p className="fine">This tool did not run.</p>}
        </div>
      </details>
      {part.state === "output-error" && (
        <p className="error fine tool-error">{part.errorText}</p>
      )}
    </>
  );
}
