import { useState } from "react";
import { api, errorMessage } from "../../api";
import { formatMoneyValue } from "../../format";
import { displayText } from "../display-text";

export function ActionReceipt({ data }: { data: Record<string, unknown> }) {
  const [busy, setBusy] = useState(false);
  const [undone, setUndone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const undoId = typeof data.undo_id === "string" ? data.undo_id : null;
  async function undo() {
    if (!undoId || busy) return;
    setBusy(true); setError(null);
    try { await api(`/api/undo/${encodeURIComponent(undoId)}`, { method: "POST" }); setUndone(true); }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  const items = Array.isArray(data.items) ? data.items : [];
  return <section className="chat-card" aria-label="Action result">
    <strong>{displayText(typeof data.title === "string" ? data.title : "Action completed")}</strong>
    {formatMoneyValue(data.amount) && <span> · {formatMoneyValue(data.amount)}</span>}
    {typeof data.summary === "string" && <p>{displayText(data.summary)}</p>}
    {typeof data.text === "string" && <p>{displayText(data.text)}</p>}
    {typeof data.rule === "string" && <p>Rule: {displayText(data.rule)}</p>}
    {typeof data.rows_changed === "number" && <p>{data.rows_changed} rows changed.</p>}
    {items.length > 0 && <ul>{items.map((item: unknown, index: number) => {
      if (!item || typeof item !== "object") return null;
      const row = item as Record<string, unknown>;
      const name = row.payee ?? row.description ?? row.summary ?? row.kind;
      return <li key={index}>{typeof name === "string" ? displayText(name) : "Movement"} {formatMoneyValue(row.amount)}</li>;
    })}</ul>}
    {formatMoneyValue(data.total_outflow) && <p>Spent {formatMoneyValue(data.total_outflow)}</p>}
    {formatMoneyValue(data.total_inflow) && <p>Received {formatMoneyValue(data.total_inflow)}</p>}
    {undoId && (undone ? <p>Undone.</p> : <button type="button" disabled={busy} onClick={() => void undo()}>{busy ? "Undoing…" : "Undo"}</button>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
