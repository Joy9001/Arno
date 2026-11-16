import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { api, errorMessage } from "../api";
import { formatMoment } from "../format";
import { useApi } from "../useApi";

type HistoryEntry = {
  undo_id: string;
  created_at: string;
  actor: string;
  message_id: string | null;
  conversation_id: string | null;
  summary: string;
  records: { table: string; id: string | null }[];
  undoable: boolean;
  reason: string | null;
};
type HistoryPage = { items: HistoryEntry[]; next_cursor: string | null };

const ACTORS: Record<string, string> = { user: "You", agent: "The agent", system: "System" };

/** One write group per row: what changed, who changed it, and the undo result as it stands. */
export function HistoryEntries({ items, onUndo, busy }: {
  items: HistoryEntry[]; onUndo: (id: string) => void; busy: boolean;
}) {
  return (
    <div className="stg-list">
      {items.map((entry) => (
        <div className="stg-list-row" key={entry.undo_id}>
          <div className="stg-list-body">
            <p className="stg-question">{entry.summary}</p>
            <p className="stg-list-meta">
              {formatMoment(entry.created_at)} · {ACTORS[entry.actor] ?? entry.actor}
              {entry.conversation_id && ` · conversation ${entry.conversation_id}`}
            </p>
            <p className="stg-list-meta">
              {entry.records
                .map((record) => `${record.table}: ${record.id ?? "unknown"}`)
                .join(", ")}
            </p>
            {!entry.undoable && entry.reason && <p className="stg-hint">{entry.reason}</p>}
          </div>
          {entry.undoable && (
            <div className="stg-list-actions">
              <button
                type="button"
                className="ghost small"
                disabled={busy}
                onClick={() => onUndo(entry.undo_id)}
              >
                <RotateCcw size={14} strokeWidth={1.8} aria-hidden="true" />
                Undo
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export function HistoryPanel() {
  const [older, setOlder] = useState<HistoryEntry[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const history = useApi<HistoryPage>("/api/history?limit=25");

  const items = [...(history.data?.items ?? []), ...older];
  const cursor = next ?? history.data?.next_cursor ?? null;

  async function loadMore() {
    if (!cursor) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await api<HistoryPage>(
        `/api/history?limit=25&cursor=${encodeURIComponent(cursor)}`,
      );
      setOlder((current) => [...current, ...page.items]);
      setNext(page.next_cursor);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingMore(false);
    }
  }

  async function undo(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/undo/${encodeURIComponent(id)}`, { method: "POST" });
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
      setOlder([]);
      setNext(null);
      history.reload();
    }
  }

  return (
    <section className="stg-section">
      <header className="stg-section-head">
        <h3>History</h3>
        <p>
          Every write Arno recorded, newest first. Undo what the ledger still allows; when a later
          edit blocks one, its reason is shown instead.
        </p>
      </header>
      {(error || history.error) && (
        <div className="stg-error" role="alert">
          <p>{error || history.error}</p>
        </div>
      )}
      {history.loading && !history.data && <p role="status">Loading history…</p>}
      {history.data && (
        <>
          {items.length === 0 ? (
            <div className="stg-empty">
              <p>No changes recorded yet.</p>
            </div>
          ) : (
            <HistoryEntries items={items} onUndo={(id) => void undo(id)} busy={busy} />
          )}
          {cursor && (
            <nav className="stg-pager" aria-label="History pages">
              <span className="stg-pager-count">{items.length} changes shown</span>
              <div className="stg-pager-buttons">
                <button
                  type="button"
                  className="ghost small"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </button>
              </div>
            </nav>
          )}
        </>
      )}
    </section>
  );
}
