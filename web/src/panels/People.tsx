import { formatDate, formatPaise } from "../format";
import type { Person } from "../types";
import { useApi } from "../useApi";

type People = { people: Person[]; as_of: string };

function directionLabel(person: Person): string {
  if (person.direction === "owes_you") return "owes you";
  if (person.direction === "you_owe") return "you owe";
  return "settled";
}

export function PeoplePanel() {
  const { data, error, loading } = useApi<People>("/api/people");

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">People</p>
          <h2>Who owes what</h2>
          <p className="muted">
            Every person and organisation has a ledger account. A positive balance means they owe
            you; a negative one means you owe them.
          </p>
        </div>
        {data && <p className="fine">as of {data.as_of}</p>}
      </header>

      {loading && !data && <p className="muted">Loading people…</p>}
      {error && <p className="error">{error}</p>}
      {data && data.people.length === 0 && <p className="muted">No people or organisations yet.</p>}
      {data && data.people.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Kind</th>
                <th>Balance</th>
                <th>Direction</th>
                <th>Expected</th>
              </tr>
            </thead>
            <tbody>
              {data.people.map((person) => (
                <tr key={person.payee_id}>
                  <td>
                    {person.display_name}
                    {person.upi_id && <span className="fine"> · {person.upi_id}</span>}
                  </td>
                  <td>
                    <span className="badge muted">{person.kind}</span>
                  </td>
                  <td className={`num ${person.balance_paise < 0 ? "negative" : ""}`}>
                    {formatPaise(person.balance_paise)}
                  </td>
                  <td>{directionLabel(person)}</td>
                  <td>{formatDate(person.next_expected_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
