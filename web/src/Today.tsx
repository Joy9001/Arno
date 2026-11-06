import { Skeleton } from "@radix-ui/themes";
import { AlertTriangle, ArrowUpRight, FileSearch } from "lucide-react";
import { formatRupees } from "./chat/format";
import { formatDate } from "./format";
import { Busy } from "./Loading";
import { ProjectionChart, type ProjectionPayload } from "./projection";
import { OBLIGATION_WARNING, projectionReadiness } from "./projectionData";
import type { CashQuality, Movement, NotificationItem } from "./types";

export type HomePayload = {
  as_of: string;
  spendable_paise: number;
  cash_quality: CashQuality;
  projection: ProjectionPayload;
  unread_count: number;
  needs_you: NotificationItem[];
  recent: Movement[];
};

const HORIZONS = [90, 365];

/** Home's one answer: cash on hand, the floor's probability, and the shape of the horizon. */
export function TodaySummary({
  data,
  horizon,
  onHorizon,
  onOpenProjection,
  onOpenEvidence,
  onOpenSettings,
  busy = false,
}: {
  data: HomePayload;
  horizon: number;
  onHorizon: (days: number) => void;
  onOpenProjection?: () => void;
  onOpenEvidence?: (payload: ProjectionPayload) => void;
  onOpenSettings?: () => void;
  busy?: boolean;
}) {
  const projection = data.projection;
  const cash = data.cash_quality;
  const cashNames = cash.unknown_accounts.slice(0, 2).join(", ");
  const cashMore =
    cash.unknown_accounts.length > 2 ? ` and ${cash.unknown_accounts.length - 2} more` : "";
  const low =
    projection.median_low.amount?.text ??
    (projection.median_low.paise !== undefined
      ? formatRupees(projection.median_low.paise)
      : null);
  const floorText =
    projection.floor?.text ??
    (projection.floor_paise !== undefined ? formatRupees(projection.floor_paise) : null);
  const floorSet = projection.floor_set ?? (projection.floor_paise ?? 0) > 0;
  const holds = projection.floor_probability >= projection.floor_threshold;
  const readiness = projectionReadiness(projection);
  const cashMessage =
    cash.status === "no_accounts"
      ? "The projection needs a liquid account. Add a bank, cash, or wallet account with its balance."
      : `The projection needs a known starting balance. Add a statement balance or import a statement${
          cashNames ? ` for ${cashNames}${cashMore}` : ""
        }.`;
  const gated = !readiness.ready || cash.status !== "known";
  const gateMessage = readiness.message ?? cashMessage;

  return (
    <section className="today" aria-label="Today's money">
      <div className="today-cash">
        <div className="today-label">Cash on hand</div>
        {cash.status === "known" ? (
          <div className="today-amount">{formatRupees(data.spendable_paise)}</div>
        ) : (
          <div className="today-amount today-amount-unknown">
            {cash.status === "no_accounts" ? "No accounts" : "Unknown"}
          </div>
        )}
        <p className="today-scope">
          {cash.status === "known"
            ? `Liquid account balances · as of ${formatDate(data.as_of)}`
            : cash.status === "no_accounts"
              ? "Add a bank, cash, or wallet account to see cash on hand."
              : `No known balance for ${cashNames}${cashMore}. Add a statement balance or import a statement.`}
        </p>
      </div>

      <div className="today-projection">
        <div className="today-projection-head">
          <h3>Next {horizon === 365 ? "year" : `${horizon} days`}</h3>
          <div className="today-controls">
            {busy && (
              <span className="updating" aria-live="polite">
                <Busy />
                Updating…
              </span>
            )}
            <div className="seg" role="group" aria-label="Horizon">
              {HORIZONS.map((days) => (
                <button
                  key={days}
                  type="button"
                  className={days === horizon ? "on" : undefined}
                  aria-pressed={days === horizon}
                  aria-busy={busy || undefined}
                  onClick={() => onHorizon(days)}
                >
                  {days === 365 ? "1y" : `${days}d`}
                </button>
              ))}
            </div>
          </div>
        </div>

        {gated ? (
          <p className="today-warning">
            <AlertTriangle size={15} aria-hidden="true" />
            <span>{gateMessage}</span>
          </p>
        ) : (
          <>
            <p className="today-verdict">
              {floorSet ? (
                <>
                  <b className={holds ? "good" : "danger"}>
                    {Math.round(projection.floor_probability * 100)}%
                  </b>{" "}
                  chance the floor holds
                  {floorText ? <> · floor {floorText}</> : null}
                </>
              ) : (
                <>
                  <b className="neutral">No floor set</b>
                  {onOpenSettings ? (
                    <>
                      {" "}
                      ·{" "}
                      <button type="button" className="inline-link" onClick={onOpenSettings}>
                        Set a floor
                      </button>
                    </>
                  ) : null}
                </>
              )}
            </p>

            {projection.obligations_met === false && (
              <p className="today-warning">
                <AlertTriangle size={15} aria-hidden="true" />
                <span>{OBLIGATION_WARNING}</span>
              </p>
            )}

            <div className={busy ? "today-chart busy" : "today-chart"}>
              <ProjectionChart payload={projection} height={110} />
            </div>

            <p className="today-facts">
              {low ? (
                <>
                  Lowest median balance {low} on {formatDate(projection.median_low.date)}
                </>
              ) : (
                "No median balance was returned for this horizon."
              )}
              {projection.first_floor_crossing
                ? ` · median crosses ${floorSet ? "the floor" : "₹0"} on ${formatDate(
                    projection.first_floor_crossing,
                  )}`
                : floorSet
                  ? " · median stays above the floor"
                  : ""}
            </p>
          </>
        )}

        <div className="today-foot">
          {onOpenEvidence && projection.sources?.length ? (
            <button
              type="button"
              className="ghost small"
              onClick={() => onOpenEvidence(projection)}
            >
              <FileSearch size={14} strokeWidth={1.8} aria-hidden="true" />
              Sources
            </button>
          ) : null}
          {onOpenProjection && (
            <button type="button" className="ghost small" onClick={onOpenProjection}>
              <ArrowUpRight size={14} strokeWidth={1.8} aria-hidden="true" />
              Projection
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

export function TodaySummarySkeleton() {
  return (
    <section className="today today-skeleton" aria-busy="true" aria-label="Loading today's money">
      <div className="today-cash">
        <Skeleton className="sk sk-label" />
        <Skeleton className="sk sk-amount" />
        <Skeleton className="sk sk-line short" />
      </div>
      <div className="today-projection">
        <div className="today-projection-head">
          <Skeleton className="sk sk-heading" />
          <Skeleton className="sk sk-seg" />
        </div>
        <Skeleton className="sk sk-line wide" />
        <Skeleton className="sk sk-chart" />
        <Skeleton className="sk sk-line" />
      </div>
      <span className="sr-only">Loading today's numbers…</span>
    </section>
  );
}
