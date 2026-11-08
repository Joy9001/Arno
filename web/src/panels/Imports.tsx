import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Skeleton } from "@radix-ui/themes";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileUp,
  MessageCircle,
  RefreshCw,
} from "lucide-react";
import { askAgent, attachAgent } from "../ask";
import { api, errorMessage } from "../api";
import { STATEMENT_ACCEPT } from "../chat/statement-attachment";
import { formatDate, formatMoment, formatPaise } from "../format";
import { Busy } from "../Loading";
import type {
  AccountSummary,
  ImportLine,
  ImportList,
  ImportRecord,
  LayoutList,
  StatementLayout,
} from "../types";
import { useApi } from "../useApi";
import "./Activity.css";

const LINE_PAGE_SIZE = 25;
const HISTORY_PAGE_SIZE = 25;

type ImportHistoryPage = ImportList & { next_cursor: string | null };
type ImportHistoryState = { cursor: string | null; page: ImportHistoryPage };

const LINE_STATE_LABEL: Record<string, string> = {
  new: "New",
  certain: "Matched",
  ambiguous: "Ambiguous",
  merged: "Merged",
  ignored: "Ignored",
};

const LINE_FILTERS = [
  { id: "all", label: "All lines" },
  { id: "decision", label: "Needs a decision" },
  { id: "new", label: "New" },
  { id: "matched", label: "Matched" },
  { id: "ignored", label: "Ignored" },
];

function StatusBadge({ status }: { status: ImportRecord["status"] }) {
  const tone =
    status === "committed"
      ? "good"
      : ["review", "parsing", "needs_password", "needs_input", "paused"].includes(status)
        ? "warn"
        : status === "failed" || status === "not_a_statement"
          ? "danger"
          : "muted";
  const label = status.replaceAll("_", " ");
  return (
    <span className={`badge ${tone}`}>
      {label.charAt(0).toUpperCase() + label.slice(1)}
    </span>
  );
}

function needsDecision(line: ImportLine): boolean {
  return (
    line.credit_decision_required ||
    line.match_state === "ambiguous" ||
    Boolean(line.suggested_transfer_account_id && !line.transfer_confirmed)
  );
}

function lineMatchesFilter(line: ImportLine, filter: string): boolean {
  if (filter === "decision") return needsDecision(line);
  if (filter === "matched") {
    return line.match_state === "certain" || line.match_state === "merged";
  }
  if (filter === "all") return true;
  return line.match_state === filter;
}

function scrollToDetail(element: HTMLElement | null) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.setTimeout(() => {
    element?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, 60);
}

