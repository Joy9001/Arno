import { useState } from "react";
import { formatDate, formatPaise, isoDate } from "../format";
import type { Schedule, Upcoming, UpcomingEvent } from "../types";
import { useApi } from "../useApi";
import {
  MoneyAsk,
  MoneyEmpty,
  MoneyError,
  MoneyPageHeader,
  MoneyPageSkeleton,
  MoneyPager,
  MoneyRefreshing,
  MoneySection,
  MoneySummary,
} from "./MoneyBits";
import { countLabel, pageSlice, safePage } from "./moneyView";
import { SchedulePaymentDetails } from "./SchedulePaymentDetails";
import { paymentLabel, remainingAmount } from "./scheduleView";

type ScheduleList = { items: Schedule[]; as_of: string };
type Bucket = "income" | "bills" | "subscriptions" | "cards" | "goals" | "other";
type BucketFilter = "all" | Bucket;

const PAGE_SIZE = 20;
const PERIODS = [30, 90, 180, 365, 730];
const SCHEDULES_DESCRIPTION =
  "Income, bills, and contributions, with payments matched to your ledger.";

const KIND_LABEL: Record<string, string> = {
  income_fixed: "Fixed income",
  income_variable: "Variable income",
  income_oneoff: "One-off income",
  obligation: "Obligation",
  instalment: "Instalment",
  goal_contribution: "Goal contribution",
  card_due: "Card payment",
  rent: "Rent",
  emi: "EMI",
  subscription: "Subscription",
  insurance: "Insurance",
  family_transfer: "Family transfer",
  other: "Other",
};

function humanKind(kind: string | null | undefined): string {
  if (!kind) return "Other";
  return KIND_LABEL[kind] ?? kind.replaceAll("_", " ");
}

const BUCKETS: Array<{ id: BucketFilter; label: string }> = [
  { id: "all", label: "All types" },
  { id: "income", label: "Income" },
  { id: "bills", label: "Bills and obligations" },
  { id: "subscriptions", label: "Subscriptions" },
  { id: "cards", label: "Card payments" },
  { id: "goals", label: "Goal contributions" },
  { id: "other", label: "Other outgoing" },
];

function isIncoming(kind: string): boolean {
  return kind.startsWith("income");
}

function bucketOf(event: UpcomingEvent): Bucket {
  if (isIncoming(event.kind)) return "income";
  if (event.kind === "card_due") return "cards";
  if (event.kind === "goal_contribution") return "goals";
  if (event.subtype === "subscription") return "subscriptions";
  if (event.kind === "obligation" || event.kind === "instalment") return "bills";
  return "other";
}

function bucketLabel(bucket: Bucket): string {
  return BUCKETS.find((item) => item.id === bucket)?.label ?? "Other outgoing";
}

function EventRow({ event }: { event: UpcomingEvent }) {
  const incoming = isIncoming(event.kind);
  const bucket = bucketOf(event);
  const amount =
    event.amount_paise === null
      ? "—"
      : `${incoming ? "+" : "−"}${formatPaise(event.amount_paise)}`;
  const detail = [
    bucketLabel(bucket),
    event.subtype ? humanKind(event.subtype) : null,
    event.account_name,
    event.confirmed ? null : "expected",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article className={`moneyv-row schedule-event${incoming ? " incoming" : ""}${event.payment_status === "paid" ? " paid" : ""}`}>
      <time className="schedule-event-date" dateTime={event.date}>{formatDate(event.date)}</time>
      <div className="moneyv-row-main">
        <strong className="schedule-event-name">{event.name}</strong>
        <span className="moneyv-meta">{detail}</span>
        <span className={`schedule-payment-status ${event.payment_status ?? "scheduled"}`}>{paymentLabel(event)}</span>
        {event.payment_status === "partial" && <span className="moneyv-meta">{formatPaise(event.paid_paise)} matched · {formatPaise(event.remaining_paise)} remaining</span>}
      </div>
      <div className="moneyv-figures">
        <strong className={`schedule-event-amount${incoming ? " moneyv-positive" : ""}`}>{amount}</strong>
      </div>
      {event.schedule_id ? <SchedulePaymentDetails event={event} /> : <MoneyAsk prompt={`Tell me about ${event.name} on ${formatDate(event.date)}.`} />}
    </article>
  );
}

