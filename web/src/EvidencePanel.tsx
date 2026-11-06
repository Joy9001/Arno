import { Fragment, useState } from "react";
import { Button, IconButton } from "@radix-ui/themes";
import { ArrowUpRight, MessageCircle, X } from "lucide-react";
import { formatDate, formatPaise } from "./format";
import {
  ProjectionChart,
  type ProjectionPayload,
  type ProjectionSource,
} from "./projection";
import { dataQualityNotes, lowAmount, OBLIGATION_WARNING, paymentMethodLabel } from "./projectionData";
import { SourceDetail } from "./SourceDetail";

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function SourceRow({
  source,
  selected,
  onSelect,
}: {
  source: ProjectionSource;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={selected ? "source-row on" : "source-row"}
      aria-expanded={selected}
      onClick={onSelect}
    >
      <span className="source-row-main">
        <strong>{source.name}</strong>
        <span className="fine">
          {source.kind.replaceAll("_", " ")}
          {source.detail ? ` · ${source.detail}` : ""}
        </span>
      </span>
      {source.amount_paise !== undefined && (
        <span className="source-amount">{formatPaise(source.amount_paise)}</span>
      )}
    </button>
  );
}

export function EvidencePanel({
  payload,
  onClose,
  onOpenProjection,
  onAsk,
}: {
  payload: ProjectionPayload;
  onClose: () => void;
  onOpenProjection?: () => void;
  onAsk?: () => void;
}) {
  const [selected, setSelected] = useState<ProjectionSource | null>(null);
  const sources = payload.sources ?? [];
  const notes = dataQualityNotes(payload);
  const floorSet = payload.floor_set ?? (payload.floor_paise ?? 0) > 0;
  const floorText =
    payload.floor?.text ??
    (payload.floor_paise !== undefined ? formatPaise(payload.floor_paise) : null);
  const median = lowAmount(payload);
  const purchase = payload.hypothetical as
    | { amount_paise?: number; date?: string; method?: string; emi_months?: number }
    | undefined;
  const payment = paymentMethodLabel(purchase?.method);

  return (
    <div className="evidence">
      <header className="evidence-head">
        <div>
          <h2>Behind this answer</h2>
          <p className="fine">{payload.horizon_days}-day projection</p>
        </div>
        <IconButton
          type="button"
          variant="soft"
          color="gray"
          aria-label="Close sources"
          title="Close sources"
          onClick={onClose}
        >
          <X size={16} aria-hidden="true" />
        </IconButton>
      </header>

      <div className="evidence-body">
        <section className="evidence-summary" aria-label="Projection result">
          <div className="evidence-verdict">
            {floorSet ? (
              <span className={`badge ${payload.floor_probability >= payload.floor_threshold ? "good" : "danger"}`}>
                {percent(payload.floor_probability)} above floor
              </span>
            ) : (
              <span className="badge muted">No floor set</span>
            )}
            {floorSet && <span className="fine">threshold {percent(payload.floor_threshold)}</span>}
          </div>
          <dl className="evidence-figures">
            <div>
              <dt>Lowest median balance</dt>
              <dd>{median === null ? "—" : formatPaise(median)}</dd>
              <span>{formatDate(payload.median_low.date)}</span>
            </div>
            <div>
              <dt>Floor</dt>
              <dd>{floorText ?? "Not set"}</dd>
              <span>{floorSet ? "Your current setting" : "Set one in Profile"}</span>
            </div>
          </dl>
          <ProjectionChart payload={payload} height={150} />
        </section>

        <section className="evidence-section">
          <h3>Assumptions</h3>
          <ul className="evidence-list">
            <li>{payload.horizon_days} days from {formatDate(payload.as_of)}</li>
            {payload.runs !== undefined && (
              <li>
                {payload.runs.toLocaleString("en-IN")} simulated runs ·{" "}
                {percent(payload.floor_threshold)} threshold
              </li>
            )}
            {purchase?.amount_paise !== undefined && (
              <li>
                Purchase of {formatPaise(purchase.amount_paise)}
                {purchase.date ? ` on ${formatDate(purchase.date)}` : ""}
                {payment ? ` · ${payment}` : ""}
                {purchase.emi_months ? ` over ${purchase.emi_months} months` : ""}
              </li>
            )}
            {floorSet && payload.optimistic && (
              <li>
                Unconfirmed income is shown separately:{" "}
                {percent(payload.optimistic.floor_probability)} above floor, optimistic only
              </li>
            )}
            {payload.obligations_met === false && (
              <li>{OBLIGATION_WARNING}</li>
            )}
          </ul>
        </section>

        <section className="evidence-section">
          <div className="evidence-section-head">
            <h3>Sources</h3>
            <span className="fine">
              {sources.length} {sources.length === 1 ? "row" : "rows"}
            </span>
          </div>
          {sources.length ? (
            <div className="source-list">
              {sources.map((source) => (
                <Fragment key={`${source.kind}-${source.id}`}>
                  <SourceRow
                    source={source}
                    selected={selected === source}
                    onSelect={() => setSelected(selected === source ? null : source)}
                  />
                  {selected === source && (
                    <SourceDetail
                      source={source}
                      onClose={() => setSelected(null)}
                    />
                  )}
                </Fragment>
              ))}
            </div>
          ) : (
            <p className="empty-inline">No source rows were returned for this answer.</p>
          )}
        </section>

        {notes.length > 0 && (
          <section className="evidence-section">
            <h3>Data quality</h3>
            <ul className="evidence-list">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <footer className="evidence-foot">
        <p className="fine">
          Figures come from your ledger and plan. A probability is not a guarantee.
        </p>
        <div className="evidence-actions">
          {onAsk && (
            <Button variant="soft" color="gray" size="2" onClick={onAsk}>
              <MessageCircle size={15} aria-hidden="true" />
              Ask a follow-up
            </Button>
          )}
          {onOpenProjection && (
            <Button variant="soft" size="2" onClick={onOpenProjection}>
              <ArrowUpRight size={15} aria-hidden="true" />
              Open projection
            </Button>
          )}
        </div>
      </footer>
    </div>
  );
}