function Pager({
  page,
  pageCount,
  first,
  last,
  total,
  noun,
  onPage,
  compact = false,
}: {
  page: number;
  pageCount: number;
  first: number;
  last: number;
  total: number;
  noun: string;
  onPage: (page: number) => void;
  compact?: boolean;
}) {
  return (
    <nav className={compact ? "act-pager compact" : "act-pager"} aria-label={`${noun} pages`}>
      <span className="act-pager-info">
        {total === 0 ? `0 ${noun}` : `${first}–${last} of ${total} ${noun}`} · page {page + 1} of{" "}
        {pageCount}
      </span>
      <div className="act-pager-buttons">
        <button
          type="button"
          className="ghost small"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={14} strokeWidth={1.9} aria-hidden="true" />
          Previous
        </button>
        <button
          type="button"
          className="ghost small"
          disabled={page >= pageCount - 1}
          onClick={() => onPage(page + 1)}
        >
          Next
          <ChevronRight size={14} strokeWidth={1.9} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}

function LineTable({ detail }: { detail: ImportRecord }) {
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(0);
  const lines = detail.lines.filter((line) => lineMatchesFilter(line, filter));
  const pageCount = Math.max(1, Math.ceil(lines.length / LINE_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const firstIndex = safePage * LINE_PAGE_SIZE;
  const rows = lines.slice(firstIndex, firstIndex + LINE_PAGE_SIZE);

  return (
    <section aria-labelledby="import-lines-heading">
      <div className="act-lines-head">
        <h3 id="import-lines-heading">Statement lines</h3>
        <div className="act-lines-controls">
          <label>
            Show
            <select
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value);
                setPage(0);
              }}
            >
              {LINE_FILTERS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <span className="fine">{detail.lines.length} rows in this statement</span>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="act-empty act-top-gap">
          <p>No statement lines match this filter.</p>
          <button type="button" className="ghost small" onClick={() => setFilter("all")}>
            Show all lines
          </button>
        </div>
      ) : (
        <>
          <div
            className="table-wrap act-line-scroll act-top-gap"
            role="region"
            aria-label="Statement lines, scroll horizontally for all columns"
            tabIndex={0}
          >
            <table>
              <caption className="sr-only">
                Statement lines, {firstIndex + 1} to {firstIndex + rows.length} of {lines.length}
              </caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Narration</th>
                  <th scope="col">Amount</th>
                  <th scope="col">Balance</th>
                  <th scope="col">Match</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((line) => (
                  <tr key={line.id}>
                    <td>{formatDate(line.date)}</td>
                    <td className="narration act-line-narration">{line.description}</td>
                    <td className={line.amount_paise < 0 ? "negative" : ""}>
                      {formatPaise(line.amount_paise)}
                    </td>
                    <td>{line.balance_paise === null ? "—" : formatPaise(line.balance_paise)}</td>
                    <td>
                      <span className="act-line-state">
                        <span
                          className={`badge ${
                            line.match_state === "ambiguous" ? "danger" : "muted"
                          }`}
                        >
                          {LINE_STATE_LABEL[line.match_state] ?? line.match_state}
                        </span>
                        {line.suggested_transfer_account_id && (
                          <span className="fine">
                            {line.transfer_confirmed ? "Confirmed" : "Needs confirmation"}
                          </span>
                        )}
                        {line.credit_decision_required && (
                          <span className="fine">Needs classification</span>
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            page={safePage}
            pageCount={pageCount}
            first={firstIndex + 1}
            last={firstIndex + rows.length}
            total={lines.length}
            noun="lines"
            onPage={setPage}
            compact
          />
        </>
      )}
    </section>
  );
}

function AmbiguousRows({
  detail,
  onResolved,
}: {
  detail: ImportRecord;
  onResolved: (next: ImportRecord) => void;
}) {
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ambiguous = detail.lines.filter((line) => line.match_state === "ambiguous");
  if (ambiguous.length === 0) return null;

  async function resolve(lineId: string, resolution: "new" | "ignore" | "merge") {
    setBusy(lineId);
    setError(null);
    try {
      const next = await api<ImportRecord>(
        `/api/imports/${detail.import_id}/lines/${lineId}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            resolution,
            movement_id: resolution === "merge" ? choice[lineId] : null,
          }),
        },
      );
      onResolved(next);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="subcard">
      <div className="section-head">
        <h3>Ambiguous lines</h3>
        <span className="fine">
          {ambiguous.length} {ambiguous.length === 1 ? "line" : "lines"}
        </span>
      </div>
      <p className="muted">
        A logged row may be one of these. Pick the logged movement to merge, keep the line as a
        new row, or ignore it.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {ambiguous.map((line) => (
        <div key={line.id} className="ambiguous" aria-busy={busy === line.id}>
          <div>
            <strong>{formatPaise(line.amount_paise)}</strong> on {formatDate(line.date)}
            <p className="fine">{line.description}</p>
          </div>
          <select
            aria-label={`Match for ${formatPaise(line.amount_paise)} on ${formatDate(line.date)}`}
            value={choice[line.id] ?? ""}
            disabled={busy === line.id}
            onChange={(event) =>
              setChoice((current) => ({ ...current, [line.id]: event.target.value }))
            }
          >
            <option value="">Choose a logged row</option>
            {line.candidates.map((candidate) => (
              <option key={candidate.movement_id} value={candidate.movement_id}>
                {candidate.date} · {formatPaise(candidate.amount_paise)} · {candidate.description}
              </option>
            ))}
          </select>
          <div className="row-actions">
            <button
              type="button"
              disabled={busy !== null || !choice[line.id]}
              onClick={() => void resolve(line.id, "merge")}
            >
              {busy === line.id ? (
                <>
                  <Busy />
                  Saving…
                </>
              ) : (
                "Merge"
              )}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy !== null}
              onClick={() => void resolve(line.id, "new")}
            >
              Keep as new
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy !== null}
              onClick={() => void resolve(line.id, "ignore")}
            >
              Ignore
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function CreditClassifyRows({
  detail,
  accounts,
  onResolved,
}: {
  detail: ImportRecord;
  accounts: AccountSummary["accounts"];
  onResolved: (next: ImportRecord) => void;
}) {
  const [kind, setKind] = useState<Record<string, string>>({});
  const [source, setSource] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = detail.lines.filter((line) => line.credit_decision_required);
  if (pending.length === 0) return null;
  const funding = accounts.filter(
    (account) =>
      ["bank", "cash", "wallet"].includes(account.kind) && account.id !== detail.account?.id,
  );

  async function classify(lineId: string) {
    setBusy(lineId);
    setError(null);
    try {
      const next = await api<ImportRecord>(`/api/imports/${detail.import_id}/lines/${lineId}`, {
        method: "PATCH",
        body: JSON.stringify({
          resolution: "classify_credit",
          credit_kind: kind[lineId],
          source_account_id: kind[lineId] === "transfer" ? source[lineId] : null,
        }),
      });
      onResolved(next);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="subcard">
      <div className="section-head">
        <h3>Card credits</h3>
        <span className="fine">
          {pending.length} {pending.length === 1 ? "line" : "lines"}
        </span>
      </div>
      <p className="muted">
        A credit on this card is not income by default. Say whether it is a merchant refund, a
        repayment from one of your accounts, or genuinely other income.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pending.map((line) => (
        <div key={line.id} className="ambiguous" aria-busy={busy === line.id}>
          <div>
            <strong>{formatPaise(line.amount_paise)}</strong> on {formatDate(line.date)}
            <p className="fine">{line.description}</p>
          </div>
          <div className="row-actions">
            <select
              aria-label={`What is the credit on ${formatDate(line.date)}?`}
              value={kind[line.id] ?? ""}
              disabled={busy === line.id}
              onChange={(event) =>
                setKind((current) => ({ ...current, [line.id]: event.target.value }))
              }
            >
              <option value="">Choose what this credit is</option>
              <option value="refund">Merchant refund</option>
              <option value="transfer">Card repayment from my account</option>
              <option value="income">Other income</option>
            </select>
            {kind[line.id] === "transfer" && (
              <select
                aria-label={`Account that paid the card on ${formatDate(line.date)}`}
                value={source[line.id] ?? ""}
                disabled={busy === line.id}
                onChange={(event) =>
                  setSource((current) => ({ ...current, [line.id]: event.target.value }))
                }
              >
                <option value="">Account that paid</option>
                {funding.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </select>
            )}
            {kind[line.id] === "transfer" && funding.length === 0 && (
              <span className="fine">Add a bank, cash, or wallet account first.</span>
            )}
            <button
              type="button"
              disabled={
                busy !== null ||
                !kind[line.id] ||
                (kind[line.id] === "transfer" && !source[line.id])
              }
              onClick={() => void classify(line.id)}
            >
              {busy === line.id ? (
                <>
                  <Busy />
                  Saving…
                </>
              ) : (
                "Save choice"
              )}
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function TransferRows({
  detail,
  accountNames,
  onResolved,
}: {
  detail: ImportRecord;
  accountNames: Map<string, string>;
  onResolved: (next: ImportRecord) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = detail.lines.filter(
    (line) => line.suggested_transfer_account_id && !line.transfer_confirmed,
  );
  if (pending.length === 0) return null;

  async function resolve(lineId: string, resolution: "confirm_transfer" | "reject_transfer") {
    setBusy(lineId);
    setError(null);
    try {
      const next = await api<ImportRecord>(`/api/imports/${detail.import_id}/lines/${lineId}`, {
        method: "PATCH",
        body: JSON.stringify({ resolution }),
      });
      onResolved(next);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="subcard">
      <div className="section-head">
        <h3>Payments to your own accounts</h3>
        <span className="fine">
          {pending.length} {pending.length === 1 ? "line" : "lines"}
        </span>
      </div>
      <p className="muted">
        The agent thinks these lines move money between your accounts. Confirm each one, or keep
        it as spending.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {pending.map((line) => (
        <div key={line.id} className="ambiguous" aria-busy={busy === line.id}>
          <div>
            <strong>{formatPaise(line.amount_paise)}</strong> on {formatDate(line.date)}
            <p className="fine">
              {line.description} →{" "}
              {accountNames.get(line.suggested_transfer_account_id!) ?? "another account"}
            </p>
          </div>
          <div className="row-actions">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void resolve(line.id, "confirm_transfer")}
            >
              {busy === line.id ? (
                <>
                  <Busy />
                  Saving…
                </>
              ) : (
                "Confirm transfer"
              )}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={busy !== null}
              onClick={() => void resolve(line.id, "reject_transfer")}
            >
              Not a transfer
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

function LoggedElsewhere({
  rows,
}: {
  rows: NonNullable<NonNullable<ImportRecord["review"]>["logged_not_on_statement"]>;
}) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / 10));
  const safePage = Math.min(page, pageCount - 1);
  const visible = rows.slice(safePage * 10, safePage * 10 + 10);
  return (
    <section className="subcard">
      <div className="section-head">
        <h3>Logged but not on the statement</h3>
        <span className="fine">{rows.length} rows</span>
      </div>
      <ul className="plain-list">
        {visible.map((row) => (
          <li key={row.movement_id}>
            <span>{formatDate(row.date)}</span>
            <span>{row.description}</span>
            <strong>{formatPaise(row.amount_paise)}</strong>
          </li>
        ))}
      </ul>
      {rows.length > 10 && (
        <Pager
          page={safePage}
          pageCount={pageCount}
          first={safePage * 10 + 1}
          last={safePage * 10 + visible.length}
          total={rows.length}
          noun="rows"
          onPage={setPage}
          compact
        />
      )}
    </section>
  );
}

function HistorySkeleton() {
  return (
    <div className="act-history" aria-busy="true" aria-label="Loading imports">
      {[0, 1, 2].map((row) => (
        <Skeleton key={row} className="sk act-history-skel" />
      ))}
      <span className="sr-only">Loading imports…</span>
    </div>
  );
}

function ParserLayouts() {
  const layouts = useApi<LayoutList>("/api/layouts");
  const [choice, setChoice] = useState<{ layout: StatementLayout; version: number } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function applyVersion() {
    if (!choice) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/layouts/${encodeURIComponent(choice.layout.id)}/rollback`, {
        method: "POST",
        body: JSON.stringify({ version: choice.version }),
      });
      setChoice(null);
      layouts.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="act-parsers" aria-labelledby="parser-layouts-heading">
      <div className="section-head">
        <h3 id="parser-layouts-heading">Parsers</h3>
        <span className="fine">{layouts.data?.items.length ?? 0} layouts</span>
      </div>
      <p className="muted">
        Each bank layout keeps its saved parser versions. Using an older version moves the
        active pointer; nothing is deleted.
      </p>
      {layouts.error && (
        <div className="act-error" role="alert">
          <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <strong>The parsers couldn’t load.</strong>
            <p className="fine">{layouts.error}</p>
          </div>
          <button type="button" className="ghost small" onClick={layouts.reload}>
            Retry
          </button>
        </div>
      )}
      {layouts.loading && !layouts.data && <Skeleton className="sk sk-line wide" />}
      {layouts.data?.items.length === 0 && (
        <p className="compact-empty">
          No saved parsers yet. The first committed import saves one.
        </p>
      )}
      {layouts.data && layouts.data.items.length > 0 && (
        <ul className="plain-list">
          {layouts.data.items.map((layout) => (
            <li key={layout.id}>
              <span>
                <strong>{layout.institution}</strong> · {layout.kind} ·{" "}
                {layout.variants.length} {layout.variants.length === 1 ? "variant" : "variants"}
              </span>
              <ul className="plain-list">
                {layout.versions.map((version) => (
                  <li key={version.version}>
                    <span>
                      Version {version.version} · {formatMoment(version.created_at)}
                      {version.version === layout.active_version && (
                        <span className="badge good"> Active</span>
                      )}
                    </span>
                    {version.version !== layout.active_version && (
                      <button
                        type="button"
                        className="ghost small"
                        disabled={busy}
                        onClick={() => setChoice({ layout, version: version.version })}
                      >
                        Use this version
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}

      <Dialog.Root
        open={Boolean(choice)}
        onOpenChange={(open) => {
          if (!open) setChoice(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="movement-dialog" aria-describedby={undefined}>
            <Dialog.Title>Use parser version {choice?.version}?</Dialog.Title>
            <p className="fine">
              {choice?.layout.institution} · {choice?.layout.kind}. The next statement from this
              bank reruns version {choice?.version}.
            </p>
            <div className="row-actions">
              <button type="button" disabled={busy} onClick={() => void applyVersion()}>
                {busy ? (
                  <>
                    <Busy />
                    Switching…
                  </>
                ) : (
                  "Use this version"
                )}
              </button>
              <Dialog.Close asChild>
                <button type="button" className="ghost" disabled={busy}>
                  Cancel
                </button>
              </Dialog.Close>
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}

export function ImportsPanel({
  importId,
  onImportId,
}: {
  importId?: string | null;
  onImportId?: (importId: string) => void;
}) {
  const [historyCursors, setHistoryCursors] = useState<(string | null)[]>([null]);
  const [history, setHistory] = useState<ImportHistoryState | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [historySettled, setHistorySettled] = useState<string | null>(null);
  const historyRequest = useRef(0);
  const historyCursor = historyCursors[historyCursors.length - 1];
  const historyPage = historyCursors.length - 1;
  const historyKey = `${historyCursor ?? "newest"}|${historyVersion}`;
  const historyLoading = historySettled !== historyKey;
  // Old data is only shown while refreshing the same page, never for a different one.
  const currentHistory = history?.cursor === historyCursor ? history.page : null;
  const reloadList = useCallback(() => setHistoryVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    const seq = historyRequest.current + 1;
    historyRequest.current = seq;
    const query = historyCursor ? `&cursor=${encodeURIComponent(historyCursor)}` : "";
    api<ImportHistoryPage>(`/api/import-history?limit=${HISTORY_PAGE_SIZE}${query}`)
      .then((page) => {
        if (!active || seq !== historyRequest.current) return;
        setHistory({ cursor: historyCursor, page });
        setHistoryError(null);
      })
      .catch((caught: unknown) => {
        if (!active || seq !== historyRequest.current) return;
        setHistoryError(errorMessage(caught));
      })
      .finally(() => {
        if (active && seq === historyRequest.current) setHistorySettled(historyKey);
      });
    return () => {
      active = false;
    };
  }, [historyCursor, historyKey]);

  const accounts = useApi<AccountSummary>("/api/accounts");
  const accountNames = new Map(
    (accounts.data?.accounts ?? []).map((account) => [account.id, account.name]),
  );
  const [selected, setSelected] = useState<ImportRecord | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [deepLinkError, setDeepLinkError] = useState<string | null>(null);
  const [deepLinkRetry, setDeepLinkRetry] = useState(0);
  const detailRef = useRef<HTMLElement | null>(null);
  // A poll that started before a write must not overwrite the write's newer payload.
  const writeSeq = useRef(0);
  const loadedImport = useRef<string | null>(null);

  const selectedLines = selected?.lines ?? [];
  const pendingTransferCount = selectedLines.filter(
    (line) => line.suggested_transfer_account_id && !line.transfer_confirmed,
  ).length;
  const pendingCreditCount = selectedLines.filter(
    (line) => line.credit_decision_required,
  ).length;
  const ambiguousCount =
    selected?.counts?.ambiguous ??
    selectedLines.filter((line) => line.match_state === "ambiguous").length;
  const decisions = ambiguousCount + pendingTransferCount + pendingCreditCount;
  const needsPoll = Boolean(
    selected && ["parsing", "needs_password", "needs_input"].includes(selected.status),
  );

  const refreshSelected = useCallback(async () => {
    const id = selected?.import_id;
    if (!id) return;
    const seq = writeSeq.current;
    try {
      const next = await api<ImportRecord>(`/api/imports/${id}`);
      if (writeSeq.current !== seq) return;
      setSelected(next);
      setPollError(null);
      if (next.status !== "parsing") reloadList();
    } catch (caught) {
      if (writeSeq.current !== seq) return;
      setPollError(errorMessage(caught));
    }
  }, [selected?.import_id, reloadList]);

  useEffect(() => {
    if (!needsPoll || busy !== null) return;
    const timer = window.setInterval(() => void refreshSelected(), 2500);
    return () => window.clearInterval(timer);
  }, [needsPoll, busy, refreshSelected]);

  async function openImport(importId: string) {
    writeSeq.current += 1;
    const seq = writeSeq.current;
    setOpening(importId);
    setError(null);
    setPollError(null);
    setPassword("");
    try {
      const next = await api<ImportRecord>(`/api/imports/${encodeURIComponent(importId)}`);
      if (seq !== writeSeq.current) return;
      setSelected(next);
      loadedImport.current = importId;
      setDeepLinkError(null);
      scrollToDetail(detailRef.current);
      onImportId?.(importId);
    } catch (caught) {
      if (seq === writeSeq.current) setError(errorMessage(caught));
    } finally {
      if (seq === writeSeq.current) setOpening(null);
    }
  }

  // A deep link bypasses the history page entirely: it fetches the exact import, and a
  // missing or failed id clears the detail rather than silently selecting another one.
  useEffect(() => {
    if (!importId || loadedImport.current === importId) return;
    let active = true;
    const seq = writeSeq.current + 1;
    writeSeq.current = seq;
    setOpening(importId);
    setError(null);
    setPollError(null);
    setDeepLinkError(null);
    setPassword("");
    loadedImport.current = null;
    setSelected(null);
    api<ImportRecord>(`/api/imports/${encodeURIComponent(importId)}`)
      .then((next) => {
        if (!active || seq !== writeSeq.current) return;
        loadedImport.current = importId;
        setSelected(next);
        scrollToDetail(detailRef.current);
      })
      .catch((caught: unknown) => {
        if (!active || seq !== writeSeq.current) return;
        loadedImport.current = null;
        setSelected(null);
        setDeepLinkError(errorMessage(caught));
        scrollToDetail(detailRef.current);
      })
      .finally(() => {
        if (active && seq === writeSeq.current) setOpening(null);
      });
    return () => {
      active = false;
    };
  }, [importId, deepLinkRetry]);

  async function act(path: string, body?: unknown) {
    if (!selected) return;
    writeSeq.current += 1;
    setBusy(path);
    setError(null);
    try {
      const next = await api<ImportRecord>(`/api/imports/${selected.import_id}/${path}`, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      setSelected(next);
      setPassword("");
      setPollError(null);
      reloadList();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  function applySelected(next: ImportRecord) {
    writeSeq.current += 1;
    setSelected(next);
    reloadList();
  }

  const blockers: string[] = [];
  if (ambiguousCount > 0) {
    blockers.push(
      `${ambiguousCount} ambiguous ${ambiguousCount === 1 ? "line" : "lines"}`,
    );
  }
  if (pendingTransferCount > 0) {
    blockers.push(
      `${pendingTransferCount} unconfirmed ${
        pendingTransferCount === 1 ? "transfer" : "transfers"
      }`,
    );
  }
  if (pendingCreditCount > 0) {
    blockers.push(
      `${pendingCreditCount} unclassified card ${
        pendingCreditCount === 1 ? "credit" : "credits"
      }`,
    );
  }

  return (
    <section className="panel act-imports">
      <header className="panel-head">
        <div>
          <h2>Imports</h2>
          <p className="muted">
            Bring your statements together. Review the rows and resolve uncertain matches before
            committing anything to your ledger.
          </p>
        </div>
        <button
          type="button"
          className="ghost"
          onClick={reloadList}
          disabled={historyLoading}
          aria-busy={historyLoading}
        >
          {historyLoading ? (
            <>
              <Busy />
              Refreshing…
            </>
          ) : (
            <>
              <RefreshCw size={14} strokeWidth={1.8} aria-hidden="true" />
              Refresh
            </>
          )}
        </button>
      </header>

      {error && (
        <div className="act-error" role="alert">
          <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <strong>That action didn’t finish.</strong>
            <p className="fine">{error}</p>
          </div>
          <button type="button" className="ghost small" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      <section className="subcard act-upload">
        <FileUp size={36} strokeWidth={1.6} aria-hidden="true" />
        <div className="section-head">
          <h3>Bring in a statement</h3>
          <span className="fine">PDF, spreadsheet, CSV, or text · 25 MB</span>
        </div>
        <div className="act-upload-fields">
          <label>
            Statements
            <input
              type="file"
              multiple
              accept={STATEMENT_ACCEPT}
              onChange={(event) => {
                const files = Array.from(event.target.files ?? []);
                event.target.value = "";
                if (files.length) attachAgent(files);
              }}
            />
          </label>
        </div>
        <p className="fine">
          Choosing files attaches them to a chat message, so you can add a note and send.
        </p>
      </section>

      <div className="imports-layout">
        <section className="subcard imports-history" aria-labelledby="import-history-heading">
          <div className="section-head">
            <h3 id="import-history-heading">Recent imports</h3>
            {currentHistory && (
              <span className="fine">
                Page {historyPage + 1} · {currentHistory.items.length}{" "}
                {currentHistory.items.length === 1 ? "import" : "imports"}
              </span>
            )}
          </div>
          {historyLoading && currentHistory && (
            <span className="act-busy" role="status">
              <Busy />
              Refreshing the list…
            </span>
          )}
          {historyError && (
            <div className="act-error" role="alert">
              <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
              <div>
                <strong>The import list couldn’t refresh.</strong>
                <p className="fine">{historyError}</p>
                {currentHistory && (
                  <p className="fine">The list below is the last successful read.</p>
                )}
              </div>
              <button type="button" className="ghost small" onClick={reloadList}>
                Retry
              </button>
            </div>
          )}
          {historyLoading && !currentHistory && <HistorySkeleton />}
          {!historyLoading && currentHistory?.items.length === 0 && (
            <p className="compact-empty">
              No imports yet. Upload a statement above to start a review.
            </p>
          )}
          {currentHistory && currentHistory.items.length > 0 && (
            <ul className="act-history">
              {currentHistory.items.map((item) => {
                const toReview =
                  item.status === "review" ? (item.counts?.ambiguous ?? 0) : 0;
                return (
                  <li key={item.import_id}>
                    <button
                      type="button"
                      className="act-import-item"
                      aria-current={selected?.import_id === item.import_id}
                      aria-busy={opening === item.import_id}
                      disabled={opening !== null}
                      onClick={() => void openImport(item.import_id)}
                    >
                      <span className="act-import-main">
                        <strong>
                          {item.account ?? item.filename ?? "Unassigned statement"}
                        </strong>
                        <span className="act-import-meta">
                          <span>
                            {item.period?.start
                              ? `${formatDate(item.period.start)} – ${formatDate(item.period.end)}`
                              : formatMoment(item.created_at)}
                          </span>
                          {item.filename && <span>{item.filename}</span>}
                          {toReview > 0 && (
                            <span>
                              {toReview} {toReview === 1 ? "line needs" : "lines need"} a decision
                            </span>
                          )}
                        </span>
                      </span>
                      <span className="act-import-side">
                        {opening === item.import_id ? (
                          <span className="act-busy">
                            <Busy />
                            Opening…
                          </span>
                        ) : (
                          <StatusBadge status={item.status} />
                        )}
                        {item.check && <span className="fine">{item.check}</span>}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {(historyPage > 0 || currentHistory?.next_cursor) && (
            <nav className="act-pager" aria-label="Import history pages">
              <span className="act-pager-info">Page {historyPage + 1}</span>
              <div className="act-pager-buttons">
                <button
                  type="button"
                  className="ghost small"
                  disabled={historyPage === 0 || historyLoading}
                  onClick={() => setHistoryCursors((cursors) => cursors.slice(0, -1))}
                >
                  <ChevronLeft size={14} strokeWidth={1.9} aria-hidden="true" />
                  Previous
                </button>
                <button
                  type="button"
                  className="ghost small"
                  disabled={historyLoading || !currentHistory?.next_cursor}
                  onClick={() => {
                    const next = currentHistory?.next_cursor;
                    if (next) setHistoryCursors((cursors) => [...cursors, next]);
                  }}
                >
                  Next
                  <ChevronRight size={14} strokeWidth={1.9} aria-hidden="true" />
                </button>
              </div>
            </nav>
          )}
        </section>

        <section className="subcard imports-detail" ref={detailRef} aria-busy={busy !== null}>
          {deepLinkError && !selected && (
            <div className="act-error" role="alert">
              <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
              <div>
                <strong>That import could not be opened.</strong>
                <p className="fine">{deepLinkError}</p>
                <p className="fine">
                  It may have been removed, or the link may be incomplete. No other import was
                  selected.
                </p>
              </div>
              <button
                type="button"
                className="ghost small"
                onClick={() => setDeepLinkRetry((value) => value + 1)}
              >
                <RefreshCw size={14} aria-hidden="true" />
                Retry
              </button>
            </div>
          )}
          {!selected && opening && (
            <div className="act-detail-placeholder" aria-busy="true" aria-label="Loading import">
              <Skeleton className="sk sk-heading" />
              <Skeleton className="sk sk-line wide" />
              <Skeleton className="sk sk-line short" />
              <span className="sr-only">Loading the import…</span>
            </div>
          )}
          {!selected && !opening && !deepLinkError && (
            <p className="empty-state">
              Choose an import to review it, or upload a new statement above.
            </p>
          )}
          {selected && (
            <>
              <div className="act-detail-head">
                <div>
                  <h3>
                    {selected.account?.name ?? "Unassigned"} <StatusBadge status={selected.status} />
                  </h3>
                  <p className="fine">
                    {selected.attachment?.filename ?? "statement"} ·{" "}
                    {selected.period?.start
                      ? `${formatDate(selected.period.start)} to ${formatDate(selected.period.end)}`
                      : "period unknown"}
                    {selected.check ? ` · ${selected.check}` : ""}
                  </p>
                  {selected.status === "review" && decisions > 0 && (
                    <p className="act-decision-note">
                      <span className="badge warn">
                        {decisions} {decisions === 1 ? "decision" : "decisions"} left
                      </span>
                    </p>
                  )}
                </div>
                <div className="act-detail-actions">
                  <button
                    type="button"
                    className="ask-link"
                    onClick={() => askAgent(`Explain import ${selected.import_id}.`)}
                  >
                    <MessageCircle size={13} aria-hidden="true" />
                    Ask
                  </button>
                  {selected.status === "committed" && (
                    <button
                      type="button"
                      className="ghost"
                      disabled={busy !== null}
                      onClick={() => void act("undo")}
                    >
                      {busy === "undo" ? (
                        <>
                          <Busy />
                          Undoing…
                        </>
                      ) : (
                        "Undo import"
                      )}
                    </button>
                  )}
                </div>
              </div>

              {selected.reason && <p className="notice">{selected.reason}</p>}

              {needsPoll && (
                <div className="act-poll" role="status">
                  <span className="act-busy">
                    <Busy />
                    {selected.status === "parsing"
                      ? "Reading the statement…"
                      : "The parser needs one more answer…"}
                  </span>
                  <span>This view updates automatically.</span>
                </div>
              )}
              {pollError && (
                <div className="act-error" role="alert">
                  <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
                  <div>
                    <strong>Couldn’t refresh this import.</strong>
                    <p className="fine">{pollError}</p>
                  </div>
                  <button type="button" className="ghost small" onClick={() => void refreshSelected()}>
                    Check now
                  </button>
                </div>
              )}

              {selected.status === "needs_password" && (
                <div className="password-card">
                  <h4>This file is locked</h4>
                  <p className="fine">
                    The password stays in memory for this import, is never stored, and never
                    reaches the model.
                  </p>
                  <div className="row-actions">
                    <input
                      type="password"
                      autoComplete="off"
                      placeholder="Statement password"
                      value={password}
                      disabled={busy !== null}
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <button
                      type="button"
                      disabled={!password || busy !== null}
                      onClick={() => void act("password", { password })}
                    >
                      {busy === "password" ? (
                        <>
                          <Busy />
                          Unlocking…
                        </>
                      ) : (
                        "Unlock"
                      )}
                    </button>
                  </div>
                </div>
              )}

              {selected.counts && (
                <div className="summary-strip">
                  <div className="summary-cell">
                    <span>Rows</span>
                    <strong>{selected.lines.length}</strong>
                  </div>
                  <div className="summary-cell">
                    <span>New</span>
                    <strong>{selected.counts.new}</strong>
                  </div>
                  <div className="summary-cell">
                    <span>Matched</span>
                    <strong>{selected.counts.certain}</strong>
                  </div>
                  <div className="summary-cell">
                    <span>Needs review</span>
                    <strong>{decisions}</strong>
                  </div>
                  <div className="summary-cell">
                    <span>Logged elsewhere</span>
                    <strong>{selected.counts.logged_not_on_statement}</strong>
                  </div>
                </div>
              )}

              {selected.status === "review" && decisions > 0 && (
                <section className="act-decisions" aria-labelledby="import-decisions-heading">
                  <div className="section-head">
                    <h3 id="import-decisions-heading">Needs your review</h3>
                    <span className="fine">{decisions} items</span>
                  </div>
                  <p className="fine act-decision-note">
                    Nothing reaches the ledger until every item here is resolved and you commit.
                  </p>
                  <CreditClassifyRows
                    detail={selected}
                    accounts={accounts.data?.accounts ?? []}
                    onResolved={applySelected}
                  />
                  <TransferRows
                    detail={selected}
                    accountNames={accountNames}
                    onResolved={applySelected}
                  />
                  <AmbiguousRows detail={selected} onResolved={applySelected} />
                </section>
              )}

              {selected.lines.length > 0 && (
                <LineTable key={selected.import_id} detail={selected} />
              )}

              {selected.review?.logged_not_on_statement &&
                selected.review.logged_not_on_statement.length > 0 && (
                  <LoggedElsewhere rows={selected.review.logged_not_on_statement} />
                )}

              {(selected.status === "review" || selected.status === "failed") && (
                <>
                  <div className="row-actions">
                    {selected.status === "review" && (
                      <button
                        type="button"
                        disabled={busy !== null || blockers.length > 0}
                        onClick={() => void act("commit")}
                      >
                        {busy === "commit" ? (
                          <>
                            <Busy />
                            Committing…
                          </>
                        ) : (
                          "Commit import"
                        )}
                      </button>
                    )}
                    <button
                      type="button"
                      className="ghost"
                      disabled={busy !== null}
                      onClick={() => void act("reject")}
                    >
                      {busy === "reject" ? (
                        <>
                          <Busy />
                          Rejecting…
                        </>
                      ) : (
                        "Reject import"
                      )}
                    </button>
                  </div>
                  {blockers.length > 0 && (
                    <p className="fine act-decision-note">
                      Resolve {blockers.join(" and ")} before committing.
                    </p>
                  )}
                </>
              )}

              {selected.parser_saved && (
                <p className="fine">
                  This layout’s parser is saved. Next month’s statement reruns it with no model
                  call.
                </p>
              )}
            </>
          )}
        </section>
      </div>

      <ParserLayouts />
    </section>
  );
}
