import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "../api";
import { formatDate, formatMoment, formatPaise } from "../format";
import type { AccountSummary, ImportList, ImportRecord } from "../types";
import { useApi } from "../useApi";

function StatusBadge({ status }: { status: ImportRecord["status"] }) {
  const tone =
    status === "committed" || status === "review"
      ? "good"
      : status === "failed" || status === "not_a_statement" || status === "rejected"
        ? "danger"
        : "muted";
  return <span className={`badge ${tone}`}>{status.replaceAll("_", " ")}</span>;
}

function LineTable({ detail }: { detail: ImportRecord }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Narration</th>
            <th>Amount</th>
            <th>Balance</th>
            <th>Category</th>
            <th>Match</th>
          </tr>
        </thead>
        <tbody>
          {detail.lines.map((line) => (
            <tr key={line.id}>
              <td>{formatDate(line.date)}</td>
              <td className="narration">{line.description}</td>
              <td className={line.amount_paise < 0 ? "negative" : ""}>
                {formatPaise(line.amount_paise)}
              </td>
              <td>{line.balance_paise === null ? "—" : formatPaise(line.balance_paise)}</td>
              <td>{line.suggested_category?.category_name ?? "—"}</td>
              <td>
                <span className={`badge ${line.match_state === "ambiguous" ? "danger" : "muted"}`}>
                  {line.match_state}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
      <h3>Ambiguous lines</h3>
      <p className="muted">
        A logged row may be one of these. Pick the logged movement to merge, keep the line as a
        new row, or ignore it.
      </p>
      {error && <p className="error">{error}</p>}
      {ambiguous.map((line) => (
        <div key={line.id} className="ambiguous">
          <div>
            <strong>{formatPaise(line.amount_paise)}</strong> on {formatDate(line.date)}
            <p className="fine">{line.description}</p>
          </div>
          <select
            value={choice[line.id] ?? ""}
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
              disabled={busy === line.id || !choice[line.id]}
              onClick={() => void resolve(line.id, "merge")}
            >
              Merge
            </button>
            <button type="button" className="ghost" onClick={() => void resolve(line.id, "new")}>
              Keep as new
            </button>
            <button type="button" className="ghost" onClick={() => void resolve(line.id, "ignore")}>
              Ignore
            </button>
          </div>
        </div>
      ))}
    </section>
  );
}

export function ImportsPanel() {
  const list = useApi<ImportList>("/api/imports");
  const reloadList = list.reload;
  const accounts = useApi<AccountSummary>("/api/accounts");
  const [selected, setSelected] = useState<ImportRecord | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [password, setPassword] = useState("");
  const [accountChoice, setAccountChoice] = useState("");
  const [openingRupees, setOpeningRupees] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectedId = useRef<string | null>(null);

  useEffect(() => {
    selectedId.current = selected?.import_id ?? null;
  }, [selected]);

  useEffect(() => {
    const id = selected?.import_id;
    if (!id || !["parsing", "needs_password", "needs_input"].includes(selected.status)) {
      return;
    }
    let active = true;
    const timer = window.setInterval(() => {
      api<ImportRecord>(`/api/imports/${id}`)
        .then((next) => {
          if (!active) return;
          setSelected(next);
          if (next.status !== "parsing") {
            reloadList();
          }
        })
        .catch(() => undefined);
    }, 2500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selected?.import_id, selected?.status, reloadList]);

  async function openImport(importId: string) {
    setError(null);
    try {
      setSelected(await api<ImportRecord>(`/api/imports/${importId}`));
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const attachment = await api<{ id: string }>("/api/attachments", {
        method: "POST",
        body: form,
      });
      const started = await api<ImportRecord>("/api/imports", {
        method: "POST",
        body: JSON.stringify({
          attachment_id: attachment.id,
          account_id: accountId || null,
        }),
      });
      setSelected(started);
      setFile(null);
      list.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function act(path: string, body?: unknown) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api<ImportRecord>(`/api/imports/${selected.import_id}/${path}`, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      setSelected(next);
      setPassword("");
      list.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitPassword() {
    await act("password", { password });
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Imports</p>
          <h2>Statements in, rows out</h2>
          <p className="muted">
            Upload a statement. A known layout reruns its saved parser with no model call. A new
            layout starts a parse run, the checks gate it, and nothing reaches the ledger until
            you commit.
          </p>
        </div>
        <button type="button" className="ghost" onClick={list.reload}>
          Refresh
        </button>
      </header>

      {error && <p className="error">{error}</p>}

      <section className="subcard">
        <h3>Upload a statement</h3>
        <div className="filters">
          <label>
            File
            <input
              type="file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              accept=".pdf,.xlsx,.xls,.xlsb,.ods,.csv,.tsv,.txt,.docx,.rtf,.ofx,.qfx,.xml,.json,.zip"
            />
          </label>
          <label>
            Account (optional)
            <select value={accountId} onChange={(event) => setAccountId(event.target.value)}>
              <option value="">Infer from the file</option>
              {(accounts.data?.accounts ?? [])
                .filter((account) => ["bank", "card", "wallet"].includes(account.kind))
                .map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
            </select>
          </label>
          <button type="button" disabled={!file || busy} onClick={() => void upload()}>
            Upload and start
          </button>
        </div>
        <p className="fine">Up to 25 MB. Files stay in the database only until commit or reject.</p>
      </section>

      <div className="imports-layout">
        <section className="subcard imports-history">
          <h3>History</h3>
          {list.loading && !list.data && <p className="muted">Loading…</p>}
          {list.data?.items.length === 0 && <p className="muted">No imports yet.</p>}
          <ul className="plain-list">
            {list.data?.items.map((item) => (
              <li key={item.import_id}>
                <button
                  type="button"
                  className={selected?.import_id === item.import_id ? "import-item active" : "import-item"}
                  onClick={() => void openImport(item.import_id)}
                >
                  <span>
                    <strong>{item.account ?? "Unassigned"}</strong>
                    <span className="fine">
                      {item.period?.start
                        ? `${item.period.start} to ${item.period.end}`
                        : formatMoment(item.created_at)}
                    </span>
                  </span>
                  <StatusBadge status={item.status} />
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="subcard imports-detail">
          {!selected && <p className="muted">Choose an import to review it.</p>}
          {selected && (
            <>
              <div className="account-head">
                <div>
                  <h3>
                    {selected.account?.name ?? "Unassigned"}{" "}
                    <StatusBadge status={selected.status} />
                  </h3>
                  <p className="fine">
                    {selected.attachment?.filename ?? "statement"} ·{" "}
                    {selected.period?.start
                      ? `${formatDate(selected.period.start)} to ${formatDate(selected.period.end)}`
                      : "period unknown"}
                    {selected.check ? ` · ${selected.check}` : ""}
                  </p>
                </div>
                {selected.status === "committed" && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy}
                    onClick={() => void act("undo")}
                  >
                    Undo import
                  </button>
                )}
              </div>

              {selected.reason && <p className="notice">{selected.reason}</p>}

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
                      onChange={(event) => setPassword(event.target.value)}
                    />
                    <button
                      type="button"
                      disabled={!password || busy}
                      onClick={() => void submitPassword()}
                    >
                      Unlock
                    </button>
                  </div>
                </div>
              )}

              {selected.status === "needs_input" && (
                <div className="password-card">
                  <h4>One more answer</h4>
                  <p className="fine">{selected.reason ?? "Which account is this statement for?"}</p>
                  <div className="row-actions">
                    <select
                      value={accountChoice}
                      onChange={(event) => setAccountChoice(event.target.value)}
                    >
                      <option value="">Choose an account</option>
                      {(accounts.data?.accounts ?? [])
                        .filter((account) => ["bank", "card", "wallet"].includes(account.kind))
                        .map((account) => (
                          <option key={account.id} value={account.id}>
                            {account.name}
                          </option>
                        ))}
                    </select>
                    <button
                      type="button"
                      disabled={!accountChoice || busy}
                      onClick={() => void act("account", { account_id: accountChoice })}
                    >
                      Use this account
                    </button>
                  </div>
                  {selected.reason?.toLowerCase().includes("opening") && (
                    <div className="row-actions">
                      <input
                        type="number"
                        step="0.01"
                        placeholder="Opening balance in rupees"
                        value={openingRupees}
                        onChange={(event) => setOpeningRupees(event.target.value)}
                      />
                      <button
                        type="button"
                        disabled={!openingRupees || busy}
                        onClick={() =>
                          void act("opening", {
                            opening_paise: Math.round(Number(openingRupees) * 100),
                          })
                        }
                      >
                        Use this opening
                      </button>
                    </div>
                  )}
                </div>
              )}

              {selected.counts && (
                <div className="tiles">
                  <div className="tile">
                    <span>New</span>
                    <strong>{selected.counts.new}</strong>
                  </div>
                  <div className="tile">
                    <span>Merged</span>
                    <strong>{selected.counts.certain}</strong>
                  </div>
                  <div className="tile">
                    <span>Ambiguous</span>
                    <strong>{selected.counts.ambiguous}</strong>
                  </div>
                  <div className="tile">
                    <span>Logged elsewhere</span>
                    <strong>{selected.counts.logged_not_on_statement}</strong>
                  </div>
                </div>
              )}

              {selected.lines.length > 0 && <LineTable detail={selected} />}

              {selected.status === "review" && (
                <AmbiguousRows detail={selected} onResolved={setSelected} />
              )}

              {selected.review?.logged_not_on_statement &&
                selected.review.logged_not_on_statement.length > 0 && (
                  <section className="subcard">
                    <h3>Logged but not on the statement</h3>
                    <ul className="plain-list">
                      {selected.review.logged_not_on_statement.map((row) => (
                        <li key={row.movement_id}>
                          <span>{formatDate(row.date)}</span>
                          <span>{row.description}</span>
                          <strong>{formatPaise(row.amount_paise)}</strong>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

              {(selected.status === "review" || selected.status === "failed") && (
                <div className="row-actions">
                  {selected.status === "review" && (
                    <button
                      type="button"
                      disabled={busy || (selected.counts?.ambiguous ?? 0) > 0}
                      onClick={() => void act("commit")}
                    >
                      Commit import
                    </button>
                  )}
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy}
                    onClick={() => void act("reject")}
                  >
                    Reject
                  </button>
                </div>
              )}

              {selected.status === "review" && selected.counts?.ambiguous ? (
                <p className="fine">Resolve every ambiguous line before committing.</p>
              ) : null}

              {selected.parser_saved && (
                <p className="fine">
                  This layout's parser is saved. Next month's statement reruns it with no model call.
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </section>
  );
}
