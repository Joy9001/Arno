import { useState } from "react";
import { formatDate, formatPaise } from "../format";
import type { Person } from "../types";
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
} from "./MoneyBits";
import { countLabel, pageSlice, safePage } from "./moneyView";

type People = { people: Person[]; as_of: string };
const PAGE_SIZE = 20;
const PEOPLE_DESCRIPTION =
  "Shared balances, grouped by who owes whom.";

const DIRECTION_LABEL: Record<Person["direction"], string> = {
  owes_you: "They owe you",
  you_owe: "You owe them",
  settled: "Settled up",
};

function personKind(kind: string): string {
  if (kind === "person") return "Person";
  if (kind === "organization") return "Organisation";
  return kind;
}

function PersonRow({ person }: { person: Person }) {
  const detail = [
    personKind(person.kind),
    person.upi_id,
    person.next_expected_date ? `expected ${formatDate(person.next_expected_date)}` : null,
    person.account_active === false ? "ledger account archived" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <article className={`person-tile ${person.direction}`}>
      <span className="person-avatar" aria-hidden="true">
        {person.display_name.trim().charAt(0).toUpperCase()}
      </span>
      <div className="moneyv-row-main">
        <strong>{person.display_name}</strong>
        <span className="moneyv-meta">{detail}</span>
      </div>
      <div className="person-balance">
        <strong>{person.direction === "settled" ? "Settled" : formatPaise(Math.abs(person.balance_paise))}</strong>
        <span className="moneyv-meta">{DIRECTION_LABEL[person.direction]}</span>
      </div>
      <MoneyAsk prompt={`Tell me about ${person.display_name} and what we owe each other.`} />
    </article>
  );
}

function PeopleGroup({
  title,
  people,
  page,
  onPage,
}: {
  title: string;
  people: Person[];
  page: number;
  onPage: (page: number) => void;
}) {
  if (people.length === 0) return null;
  const currentPage = safePage(page, people.length, PAGE_SIZE);
  return (
    <MoneySection title={title} note={countLabel(people.length, "person", "people")}>
      <div className="people-grid">
        {pageSlice(people, currentPage, PAGE_SIZE).map((person) => (
          <PersonRow key={person.payee_id} person={person} />
        ))}
      </div>
      <MoneyPager
        page={currentPage}
        pageSize={PAGE_SIZE}
        total={people.length}
        noun="people"
        onPage={onPage}
      />
    </MoneySection>
  );
}

export function PeoplePanel() {
  const { data, error, loading, reload } = useApi<People>("/api/people");
  const [owedPage, setOwedPage] = useState(0);
  const [owePage, setOwePage] = useState(0);
  const [settledPage, setSettledPage] = useState(0);

  if (loading && !data) {
    return (
      <MoneyPageSkeleton
        title="People"
        description={PEOPLE_DESCRIPTION}
        summary={3}
        rows={5}
      />
    );
  }

  const header = (
    <MoneyPageHeader
      title="People"
      description={PEOPLE_DESCRIPTION}
      aside={
        data ? (
          <>
            As of {formatDate(data.as_of)}
            {loading && <MoneyRefreshing />}
          </>
        ) : undefined
      }
    />
  );

  if (!data) {
    return (
      <section className="panel">
        {header}
        <MoneyError
          title="Couldn’t load people"
          message={error ?? "The people request did not return data."}
          onRetry={reload}
        />
      </section>
    );
  }

  const people = data.people;
  const theyOwe = people.filter((person) => person.direction === "owes_you");
  const youOwe = people.filter((person) => person.direction === "you_owe");
  const settled = people.filter((person) => person.direction === "settled");
  const owed = theyOwe.reduce((total, person) => total + Math.abs(person.balance_paise), 0);
  const owe = youOwe.reduce((total, person) => total + Math.abs(person.balance_paise), 0);

  return (
    <section className="panel">
      {header}
      {error && <MoneyError title="Couldn’t refresh people" message={error} onRetry={reload} />}

      {people.length > 0 && (
        <dl className="people-totals" aria-label="Balances with people and organizations">
          <div><dt>Owed to you</dt><dd>{formatPaise(owed)}</dd></div>
          <div><dt>You owe</dt><dd>{formatPaise(owe)}</dd></div>
          <div><dt>Net</dt><dd>{formatPaise(owed - owe)}</dd></div>
        </dl>
      )}

      {people.length === 0 ? (
        <MoneyEmpty prompt="Add a person: " action="Ask to add one">
          No people or organisations yet.
        </MoneyEmpty>
      ) : (
        <div className="people-groups">
          <PeopleGroup title="They owe you" people={theyOwe} page={owedPage} onPage={setOwedPage} />
          <PeopleGroup title="You owe them" people={youOwe} page={owePage} onPage={setOwePage} />
          <PeopleGroup
            title="Settled"
            people={settled}
            page={settledPage}
            onPage={setSettledPage}
          />
        </div>
      )}
    </section>
  );
}
