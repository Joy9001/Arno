import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  CheckCircle2,
  Code2,
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
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, errorMessage } from "../api";
import { formatRupees } from "./format";
import { formatMoment } from "../format";
import { ProjectionCard, type ProjectionPayload } from "../projection";

const COLORS = ["#0b6cf0", "#30d158", "#ff9f0a", "#ff453a", "#bf5af2", "#64d2ff"];

function unitValue(value: number, unit: string): string {
  if (unit === "paise") return formatRupees(value);
  if (unit === "percent") return `${value}%`;
  return new Intl.NumberFormat("en-IN").format(value);
}

type Point = { x: string; y: number };
type Series = { name: string; points: Point[] };

function chartRows(series: Series[]): Record<string, string | number>[] {
  const rows = new Map<string, Record<string, string | number>>();
  for (const line of series) {
    for (const point of line.points) {
      const row = rows.get(point.x) ?? { x: point.x };
      row[line.name] = point.y;
      rows.set(point.x, row);
    }
  }
  return [...rows.values()];
}

export function ChartCard({ data }: { data: Record<string, unknown> }) {
  const type = String(data.type ?? "line");
  const title = String(data.title ?? "Chart");
  const unit = String(data.unit ?? "paise");
  const series = (data.series as Series[] | undefined) ?? [];
  const asOf = data.as_of ? String(data.as_of) : null;
  const computedAt = data.computed_at ? String(data.computed_at) : null;
  const script = typeof data.script === "string" ? data.script : null;
  const rows = chartRows(series);
  const tick = (value: number) => unitValue(value, unit);

  return (
    <figure className="chat-card chart-card">
      <figcaption>
        <strong>{title}</strong>
        <span className="fine">
          {computedAt ? `Calculated ${formatMoment(computedAt)}` : ""}
          {computedAt && asOf ? " · " : ""}
          {asOf ? `as of ${asOf}` : ""}
        </span>
      </figcaption>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={240}>
          {type === "donut" ? (
            <PieChart>
              <Pie
                data={series[0]?.points.map((point) => ({ name: point.x, value: point.y })) ?? []}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
                isAnimationActive={false}
              >
                {(series[0]?.points ?? []).map((point, index) => (
                  <Cell key={point.x} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value) => tick(Number(value))} />
              <Legend />
            </PieChart>
          ) : type === "bar" || type === "stacked_bar" ? (
            <BarChart data={rows}>
              <CartesianGrid stroke="#2a2a2c" vertical={false} />
              <XAxis dataKey="x" stroke="#8b8b90" fontSize={12} />
              <YAxis stroke="#8b8b90" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Bar
                  key={line.name}
                  dataKey={line.name}
                  stackId={type === "stacked_bar" ? "stack" : undefined}
                  fill={COLORS[index % COLORS.length]}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          ) : (
            <LineChart data={rows}>
              <CartesianGrid stroke="#2a2a2c" vertical={false} />
              <XAxis dataKey="x" stroke="#8b8b90" fontSize={12} />
              <YAxis stroke="#8b8b90" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Line
                  key={line.name}
                  type="monotone"
                  dataKey={line.name}
                  stroke={COLORS[index % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      <details>
        <summary className="fine">
          <Code2 size={14} strokeWidth={1.8} aria-hidden="true" />{" "}
          {script ? "Script behind this chart" : "Source unavailable"}
        </summary>
        {script ? (
          <pre className="sql">{script}</pre>
        ) : (
          <p className="fine">
            The script that calculated this chart was not saved for this reply.
          </p>
        )}
      </details>
    </figure>
  );
}

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
  const reason = detail?.reason ?? (data.reason ? String(data.reason) : null);
  const visibleReason = status === "needs_password" ? null : reason;
  const filename =
    detail?.attachment?.filename ??
    (data.filename ? String(data.filename) : null) ??
    "Statement";
  const account = detail?.account?.name;
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
  if (card === "chart") return <ChartCard data={data} />;
  if (card === "projection") return <ProjectionCard data={data} onEvidence={onEvidence} />;

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
            <strong>{String(data.title ?? "Photo logged")}</strong>
            <span className="money">{formatRupees(amount)}</span>
          </div>
          <dl>
            <dt>Merchant</dt>
            <dd>{String(data.merchant ?? "unknown")}</dd>
            <dt>Date</dt>
            <dd>{String(data.date ?? "unknown")}</dd>
            <dt>Method</dt>
            <dd>{method.toLowerCase()}</dd>
            <dt>UPI ref</dt>
            <dd>{data.upi_ref ? String(data.upi_ref) : "not shown"}</dd>
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
            <strong>{String(data.title ?? "Capture failed")}</strong>
          </div>
          <p>{String(data.note ?? "The file is saved. Retry it.")}</p>
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
    const title = String(data.title ?? "Logged");
    const summary = data.summary ? String(data.summary) : "";
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{title}</strong>
          {Boolean(data.amount && typeof data.amount === "object") && (
            <span className="money">{String((data.amount as Record<string, unknown>).text ?? "")}</span>
          )}
        </div>
        {summary && <p>{summary}</p>}
        {Boolean(data.rule) && <p className="fine">Rule: {String(data.rule)}</p>}
        {typeof data.rows_changed === "number" && data.rows_changed > 0 && (
          <p className="fine">{data.rows_changed} past row(s) changed.</p>
        )}
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "import") return <ImportCard data={data} />;

  if (card === "preference") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{String(data.title ?? "Remembered")}</strong>
        </div>
        <p>{String(data.text ?? "")}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "setup") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{String(data.title ?? "Setup saved")}</strong>
        </div>
        <p>{String(data.summary ?? "")}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "query") {
    return (
      <details className="chat-card">
        <summary>{String(data.row_count ?? 0)} row(s) from read-only SQL</summary>
        <pre className="sql">{String(data.sql ?? "")}</pre>
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
      <summary>{card}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}

export function NumberFlags({ amounts }: { amounts: string[] }) {
  if (!amounts.length) return null;
  return (
    <p className="note">
      Not verified against a ledger row: {amounts.join(", ")}. Check them in the ledger before you
      rely on them.
    </p>
  );
}
