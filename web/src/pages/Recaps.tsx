import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { askAgent } from "../ask";
import { formatDate, formatPaise, isoDate } from "../format";
import { Loading } from "../Loading";
import { goTo } from "../nav";
import { Orb } from "../Orb";
import { readExpenses, totalsBy, type Spending } from "../spending";

type Period = {
  id: string;
  kind: "Daily" | "Weekly" | "Monthly";
  title: string;
  start: string;
  end: string;
  /** The same-length period before, for the comparison. */
  previous: { start: string; end: string; label: string };
  tone: string;
};

function shift(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function monthName(date: Date): string {
  return date.toLocaleDateString("en-IN", { month: "long" });
}

/** The recap periods, newest first. Weeks start on Monday. */
function periods(now = new Date()): Period[] {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = shift(today, -1);
  const monday = shift(today, -((today.getDay() + 6) % 7));
  const lastMonday = shift(monday, -7);
  const monthStart = new Date(today.getFullYear(), today.getMonth(), 1);
  const lastMonthStart = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  const lastMonthEnd = shift(monthStart, -1);
  const twoMonthsStart = new Date(today.getFullYear(), today.getMonth() - 2, 1);
  const elapsed = Math.round((today.getTime() - monthStart.getTime()) / 86_400_000);
  return [
    {
      id: "yesterday",
      kind: "Daily",
      title: "Yesterday",
      start: isoDate(yesterday),
      end: isoDate(yesterday),
      previous: { start: isoDate(shift(yesterday, -7)), end: isoDate(shift(yesterday, -7)), label: "the same day last week" },
      tone: "cobalt",
    },
    {
      id: "this-week",
      kind: "Weekly",
      title: "This week so far",
      start: isoDate(monday),
      end: isoDate(today),
      previous: { start: isoDate(lastMonday), end: isoDate(shift(today, -7)), label: "the same days last week" },
      tone: "sun",
    },
    {
      id: "last-week",
      kind: "Weekly",
      title: `Week of ${formatDate(isoDate(lastMonday)).slice(0, 6)}`,
      start: isoDate(lastMonday),
      end: isoDate(shift(monday, -1)),
      previous: { start: isoDate(shift(lastMonday, -7)), end: isoDate(shift(lastMonday, -1)), label: "the week before" },
      tone: "sun",
    },
    {
      id: "this-month",
      kind: "Monthly",
      title: `${monthName(today)} so far`,
      start: isoDate(monthStart),
      end: isoDate(today),
      previous: {
        start: isoDate(lastMonthStart),
        end: isoDate(shift(lastMonthStart, elapsed)),
        label: `the same days of ${monthName(lastMonthStart)}`,
      },
      tone: "mesh",
    },
    {
      id: "last-month",
      kind: "Monthly",
      title: monthName(lastMonthStart),
      start: isoDate(lastMonthStart),
      end: isoDate(lastMonthEnd),
      previous: { start: isoDate(twoMonthsStart), end: isoDate(shift(lastMonthStart, -1)), label: monthName(twoMonthsStart) },
      tone: "mesh",
    },
  ];
}

type Story = { key: string; label: string; big: string; line: string; bars?: Array<{ name: string; paise: number }> };

/** Stories are sums over ledger rows; the sentences are templates, not model text. */
function buildStories(period: Period, current: Spending, previous: Spending): Story[] {
  const total = current.rows.reduce((sum, row) => sum + row.amount_paise, 0);
  const before = previous.rows.reduce((sum, row) => sum + row.amount_paise, 0);
  if (!current.rows.length) {
    return [
      {
        key: "empty",
        label: period.title,
        big: "₹0",
        line: "No spending recorded in this period. If you spent, it isn't in the ledger yet.",
      },
    ];
  }
  const change = before > 0 ? Math.round(((total - before) / before) * 100) : null;
  const comparison =
    change === null
      ? `Nothing was recorded for ${period.previous.label} to compare with.`
      : total * 100 < before
        ? `Under 1% of the ${formatPaise(before)} spent in ${period.previous.label}.`
        : change === 0
        ? `About the same as ${period.previous.label}.`
        : `${Math.abs(change)}% ${change < 0 ? "less" : "more"} than ${period.previous.label}.`;
  const categories = totalsBy(current.rows, (row) => row.category_name ?? "Uncategorized");
  const top = categories.slice(0, 4).map((entry) => ({ name: entry.key, paise: entry.paise }));
  const rest = categories.slice(4).reduce((sum, entry) => sum + entry.paise, 0);
  if (rest > 0) top.push({ name: "Everything else", paise: rest });
  const biggest = current.rows.reduce((max, row) => (row.amount_paise > max.amount_paise ? row : max));
  const payees = totalsBy(current.rows, (row) => row.payee_name ?? row.description);
  const frequent = [...payees].sort((a, b) => b.count - a.count)[0];
  const stories: Story[] = [
    { key: "total", label: "You spent", big: formatPaise(total), line: comparison },
    {
      key: "where",
      label: "Where it went",
      big: categories[0].key,
      line: `${formatPaise(categories[0].paise)} of ${formatPaise(total)}, across ${categories[0].count} ${
        categories[0].count === 1 ? "row" : "rows"
      }.`,
      bars: top,
    },
    {
      key: "biggest",
      label: "The biggest single spend",
      big: formatPaise(biggest.amount_paise),
      line: `${biggest.payee_name ?? biggest.description} on ${formatDate(biggest.date)}.`,
    },
  ];
  if (frequent && frequent.count > 1) {
    stories.push({
      key: "frequent",
      label: "Your most frequent",
      big: frequent.key,
      line: `${frequent.count} times, ${formatPaise(frequent.paise)} in all.`,
    });
  }
  return stories;
}

export function RecapsPage() {
  const list = useMemo(() => periods(), []);
  const [selected, setSelected] = useState(list[3].id);
  const [index, setIndex] = useState(0);
  const [state, setState] = useState<{
    id: string;
    stories: Story[] | null;
    rows: number;
    truncated: boolean;
    error: string | null;
  }>({ id: "", stories: null, rows: 0, truncated: false, error: null });
  const period = list.find((entry) => entry.id === selected) ?? list[0];

  useEffect(() => {
    let alive = true;
    void Promise.all([
      readExpenses(period.start, period.end),
      readExpenses(period.previous.start, period.previous.end),
    ])
      .then(([current, previous]) => {
        if (!alive) return;
        setState({
          id: period.id,
          stories: buildStories(period, current, previous),
          rows: current.rows.length,
          truncated: current.truncated,
          error: null,
        });
      })
      .catch((caught: unknown) => {
        if (alive) {
          setState({ id: period.id, stories: null, rows: 0, truncated: false, error: String(caught) });
        }
      });
    return () => {
      alive = false;
    };
  }, [period]);

  const loading = state.id !== period.id;
  const stories = loading ? null : state.stories;
  const story = stories?.[Math.min(index, stories.length - 1)];

  function choose(id: string) {
    setSelected(id);
    setIndex(0);
  }

  function step(delta: number) {
    if (!stories) return;
    setIndex((value) => Math.min(stories.length - 1, Math.max(0, value + delta)));
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight") step(1);
    if (event.key === "ArrowLeft") step(-1);
  }

  const peak = Math.max(...(story?.bars ?? []).map((bar) => bar.paise), 1);

  return (
    <section className="recaps">
      <aside className="recap-list" aria-label="Recaps">
        <h2>Recaps</h2>
        <p className="fine">Built from your ledger rows. Every figure is a sum you can open.</p>
        {(["Daily", "Weekly", "Monthly"] as const).map((kind) => (
          <div key={kind} className="recap-group">
            <span className="recap-kind">{kind}</span>
            {list
              .filter((entry) => entry.kind === kind)
              .map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className={entry.id === selected ? "recap-item on" : "recap-item"}
                  aria-current={entry.id === selected ? "true" : undefined}
                  onClick={() => choose(entry.id)}
                >
                  <span className={`recap-thumb ${entry.tone}`} aria-hidden="true" />
                  <span>
                    <b>{entry.title}</b>
                    <span>
                      {formatDate(entry.start)}
                      {entry.end !== entry.start ? ` – ${formatDate(entry.end)}` : ""}
                    </span>
                  </span>
                </button>
              ))}
          </div>
        ))}
      </aside>

      <div className="recap-stage">
        <button type="button" className="recap-nav" aria-label="Previous story" disabled={index === 0} onClick={() => step(-1)}>
          <ChevronLeft size={22} aria-hidden="true" />
        </button>
        <div
          className={`story ${period.tone}`}
          tabIndex={0}
          role="group"
          aria-roledescription="story"
          aria-label={`${period.title}, story ${index + 1} of ${stories?.length ?? 1}`}
          onKeyDown={onKey}
        >
          <div className="story-progress" aria-hidden="true">
            {(stories ?? [null]).map((_, position) => (
              <i key={position} className={position <= index ? "done" : ""} />
            ))}
          </div>
          <div className="story-head">
            <Orb size={28} />
            <b>Fin-Agent</b>
            <span>· {period.title}</span>
          </div>
          {loading ? (
            <div className="story-loading">
              <Loading label="Adding up the rows…" />
            </div>
          ) : state.error ? (
            <p className="story-line">The ledger couldn&apos;t be read: {state.error}</p>
          ) : story ? (
            <>
              <span className="story-label">{story.label}</span>
              <strong className={story.big.length > 9 ? "story-big long" : "story-big"}>{story.big}</strong>
              <p className="story-line">{story.line}</p>
              {story.bars && (
                <div className="story-bars">
                  {story.bars.map((bar) => (
                    <div key={bar.name} className="story-bar">
                      <span>{bar.name}</span>
                      <i style={{ width: `${Math.max(4, (bar.paise / peak) * 100)}%` }} />
                      <b>{formatPaise(bar.paise)}</b>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : null}
          <div className="story-foot">
            <button type="button" className="story-cite" onClick={() => goTo("activity", "ledger")}>
              From {state.rows.toLocaleString("en-IN")} ledger {state.rows === 1 ? "row" : "rows"}
              {state.truncated ? " (first 2,000)" : ""} · open the ledger
            </button>
            <button
              type="button"
              className="story-ask"
              onClick={() => askAgent(`About my spending ${period.title.toLowerCase()}: `)}
            >
              Ask about {period.title.toLowerCase()}…
            </button>
          </div>
        </div>
        <button
          type="button"
          className="recap-nav"
          aria-label="Next story"
          disabled={!stories || index >= stories.length - 1}
          onClick={() => step(1)}
        >
          <ChevronRight size={22} aria-hidden="true" />
        </button>
      </div>
    </section>
  );
}