function ScheduleCalendar({ start, events }: { start: string; events: UpcomingEvent[] }) {
  const today = isoDate(new Date());
  const first = new Date(`${start}T00:00:00`);
  return (
    <div className="schedule-calendar" role="region" aria-label="Next 30 days of schedules" tabIndex={0}>
      {Array.from({ length: 30 }, (_, index) => {
        const date = new Date(first);
        date.setDate(first.getDate() + index);
        const day = isoDate(date);
        const scheduled = events.filter((event) => event.date === day);
        return (
          <div className={`schedule-day${day === today ? " today" : ""}`} key={day}>
            <time dateTime={day}>{date.getDate()}<small>{date.toLocaleDateString("en-IN", { month: "short" })}</small></time>
            {day === today && <span className="sr-only">Today.</span>}
            <div className="schedule-day-marks">
              {scheduled.slice(0, 3).map((event, eventIndex) => (
                <span key={eventIndex} className={isIncoming(event.kind) ? "in" : "out"} title={event.name} />
              ))}
            </div>
            <span className="sr-only">{scheduled.length ? scheduled.map((event) => event.name).join(", ") : "Nothing scheduled"}</span>
          </div>
        );
      })}
    </div>
  );
}

export function SchedulesPanel() {
  const [days, setDays] = useState(90);
  const [bucket, setBucket] = useState<BucketFilter>("all");
  const [page, setPage] = useState(0);
  const [schedulePage, setSchedulePage] = useState(0);
  const upcoming = useApi<Upcoming>(`/api/schedules/upcoming?days=${days}&past_days=31`);
  const schedules = useApi<ScheduleList>("/api/schedules");

  if (upcoming.loading && !upcoming.data) {
    return (
      <MoneyPageSkeleton
        title="Schedules"
        description={SCHEDULES_DESCRIPTION}
        summary={3}
        rows={6}
      />
    );
  }

  const events = upcoming.data?.events ?? [];
  const future = events.filter((event) => event.date >= (upcoming.data?.as_of ?? isoDate(new Date())));
  const incoming = future
    .filter((event) => isIncoming(event.kind))
    .reduce((total, event) => total + remainingAmount(event), 0);
  const outgoing = future
    .filter((event) => !isIncoming(event.kind))
    .reduce((total, event) => total + remainingAmount(event), 0);
  const net = incoming - outgoing;
  const filtered = bucket === "all" ? events : events.filter((event) => bucketOf(event) === bucket);
  const currentPage = safePage(page, filtered.length, PAGE_SIZE);
  const pageEvents = pageSlice(filtered, currentPage, PAGE_SIZE);
  const loadedDays =
    upcoming.data?.from && upcoming.data?.to
      ? Math.max(
          1,
          Math.round(
            (Date.parse(upcoming.data.to) - Date.parse(upcoming.data.as_of)) / 86_400_000,
          ),
        )
      : days;
  const period =
    upcoming.data?.from && upcoming.data?.to
      ? `${formatDate(upcoming.data.as_of)} – ${formatDate(upcoming.data.to)}`
      : undefined;

  const header = (
    <MoneyPageHeader
      title="Schedules"
      description={SCHEDULES_DESCRIPTION}
      aside={
        <>
          <label className="moneyv-inline-control">
            Period
            <select
              value={days}
              onChange={(event) => {
                setDays(Number(event.target.value));
                setPage(0);
                setSchedulePage(0);
              }}
            >
              {PERIODS.map((periodDays) => (
                <option key={periodDays} value={periodDays}>
                  {periodDays} days
                </option>
              ))}
            </select>
          </label>
          {upcoming.data && <span>As of {formatDate(upcoming.data.as_of)}</span>}
          <button type="button" className="ghost small" disabled={upcoming.loading} onClick={() => { upcoming.reload(); schedules.reload(); }}>Refresh</button>
          {upcoming.loading && <MoneyRefreshing />}
        </>
      }
    />
  );

  if (!upcoming.data) {
    return (
      <section className="panel">
        {header}
        <MoneyError
          title="Couldn’t load the schedule"
          message={upcoming.error ?? "The schedule request did not return data."}
          onRetry={upcoming.reload}
        />
      </section>
    );
  }

  return (
    <section className="panel">
      {header}
      {upcoming.error && (
        <MoneyError
          title="Couldn’t refresh the schedule"
          message={upcoming.error}
          onRetry={upcoming.reload}
        />
      )}

      {events.length > 0 && (
        <MoneySummary
          label={`Remaining scheduled amounts for the next ${loadedDays} days`}
          cells={[
            {
              label: "Money in",
              value: formatPaise(incoming),
              note: `Next ${loadedDays} days`,
              tone: incoming > 0 ? "positive" : undefined,
            },
            {
              label: "Money out",
              value: formatPaise(outgoing),
              note: period ?? "displayed period",
            },
            {
              label: "Net",
              value: formatPaise(net),
              note: "Remaining expected movement",
              tone: net < 0 ? "negative" : net > 0 ? "positive" : undefined,
            },
          ]}
        />
      )}
      {events.length > 0 && period && <p className="moneyv-basis">Totals show amounts still expected over {period}, after matched payments.</p>}
      {events.some((event) => event.amount_paise === null) && (
        <p className="moneyv-basis">
          Events without an amount are shown but not included in the totals.
        </p>
      )}
      {events.some((event) => event.kind === "card_due") && (
        <p className="moneyv-basis">
          Card forecasts use today’s outstanding for each due date, so the same bill can appear in
          more than one month. They do not track individual statement payments.
        </p>
      )}

      <ScheduleCalendar start={upcoming.data.as_of} events={events} />
      <p className="moneyv-meta">Next 30 days · green: incoming · orange: outgoing. Payment status comes from matched ledger movements.</p>

      <MoneySection
        title="Scheduled events"
        note={filtered.length ? countLabel(filtered.length, "event") : undefined}
      >
        <p className="moneyv-basis">Includes the past 31 days. Unmatched means no payment is linked; it does not mean unpaid.</p>
        {events.length > 0 && (
          <div className="moneyv-controls">
            <label className="moneyv-inline-control">
              Type
              <select
                value={bucket}
                onChange={(event) => {
                  setBucket(event.target.value as BucketFilter);
                  setPage(0);
                }}
              >
                {BUCKETS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <span className="moneyv-count">{period}</span>
          </div>
        )}

        {filtered.length === 0 ? (
          <div className="moneyv-empty">
            <p>
              {events.length === 0
                ? `Nothing is scheduled in the next ${loadedDays} days.`
                : bucket === "all"
                  ? `No events to show in the next ${loadedDays} days.`
                  : `No events in “${bucketLabel(bucket)}” over the next ${loadedDays} days.`}
            </p>
            <div className="moneyv-form-actions">
              {events.length === 0 ? (
                <MoneyAsk prompt="Add a bill or income: " label="Ask to add one" />
              ) : (
                <button
                  type="button"
                  className="pill-button"
                  onClick={() => {
                    setBucket("all");
                    setPage(0);
                  }}
                >
                  Show all types
                </button>
              )}
            </div>
          </div>
        ) : (
          <>
            <div className="moneyv-list">
              {pageEvents.map((event, index) => (
                <EventRow key={`${event.date}-${event.name}-${index}`} event={event} />
              ))}
            </div>
            <MoneyPager
              page={currentPage}
              pageSize={PAGE_SIZE}
              total={filtered.length}
              noun="events"
              onPage={setPage}
            />
          </>
        )}
      </MoneySection>

      <MoneySection title="All schedules" note={schedules.data ? countLabel(schedules.data.items.length, "rule") : undefined}>
        {schedules.error && (
          <MoneyError
            title="Couldn’t load schedules"
            message={schedules.error}
            onRetry={schedules.reload}
          />
        )}
        {schedules.loading && !schedules.data && (
          <p className="moneyv-count" role="status">
            Loading schedules…
          </p>
        )}
        {schedules.data && schedules.data.items.length === 0 && (
          <MoneyEmpty prompt="Add a bill or income: " action="Ask to add one">
            No recurring rules yet.
          </MoneyEmpty>
        )}
        {schedules.data && schedules.data.items.length > 0 && (
          <>
            <div className="table-wrap" role="region" aria-label="All schedules" tabIndex={0}>
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
                  {pageSlice(schedules.data.items, safePage(schedulePage, schedules.data.items.length, PAGE_SIZE), PAGE_SIZE).map((schedule) => (
                    <tr key={schedule.id}>
                      <td>
                        {schedule.name}
                        {!schedule.confirmed && <span className="badge muted">expected</span>}
                        {!schedule.active && <span className="badge danger">archived</span>}
                      </td>
                      <td>
                        <span className="badge muted">{humanKind(schedule.subtype ?? schedule.kind)}</span>
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
            <MoneyPager
              page={safePage(schedulePage, schedules.data.items.length, PAGE_SIZE)}
              pageSize={PAGE_SIZE}
              total={schedules.data.items.length}
              noun="rules"
              onPage={setSchedulePage}
            />
          </>
        )}
      </MoneySection>
    </section>
  );
}
