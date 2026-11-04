import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { MessageCircle, X } from "lucide-react";
import { askAgent } from "../ask";
import { api, errorMessage } from "../api";
import { formatDate, formatPaise } from "../format";
import { Busy, Loading } from "../Loading";
import { movementLabel, movementNote, humanizeKind } from "../movement";
import type { Movement, MovementList } from "../types";

const KINDS = [
  "expense",
  "income",
  "transfer",
  "split",
  "loan",
  "refund",
  "settlement",
  "adjustment",
];

type Filters = {
  kind: string;
  includeReversed: boolean;
  start: string;
  end: string;
};

const EMPTY: Filters = { kind: "", includeReversed: false, start: "", end: "" };

function buildParams(filters: Filters, cursor?: string | null): string {
  const params = new URLSearchParams({
    limit: "50",
    include_reversed: String(filters.includeReversed),
  });
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.start) params.set("start", filters.start);
  if (filters.end) params.set("end", filters.end);
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

function MovementRow({
  movement,
  onOpen,
}: {
  movement: Movement;
  onOpen: (movement: Movement) => void;
}) {
  return (
    <article className="movement">
      <div className="movement-date">{formatDate(movement.date)}</div>
      <div className="movement-body">
        <h3 title={movement.description}>{movementLabel(movement)}</h3>
        <p className="fine">
          {movementNote(movement)}
          {movement.reversed ? " · reversed" : ""}
        </p>
      </div>
      <div className="movement-amount">
        <strong>{formatPaise(movement.amount_paise)}</strong>
        <span className={`badge ${movement.reversed ? "danger" : "muted"}`}>
          {movement.reversed
            ? "Reversed"
            : movement.kind.charAt(0).toUpperCase() + movement.kind.slice(1)}
        </span>
        <button
          type="button"
          className="ghost small movement-open"
          onClick={() => onOpen(movement)}
        >
          Details
        </button>
      </div>
    </article>
  );
}

