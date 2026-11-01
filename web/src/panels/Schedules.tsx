import { formatDate, formatPaise } from "../format";
import type { Schedule, Upcoming } from "../types";
import { useApi } from "../useApi";

type ScheduleList = { items: Schedule[]; as_of: string };

export function SchedulesPanel() {
  const schedules = useApi<ScheduleList>("/api/schedules");
  const upcoming = useApi<Upcoming>("/api/schedules/upcoming?days=90");

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Schedules</p>
          <h2>What is coming</h2>
          <p className="muted">
            Income, obligations, family transfers, instalment plans, and goal contributions. Rules
            are iCalendar RRULEs, adjusted for Indian weekends and holidays.
          </p>
        </div>
      </header>

      <h3 className="section-title">Next 90 days</h3>
      {upcoming.error && <p className="error">{upcoming.error}</p>}
      {upcoming.data && upcoming.data.events.length === 0 && (
        <p className="muted">Nothing scheduled in the next 90 days.</p>
      )}
      {upcoming.data && upcoming.data.events.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>What</th>
                <th>Kind</th>
                <th>Amount</th>
                <th>Account</th>
              </tr>
            </thead>
            <tbody>
              {upcoming.data.events.map((event, index) => (
                <tr key={`${event.date}-${event.name}-${index}`}>
                  <td>{formatDate(event.date)}</td>
                  <td>
                    {event.name}
                    {!event.confirmed && (
                      <>
                        {" "}
                        <span className="badge muted">expected</span>
                      </>
                    )}
                  </td>
                  <td>
                    <span className="badge muted">{event.subtype ?? event.kind}</span>
                  </td>
                  <td className="num">{formatPaise(event.amount_paise)}</td>
                  <td>{event.account_name ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className="section-title">All schedules</h3>
      {schedules.error && <p className="error">{schedules.error}</p>}
      {schedules.data && schedules.data.items.length === 0 && <p className="muted">No schedules yet.</p>}
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
                    {!schedule.confirmed && (
                      <>
                        {" "}
                        <span className="badge muted">expected</span>
                      </>
                    )}
                    {!schedule.active && (
                      <>
                        {" "}
                        <span className="badge danger">archived</span>
                      </>
                    )}
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
    </section>
  );
}
