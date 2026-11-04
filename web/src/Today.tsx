import { formatRupees } from "./chat/format";
import { formatDate } from "./format";
import { Loading, Busy } from "./Loading";
import { ProjectionChart, type ProjectionPayload } from "./projection";
import type { Movement, NotificationItem } from "./types";

export type HomePayload = {
  as_of: string;
  spendable_paise: number;
  projection: ProjectionPayload;
  unread_count: number;
  needs_you: NotificationItem[];
  recent: Movement[];
};

const HORIZONS = [90, 365];

/** Home's one answer: spendable, the floor's probability, and the shape of the horizon. */
export function TodaySummary({
  data,
  horizon,
  onHorizon,
  onOpenProjection,
  busy = false,
}: {
  data: HomePayload;
  horizon: number;
  onHorizon: (days: number) => void;
  onOpenProjection?: () => void;
  busy?: boolean;
}) {
  const projection = data.projection;
  const low =
    projection.median_low.amount?.text ??
    (projection.median_low.paise !== undefined
      ? formatRupees(projection.median_low.paise)
      : null);
  const floorText =
    projection.floor?.text ??
    (projection.floor_paise !== undefined
      ? formatRupees(projection.floor_paise)
      : null);
  const floorSet = projection.floor_set ?? (projection.floor_paise ?? 0) > 0;
  const holds = projection.floor_probability >= projection.floor_threshold;

  return (
    <section className="today" aria-label="Today's money">
      <div className="today-head">
        <div>
          <div className="today-label">Spendable</div>
          <div className="today-amount">{formatRupees(data.spendable_paise)}</div>
          <p className="today-note">
            {floorSet ? (
              <>
                Floor holds in{" "}
                <b className={holds ? "good" : "danger"}>
                  {Math.round(projection.floor_probability * 100)}%
                </b>{" "}
                of {projection.runs?.toLocaleString("en-IN") ?? "1,000"} runs
                {floorText ? <> · floor {floorText}</> : null}
                {` · threshold ${Math.round(projection.floor_threshold * 100)}%`}
                {low ? (
                  <>
                    {" "}
                    · lowest median {low} on {formatDate(projection.median_low.date)}
                  </>
                ) : null}
              </>
            ) : (
              <>
                <b className="neutral">No floor set</b>
                {low ? (
                  <>
                    {" "}
                    · lowest median {low} on {formatDate(projection.median_low.date)}
                  </>
                ) : null}
                {projection.first_floor_crossing ? (
                  <> · median crosses ₹0 on {formatDate(projection.first_floor_crossing)}</>
                ) : null}{" "}
                ·{" "}
                <a className="inline-link" href="#settings/profile">
                  Set a floor
                </a>
              </>
            )}
          </p>
        </div>
        <div className="today-controls">
          {busy && <Busy />}
          <div className="seg" role="group" aria-label="Horizon">
            {HORIZONS.map((days) => (
              <button
                key={days}
                type="button"
                className={days === horizon ? "on" : undefined}
                aria-busy={busy || undefined}
                onClick={() => onHorizon(days)}
              >
                {days === 365 ? "1y" : `${days}d`}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className={busy ? "today-chart busy" : "today-chart"}>
        <ProjectionChart payload={projection} height={110} />
      </div>
      {onOpenProjection && (
        <div className="today-foot">
          <button type="button" className="ghost small" onClick={onOpenProjection}>
            Open projection
          </button>
        </div>
      )}
    </section>
  );
}

export function TodaySummarySkeleton() {
  return (
    <section className="today today-state" aria-busy="true">
      <Loading label="Loading today's numbers…" />
    </section>
  );
}