export function LedgerPanel() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [applied, setApplied] = useState<Filters>(EMPTY);
  const [items, setItems] = useState<Movement[]>([]);
  const [total, setTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [openMovement, setOpenMovement] = useState<Movement | null>(null);

  useEffect(() => {
    let alive = true;
    api<MovementList>(`/api/movements?${buildParams(applied)}`)
      .then((result) => {
        if (!alive) return;
        setItems(result.items);
        setTotal(result.total_count);
        setNextCursor(result.next_cursor);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (alive) setError(errorMessage(caught));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [applied, version]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const result = await api<MovementList>(
        `/api/movements?${buildParams(applied, nextCursor)}`,
      );
      setItems((current) => [...current, ...result.items]);
      setTotal(result.total_count);
      setNextCursor(result.next_cursor);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>Every movement</h2>
          <p className="muted">
            One row for each real money movement, with the transfers that balanced it. Corrections
            are reversals; nothing is edited in place.
          </p>
        </div>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setLoading(true);
            setVersion((value) => value + 1);
          }}
        >
          {loading ? (
            <>
              <Busy />
              Refreshing…
            </>
          ) : (
            "Refresh"
          )}
        </button>
      </header>

      <form
        className="filters"
        onSubmit={(event) => {
          event.preventDefault();
          setLoading(true);
          setApplied(draft);
        }}
      >
        <label>
          Kind
          <select
            value={draft.kind}
            onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
          >
            <option value="">All kinds</option>
            {KINDS.map((option) => (
              <option key={option} value={option}>
                {humanizeKind(option)}
              </option>
            ))}
          </select>
        </label>
        <label>
          From
          <input
            type="date"
            value={draft.start}
            onChange={(event) => setDraft({ ...draft, start: event.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={draft.end}
            onChange={(event) => setDraft({ ...draft, end: event.target.value })}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.includeReversed}
            onChange={(event) =>
              setDraft({ ...draft, includeReversed: event.target.checked })
            }
          />
          Show reversed
        </label>
        <button type="submit">Apply</button>
        <button
          type="button"
          className="ghost"
          onClick={() => {
            setLoading(true);
            setDraft(EMPTY);
            setApplied(EMPTY);
          }}
        >
          Clear
        </button>
      </form>

      <div className="list-meta">
        <span className="fine">{total} movements</span>
      </div>

      {loading && items.length === 0 && <Loading label="Loading the ledger…" />}
      {error && (
        <div className="panel-error">
          <p className="error">{error}</p>
          <button
            type="button"
            className="ghost small"
            onClick={() => {
              setLoading(true);
              setVersion((value) => value + 1);
            }}
          >
            Retry
          </button>
        </div>
      )}
      {!loading && !error && items.length === 0 && <p className="muted">No movements match.</p>}

      <div className="movement-list">
        {items.length > 0 && (
          <div className="movement-header" aria-hidden="true">
            <span>Date</span>
            <span>Movement</span>
            <span>Amount</span>
          </div>
        )}
        {items.map((movement) => (
          <MovementRow key={movement.id} movement={movement} onOpen={setOpenMovement} />
        ))}
      </div>

      {nextCursor && (
        <div className="load-more">
          <button type="button" className="ghost" onClick={() => void loadMore()}>
            {loadingMore ? (
              <>
                <Busy />
                Loading…
              </>
            ) : (
              "Load more"
            )}
          </button>
        </div>
      )}

      {openMovement && (
        <Dialog.Root open onOpenChange={(open) => !open && setOpenMovement(null)}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay" />
            <Dialog.Content className="movement-dialog" aria-describedby={undefined}>
              <div className="queue-head">
                <Dialog.Title>{movementLabel(openMovement)}</Dialog.Title>
                <Dialog.Close asChild>
                  <button type="button" className="ghost small" aria-label="Close">
                    <X size={15} strokeWidth={1.8} aria-hidden="true" />
                  </button>
                </Dialog.Close>
              </div>
              <dl className="detail-grid">
                <dt>ID</dt>
                <dd>{openMovement.id}</dd>
                <dt>Date</dt>
                <dd>{formatDate(openMovement.date)}</dd>
                <dt>Kind</dt>
                <dd>{humanizeKind(openMovement.kind)}</dd>
                <dt>Category</dt>
                <dd>{openMovement.category_name ?? "unlabelled"}</dd>
                <dt>Payee</dt>
                <dd>{openMovement.payee_name ?? "not set"}</dd>
                <dt>Statement text</dt>
                <dd className="statement-text">{openMovement.description}</dd>
                <dt>Amount</dt>
                <dd>{formatPaise(openMovement.amount_paise)}</dd>
                <dt>State</dt>
                <dd>{openMovement.reversed ? "reversed" : "live"}</dd>
                <dt>Evidence</dt>
                <dd>
                  {openMovement.evidence.length
                    ? openMovement.evidence
                        .map((item) => item.replaceAll("_", " "))
                        .join(", ")
                    : "none"}
                </dd>
              </dl>
              <h3 className="section-title">Transfers</h3>
              <ul className="transfers">
                {openMovement.transfers.map((transfer) => (
                  <li key={transfer.transfer_id}>
                    <span>
                      {transfer.from_name} → {transfer.to_name}
                    </span>
                    <span>{formatPaise(transfer.amount_paise)}</span>
                  </li>
                ))}
              </ul>
              <div className="button-row">
                <button
                  type="button"
                  className="ask-link"
                  onClick={() => {
                    askAgent(
                      `Explain the movement "${movementLabel(openMovement)}" for ${formatPaise(openMovement.amount_paise)} on ${formatDate(openMovement.date)}.`,
                    );
                    setOpenMovement(null);
                  }}
                >
                  <MessageCircle size={13} aria-hidden="true" />
                  Ask about this
                </button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </section>
  );
}
