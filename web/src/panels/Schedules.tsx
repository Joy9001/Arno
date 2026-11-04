import { MessageCircle } from "lucide-react";
import { askAgent } from "../ask";
import { formatDate, formatPaise, isoDate } from "../format";
import { Loading } from "../Loading";
import type { Schedule, Upcoming } from "../types";
import { useApi } from "../useApi";

type ScheduleList = { items: Schedule[]; as_of: string };

function isIncoming(kind: string): boolean {
  return kind.startsWith("income");
}

function direction(kind: string): "in" | "out" {
  return isIncoming(kind) ? "in" : "out";
}

export function SchedulesPanel() {
  const schedules = useApi<ScheduleList>("/api/schedules");
  const upcoming = useApi<Upcoming>("/api/schedules/upcoming?days=90");

  const events = upcoming.data?.events ?? [];
  const from = upcoming.data?.from
    ? new Date(`${upcoming.data.from}T00:00:00`)
    : new Date();
  const cutoff = new Date(from);
  cutoff.setDate(cutoff.getDate() + 30);
  const cutoffIso = isoDate(cutoff);
  const next30 = events.filter((event) => event.date <= cutoffIso);
  const incoming = next30
    .filter((event) => isIncoming(event.kind))
    .reduce((total, event) => total + (event.amount_paise ?? 0), 0);
  const outgoing = next30
    .filter((event) => !isIncoming(event.kind))
    .reduce((total, event) => total + (event.amount_paise ?? 0), 0);

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>What is coming</h2>
          <p className="muted">
            Income, obligations, family transfers, instalment plans, and goal contributions. Rules
            are iCalendar RRULEs, adjusted for Indian weekends and holidays.
          </p>
        </div>
        {upcoming.data && <p className="fine">from {formatDate(upcoming.data.from)}</p>}
      </header>

      {events.length > 0 && (
        <>
          <div className="summary-strip">
            <div className="summary-cell">
              <span>Money in · 30 days</span>
              <strong className="positive">{formatPaise(incoming)}</strong>
            </div>
            <div className="summary-cell">
              <span>Money out · 30 days</span>
              <strong>{formatPaise(outgoing)}</strong>
            </div>
            <div className="summary-cell">
              <span>Net · 30 days</span>
              <strong className={incoming - outgoing < 0 ? "negative" : "positive"}>
                {formatPaise(incoming - outgoing)}
              </strong>
            </div>
          </div>

          <p className="fine">
            The totals above cover the first 30 days; the list below covers 90.
          </p>
        </>
      )}

      <div className="section-head">
        <h3>Next 90 days</h3>
        <span className="fine">{events.length} events</span>
      </div>

      {upcoming.error && (
        <div className="panel-error">
          <p className="error">{upcoming.error}</p>
          <button type="button" className="ghost small" onClick={upcoming.reload}>
            Retry
          </button>
        </div>
      )}
      {upcoming.loading && !upcoming.data && <Loading label="Loading the schedule…" />}
      {upcoming.data && events.length === 0 && (
        <div className="empty-state">
          <p>Nothing scheduled in the next 90 days.</p>
          <button
            type="button"
            className="ask-link"
            onClick={() => askAgent("Add a bill or income: ")}
          >
            <MessageCircle size={13} aria-hidden="true" />
            Ask to add one
          </button>
        </div>
      )}

      {events.length > 0 && (
        <div className="schedule-list">
          {events.map((event, index) => (
            <article className="schedule-row" key={`${event.date}-${event.name}-${index}`}>
              <span className="schedule-date">{formatDate(event.date)}</span>
              <div className="schedule-main">
                <strong>{event.name}</strong>
                <span className="fine">
                  {(event.subtype ?? event.kind).replaceAll("_", " ")}
                  {event.account_name ? ` · ${event.account_name}` : ""}
                  {!event.confirmed ? " · expected" : ""}
                </span>
              </div>
              <span
                className={
                  direction(event.kind) === "in" ? "schedule-amount positive" : "schedule-amount"
                }
              >
                {formatPaise(event.amount_paise)}
              </span>
              <button
                type="button"
                className="ask-link"
                onClick={() => askAgent(`Tell me about ${event.name} on ${formatDate(event.date)}.`)}
              >
                <MessageCircle size={13} aria-hidden="true" />
                Ask
              </button>
            </article>
          ))}
        </div>
      )}

      <details className="all-schedules">
        <summary>
          All schedules{schedules.data ? ` (${schedules.data.items.length})` : ""}
        </summary>
        {schedules.error && (
          <div className="panel-error">
            <p className="error">{schedules.error}</p>
            <button type="button" className="ghost small" onClick={schedules.reload}>
              Retry
            </button>
          </div>
        )}
        {schedules.data && schedules.data.items.length === 0 && (
          <p className="empty-state">No schedules yet.</p>
        )}
        {schedules.data && schedules.data.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Kind</th>
                  <th>Amount</th>
                  <th>Rule</th>
                  <th>Next</th>
                  <th>Account</th>
                </tr>
              </thead>
              <tbody>
                {schedules.data.items.map((schedule) => (
                  <tr key={schedule.id}>
                    <td>
                      {schedule.name}
                      {!schedule.confirmed && <span className="badge muted">expected</span>}
                      {!schedule.active && <span className="badge danger">archived</span>}
                    </td>
                    <td>
                      <span className="badge muted">{schedule.subtype ?? schedule.kind}</span>
                    </td>
                    <td className="num">{formatPaise(schedule.amount_paise)}</td>
                    <td>
                      <code>{schedule.rule}</code>
                      {schedule.holiday_adjust !== "none" && (
                        <span className="fine"> · {schedule.holiday_adjust} working day</span>
                      )}
                    </td>
                    <td>{formatDate(schedule.next_date)}</td>
                    <td>{schedule.account_name ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </section>
  );
}
