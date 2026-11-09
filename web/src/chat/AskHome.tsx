import { useContext, useState, type ReactNode } from "react";
import { ArrowUpRight, Check, Play } from "lucide-react";
import { errorMessage } from "../api";
import { formatDate, formatPaise, percent } from "../format";
import { Busy } from "../Loading";
import { goTo, UserContext } from "../nav";
import { Orb } from "../Orb";
import { KIND_LABELS, markNotificationRead, openNotification } from "../notifications";
import { lowAmount, projectionReadiness } from "../projectionData";
import type { ProjectionPayload } from "../projection";
import type { CashQuality, Movement, NotificationItem } from "../types";
import { useApi } from "../useApi";
import { FirstRun, FirstRunStarters } from "./FirstRun";
import { firstRunSteps } from "./firstRunSteps";

/** `/api/home`: cash on hand, the projection, unread notifications, and recent movements. */
export type HomePayload = {
  as_of: string;
  spendable_paise: number;
  cash_quality: CashQuality;
  projection: ProjectionPayload;
  unread_count: number;
  needs_you: NotificationItem[];
  recent: Movement[];
  /** How much the ledger holds; an empty one is a first run. */
  ledger: { movements: number; spending: number; first_date: string | null };
};

function partOfDay(hour: number): string {
  if (hour < 5) return "Still up";
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** The agent's home: the new-conversation view, above the composer. */
export function AskGreeting() {
  const user = useContext(UserContext);
  // "You" is the default display name, not a name to greet.
  const first = user?.display_name?.trim().split(/\s+/)[0];
  const name = first && first.toLowerCase() !== "you" ? first : undefined;
  return (
    <div className="ask-hero">
      <div className="ask-halo">
        <Orb size={88} live />
      </div>
      <h3 className="ask-title">
        {partOfDay(new Date().getHours())}
        {name ? `, ${name}` : ""}
      </h3>
      <p className="ask-sub">Ask me anything about your money.</p>
    </div>
  );
}

function NoticedCards({ starters }: { starters: ReactNode }) {
  const home = useApi<HomePayload>("/api/home?horizon_days=90");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!home.data) {
    return home.error ? (
      <>
        {starters}
        <p className="ask-noticed-error">{home.error}</p>
      </>
    ) : null;
  }
  const data = home.data;
  // Until cash, the forecast, and recaps all have something to show, the home says what each needs.
  const firstRun = firstRunSteps(data).some((step) => !step.ready);

  async function act(item: NotificationItem, open: boolean) {
    setBusy(item.id);
    setError(null);
    try {
      if (open) await openNotification(item);
      else await markNotificationRead(item);
      home.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  const view = (notificationsOnly: boolean) => (
    <NoticedView
      data={data}
      busy={busy}
      error={error}
      notificationsOnly={notificationsOnly}
      onAct={(item, open) => void act(item, open)}
    />
  );
  if (!firstRun) {
    return (
      <>
        {starters}
        {view(false)}
      </>
    );
  }
  return (
    <>
      {data.ledger.movements === 0 ? <FirstRunStarters /> : starters}
      <FirstRun data={data} />
      {view(true)}
    </>
  );
}

function cashGate(cash: CashQuality): string {
  if (cash.status === "no_accounts") {
    return "I need a liquid account to start from. Add a bank, cash, or wallet account with its balance.";
  }
  const names = cash.unknown_accounts.slice(0, 2).join(", ");
  const more = cash.unknown_accounts.length > 2 ? ` and ${cash.unknown_accounts.length - 2} more` : "";
  return `I need a known starting balance. Add a statement balance or import a statement${
    names ? ` for ${names}${more}` : ""
  }.`;
}

/** What the agent noticed, from `/api/home`. Every figure is a field of that payload. */
export function NoticedView({
  data,
  busy = null,
  error = null,
  onAct = () => {},
  notificationsOnly = false,
}: {
  data: HomePayload;
  busy?: string | null;
  error?: string | null;
  onAct?: (item: NotificationItem, open: boolean) => void;
  /** Only the unread cards, under the first-run list. */
  notificationsOnly?: boolean;
}) {
  const projection = data.projection;
  const readiness = projectionReadiness(projection);
  const cashKnown = data.cash_quality.status === "known";
  const floorPaise = projection.floor?.paise ?? projection.floor_paise ?? null;
  const floorSet = projection.floor_set ?? (floorPaise ?? 0) > 0;
  const holds = projection.floor_probability >= projection.floor_threshold;
  const low = lowAmount(projection);
  const unread = data.needs_you.filter((item) => !item.read_at).slice(0, 2);
  const forecast = readiness.ready && cashKnown;
  const unreadCards = unread.map((item) => (
          <article key={item.id} className={item.kind === "reminder" ? "ask-card due" : "ask-card"}>
            <span className="ask-card-k">{KIND_LABELS[item.kind] ?? item.kind.replaceAll("_", " ")}</span>
            <strong className="ask-card-title">{item.title}</strong>
            <div className="ask-card-actions">
              <button
                type="button"
                className="pill-button light"
                disabled={busy === item.id}
                onClick={() => onAct(item, true)}
              >
                {busy === item.id ? <Busy /> : <ArrowUpRight size={14} aria-hidden="true" />}
                {item.kind === "new_subscription" ? "Decide" : "Open"}
              </button>
              <button
                type="button"
                className="pill-button"
                disabled={busy === item.id}
                onClick={() => onAct(item, false)}
              >
                <Check size={14} aria-hidden="true" />
                Mark read
              </button>
            </div>
          </article>
  ));

  if (notificationsOnly) {
    return unread.length ? (
      <section className="ask-noticed" aria-label="Needs you">
        {error && (
          <p className="ask-noticed-error" role="alert">
            {error}
          </p>
        )}
        <div className="ask-cards">{unreadCards}</div>
      </section>
    ) : null;
  }

  return (
    <section className="ask-noticed" aria-label="What I noticed">
      <p className="ask-noticed-head">
        <Orb size={16} />I noticed {unread.length ? `${unread.length + 2} things` : "two things"}
      </p>
      {error && (
        <p className="ask-noticed-error" role="alert">
          {error}
        </p>
      )}
      <div className="ask-cards">
        <article className="ask-card mesh">
          <span className="ask-card-k">Cash on hand</span>
          <strong className="ask-card-fig">
            {cashKnown
              ? formatPaise(data.spendable_paise)
              : data.cash_quality.status === "no_accounts"
                ? "No accounts"
                : "Unknown"}
          </strong>
          <p>
            {cashKnown
              ? `Liquid balances as of ${formatDate(data.as_of)}. Not the same as free to spend.`
              : cashGate(data.cash_quality)}
          </p>
          <button type="button" className="pill-button ink" onClick={() => goTo("money", "accounts")}>
            See the accounts
          </button>
        </article>

        {!forecast ? (
          <article className="ask-card">
            <span className="ask-card-k">No forecast yet</span>
            <strong className="ask-card-fig small">Projection waits</strong>
            <p>{readiness.message ?? cashGate(data.cash_quality)}</p>
            <button type="button" className="pill-button light" onClick={() => goTo("money", "accounts")}>
              Fix it
            </button>
          </article>
        ) : floorSet ? (
          <article className={holds ? "ask-card" : "ask-card due"}>
            <span className="ask-card-k">Floor holds · next {projection.horizon_days} days</span>
            <strong className="ask-card-fig">{percent(projection.floor_probability)}</strong>
            <p>
              Lowest median {low === null ? "—" : formatPaise(low)} on{" "}
              {formatDate(projection.median_low.date)}.
            </p>
            <button type="button" className="pill-button light" onClick={() => goTo("money", "timeline")}>
              Open the timeline
            </button>
          </article>
        ) : (
          <article className="ask-card">
            <span className="ask-card-k">No floor set</span>
            <strong className="ask-card-fig small">{low === null ? "—" : formatPaise(low)}</strong>
            <p>
              Your lowest median balance, on {formatDate(projection.median_low.date)}. Set a floor and
              I&apos;ll check every purchase against it.
            </p>
            <button type="button" className="pill-button light" onClick={() => goTo("settings", "profile")}>
              Set a floor
            </button>
          </article>
        )}

        {unreadCards}

        {unread.length < 2 && (
          <article className="ask-card recap">
            <span className="ask-recap-thumb" aria-hidden="true" />
            <span className="ask-card-k">Recaps</span>
            <strong className="ask-card-fig small">Your month so far</strong>
            <p>Daily, weekly, and monthly stories, added up from your ledger.</p>
            <button type="button" className="pill-button lime" onClick={() => goTo("recaps")}>
              <Play size={13} aria-hidden="true" />
              Watch
            </button>
          </article>
        )}
      </div>
    </section>
  );
}

/** Below the composer on the agent's home: starter prompts, then what the agent noticed. */
export function AskBelow({ starters }: { starters: ReactNode }) {
  return <NoticedCards starters={starters} />;
}
