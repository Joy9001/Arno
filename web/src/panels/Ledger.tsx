import { useState } from "react";
import { formatDate, formatPaise } from "../format";
import type { Movement, MovementList } from "../types";
import { useApi } from "../useApi";

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

function MovementRow({ movement }: { movement: Movement }) {
  return (
    <article className="movement">
      <div className="movement-main">
        <div>
          <p className="fine">{formatDate(movement.date)}</p>
          <h3>{movement.description}</h3>
          <p className="fine">
            {[movement.category_name, movement.payee_name].filter(Boolean).join(" · ") || movement.kind}
          </p>
        </div>
        <div className="movement-amount">
          <strong>{formatPaise(movement.amount_paise)}</strong>
          <span className={`badge ${movement.reversed ? "danger" : "muted"}`}>
            {movement.reversed ? "reversed" : movement.kind}
          </span>
        </div>
      </div>
      <ul className="transfers">
        {movement.transfers.map((transfer) => (
          <li key={transfer.transfer_id}>
            <span>
              {transfer.from_name} → {transfer.to_name}
            </span>
            <span>{formatPaise(transfer.amount_paise)}</span>
          </li>
        ))}
      </ul>
      {movement.evidence.length > 0 && (
        <p className="fine">Evidence: {movement.evidence.join(", ")}</p>
      )}
    </article>
  );
}

export function LedgerPanel() {
  const [kind, setKind] = useState("");
  const [includeReversed, setIncludeReversed] = useState(false);
  const params = new URLSearchParams({ limit: "100", include_reversed: String(includeReversed) });
  if (kind) params.set("kind", kind);
  const { data, error, loading, reload } = useApi<MovementList>(`/api/movements?${params}`);

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Ledger</p>
          <h2>Every movement</h2>
          <p className="muted">
            One row for each real money movement, with the transfers that balanced it. Corrections
            are reversals; nothing is edited in place.
          </p>
        </div>
        <button type="button" className="ghost" onClick={reload}>
          Refresh
        </button>
      </header>

      <div className="filters">
        <label>
          Kind
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="">All kinds</option>
            {KINDS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={includeReversed}
            onChange={(event) => setIncludeReversed(event.target.checked)}
          />
          Show reversed rows
        </label>
        {data && <span className="fine">{data.total_count} movements</span>}
      </div>

      {loading && !data && <p className="muted">Loading the ledger…</p>}
      {error && <p className="error">{error}</p>}
      {data && data.items.length === 0 && <p className="muted">No movements match.</p>}
      <div className="movement-list">
        {data?.items.map((movement) => <MovementRow key={movement.id} movement={movement} />)}
      </div>
    </section>
  );
}
