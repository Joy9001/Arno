import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { displayText } from "./chat/display-text";
import { formatDate, formatPaise } from "./format";
import type { Movement, NotificationItem } from "./types";

export function PriceChangeRows({ rows }: { rows: Movement[] }) {
  return <ul className="recap-rows">{rows.map((row) => (
    <li key={row.id}>
      <span>{formatDate(row.date)}</span>
      <span>{displayText(row.payee_name ?? row.description)}<em>{row.transfers.map((transfer) => transfer.from_name).join(", ")}</em></span>
      <b>{formatPaise(row.amount_paise)}</b>
      <span className="fine">{row.id}</span>
    </li>
  ))}</ul>;
}

export function PriceChange({ item }: { item: NotificationItem }) {
  const [rows, setRows] = useState<Movement[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const ids = Array.isArray(item.data.movement_ids)
    ? item.data.movement_ids.filter((id): id is string => typeof id === "string") : [];
  const idsKey = JSON.stringify(ids);
  useEffect(() => {
    let alive = true;
    const movementIds: string[] = JSON.parse(idsKey);
    if (!movementIds.length) return;
    Promise.all(movementIds.map((id) => api<Movement>(`/api/movements/${encodeURIComponent(id)}`)))
      .then((value) => { if (alive) { setRows(value); setError(null); } })
      .catch((caught: unknown) => { if (alive) setError(errorMessage(caught)); });
    return () => { alive = false; };
  }, [idsKey, version]);
  const { old_paise: old, new_paise: next, step_date: date } = item.data;
  return <div>
    {typeof old === "number" && typeof next === "number" && typeof date === "string" &&
      <p>Old {formatPaise(old)} · New {formatPaise(next)} · First charged {formatDate(date)}</p>}
    {ids.length > 0 && <>
      <strong>Payments behind this change</strong>
      {error ? <p role="alert">Could not load payments: {error} <button type="button" className="pill-button" onClick={() => setVersion((n) => n + 1)}>Retry</button></p>
        : rows ? <PriceChangeRows rows={rows} /> : <p>Loading payments…</p>}
    </>}
  </div>;
}
