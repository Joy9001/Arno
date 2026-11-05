import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { formatPaise } from "./format";
import type { ProjectionSource } from "./projection";

function citationEndpoint(source: ProjectionSource): string | null {
  if (source.kind === "account" || source.kind === "card") return `/api/accounts/${source.id}`;
  if (source.kind === "schedule") return `/api/schedules/${source.id}`;
  if (source.kind === "goal") return `/api/goals/${source.id}`;
  if (source.kind === "payee") return "/api/payees";
  return null;
}

function SourceFacts({ value }: { value: unknown }) {
  if (!value || typeof value !== "object") return <p className="fine">{String(value)}</p>;
  const row = value as Record<string, unknown>;
  const keys = [
    "id",
    "kind",
    "name",
    "institution",
    "last4",
    "balance_paise",
    "amount_paise",
    "date",
    "as_of",
    "status",
    "description",
    "rate_bps",
    "tenure_months",
  ];
  return (
    <>
      <dl className="citation-facts">
        {keys.map((key) => {
          const raw = row[key];
          if (raw === undefined || raw === null) return null;
          const text =
            key.endsWith("_paise") && typeof raw === "number"
              ? formatPaise(raw)
              : key === "rate_bps" && typeof raw === "number"
                ? `${raw / 100}%`
                : String(raw);
          const label = key.endsWith("_paise")
            ? key.slice(0, -"_paise".length).replaceAll("_", " ")
            : key === "rate_bps"
              ? "rate"
              : key.replaceAll("_", " ");
          return (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{text}</dd>
            </div>
          );
        })}
      </dl>
      <details className="citation-raw">
        <summary>Raw row</summary>
        <pre className="citation-json">{JSON.stringify(value, null, 2)}</pre>
      </details>
    </>
  );
}

/** Fetch and show the real row behind a citation. The source trace is never invented. */
export function SourceDetail({
  source,
  onClose,
}: {
  source: ProjectionSource;
  onClose: () => void;
}) {
  const endpoint = citationEndpoint(source);
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(endpoint));

  useEffect(() => {
    if (!endpoint) return;
    api<unknown>(endpoint)
      .then((value) => {
        if (source.kind === "payee") {
          const rows = Array.isArray(value)
            ? (value as { id?: string }[])
            : ((value as { items?: { id?: string }[] }).items ?? []);
          const row = rows.find((item) => item.id === source.id);
          if (row) setDetail(row);
          else setError("This source is no longer available.");
        } else {
          setDetail(value);
        }
      })
      .catch((caught: unknown) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, [endpoint, source]);

  return (
    <div className="citation-detail">
      <div className="citation-detail-head">
        <strong>
          {source.kind} · {source.name}
        </strong>
        <button type="button" className="ghost small" onClick={onClose}>
          Close
        </button>
      </div>
      {source.detail && <p className="fine">{source.detail}</p>}
      {loading && <p className="muted fine">Opening the underlying row…</p>}
      {error && <p className="error">{error}</p>}
      {!endpoint && !loading && !error && (
        <p className="fine">This source has no detailed row to open.</p>
      )}
      {detail !== null && <SourceFacts value={detail} />}
    </div>
  );
}
