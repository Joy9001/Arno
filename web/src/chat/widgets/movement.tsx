import { useState } from "react";
import { api, errorMessage } from "../../api";
import { formatDate, formatPaise } from "../../format";
import type { CategoryTree, Movement } from "../../types";
import { useApi } from "../../useApi";
import { displayText } from "../display-text";

export function MovementDetails({ id }: { id: string }) {
  const movement = useApi<Movement>(`/api/movements/${encodeURIComponent(id)}`);
  const categories = useApi<CategoryTree>("/api/categories");
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [undo, setUndo] = useState<string | null>(null);
  async function correct(changes: { category_id?: string; one_off?: boolean }) {
    setBusy(true); setError(null);
    try {
      const result = await api<{ undo_id: string }>(`/api/movements/${encodeURIComponent(id)}/correct`, { method: "POST", body: JSON.stringify({ ...changes, scope: "this" }) });
      setUndo(result.undo_id); movement.reload();
    } catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  async function undoChange() {
    if (!undo) return;
    setBusy(true); setError(null);
    try { await api(`/api/undo/${encodeURIComponent(undo)}`, { method: "POST" }); setUndo(null); movement.reload(); }
    catch (caught) { setError(errorMessage(caught)); } finally { setBusy(false); }
  }
  if (movement.error) return <p role="alert">{movement.error}</p>;
  if (!movement.data) return <p role="status">Loading movement…</p>;
  const item = movement.data;
  return <section aria-label="Movement details"><h4>{displayText(item.payee_name ?? item.description)}</h4>
    <p>{formatDate(item.date)} · {formatPaise(item.amount_paise)} · {displayText(item.category_name ?? "Uncategorized")}</p>
    <label>Category <select value={category} onChange={(event) => setCategory(event.target.value)}><option value="">Choose a category</option>{categories.data?.categories.filter((entry) => entry.active).map((entry) => <option key={entry.id} value={entry.id}>{displayText(entry.name)}</option>)}</select></label>
    <button type="button" disabled={!category || busy} onClick={() => void correct({ category_id: category })}>Change category</button>
    <label><input type="checkbox" checked={item.one_off} disabled={busy || movement.loading} onChange={(event) => void correct({ one_off: event.target.checked })} /> One-off (won&apos;t recur)</label>
    {undo && <button type="button" disabled={busy} onClick={() => void undoChange()}>Undo</button>}
    {categories.error && <p role="alert">Categories: {categories.error}</p>}{error && <p role="alert">{error}</p>}
  </section>;
}
