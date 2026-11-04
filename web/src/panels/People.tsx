import { MessageCircle } from "lucide-react";
import { askAgent } from "../ask";
import { formatDate, formatPaise } from "../format";
import { Loading } from "../Loading";
import type { Person } from "../types";
import { useApi } from "../useApi";
type People = { people: Person[]; as_of: string };

function PersonRow({ person }: { person: Person }) {
  return (
    <article className="person-row">
      <div>
        <strong>{person.display_name}</strong>
        <span className="fine">
          {person.kind}
          {person.upi_id ? ` · ${person.upi_id}` : ""}
          {person.next_expected_date ? ` · expected ${formatDate(person.next_expected_date)}` : ""}
        </span>
      </div>
      <span className={person.balance_paise < 0 ? "person-amount negative" : "person-amount"}>
        {formatPaise(Math.abs(person.balance_paise))}
      </span>
      <button
        type="button"
        className="ask-link"
        onClick={() => askAgent(`Tell me about ${person.display_name}.`)}
      >
        <MessageCircle size={13} aria-hidden="true" />
        Ask
      </button>
    </article>
  );
}

export function PeoplePanel() {
  const { data, error, loading, reload } = useApi<People>("/api/people");
  const people = data?.people ?? [];
  const owed = people
    .filter((person) => person.balance_paise > 0)
    .reduce((total, person) => total + person.balance_paise, 0);
  const owe = people
    .filter((person) => person.balance_paise < 0)
    .reduce((total, person) => total + Math.abs(person.balance_paise), 0);
  const theyOwe = people.filter((person) => person.direction === "owes_you");
  const youOwe = people.filter((person) => person.direction === "you_owe");
  const settled = people.filter((person) => person.direction === "settled");

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>Who owes what</h2>
          <p className="muted">
            Every person and organisation has a ledger account. A positive balance means they owe
            you; a negative one means you owe them.
          </p>
        </div>
        {data && <p className="fine">as of {data.as_of}</p>}
      </header>

      {people.length > 0 && (
        <div className="summary-strip">
          <div className="summary-cell">
            <span>Owed to you</span>
            <strong>{formatPaise(owed)}</strong>
          </div>
          <div className="summary-cell">
            <span>You owe</span>
            <strong>{formatPaise(owe)}</strong>
          </div>
          <div className="summary-cell">
            <span>Net</span>
            <strong className={owed - owe < 0 ? "negative" : "positive"}>
              {formatPaise(owed - owe)}
            </strong>
          </div>
        </div>
      )}

      {loading && !data && <Loading label="Loading people…" />}
      {error && (
        <div className="panel-error">
          <p className="error">{error}</p>
          <button type="button" className="ghost small" onClick={reload}>
            Retry
          </button>
        </div>
      )}
      {data && people.length === 0 && (
        <div className="empty-state">
          <p>No people or organisations yet.</p>
          <button type="button" className="ask-link" onClick={() => askAgent("Add a person: ")}>
            <MessageCircle size={13} aria-hidden="true" />
            Ask to add one
          </button>
        </div>
      )}

      {theyOwe.length > 0 && (
        <>
          <div className="section-head">
            <h3>They owe you</h3>
          </div>
          <div className="people-list">
            {theyOwe.map((person) => (
              <PersonRow key={person.payee_id} person={person} />
            ))}
          </div>
        </>
      )}

      {youOwe.length > 0 && (
        <>
          <div className="section-head">
            <h3>You owe them</h3>
          </div>
          <div className="people-list">
            {youOwe.map((person) => (
              <PersonRow key={person.payee_id} person={person} />
            ))}
          </div>
        </>
      )}

      {settled.length > 0 && (
        <details className="settled">
          <summary>Settled ({settled.length})</summary>
          <div className="people-list">
            {settled.map((person) => (
              <PersonRow key={person.payee_id} person={person} />
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
