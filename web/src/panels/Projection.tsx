import { useState } from "react";
import { MessageCircle } from "lucide-react";
import { askAgent } from "../ask";
import { formatDate, isoDate } from "../format";
import { Busy, Loading } from "../Loading";
import { ProjectionBody, type ProjectionPayload } from "../projection";
import { useApi } from "../useApi";

function queryPath(
  horizon: number,
  amountRupees: string,
  day: string,
  method: string,
  account: string,
): string {
  const days = Math.min(3650, Math.max(90, Math.round(horizon) || 90));
  const params = new URLSearchParams({ horizon_days: String(days) });
  if (amountRupees.trim()) {
    params.set("amount_paise", String(Math.round(Number(amountRupees) * 100)));
    params.set("date", day);
    params.set("method", method);
    if (account.trim()) params.set("account", account.trim());
  }
  return `/api/projection?${params.toString()}`;
}

export function ProjectionPanel() {
  const [horizon, setHorizon] = useState(90);
  const [amountRupees, setAmountRupees] = useState("");
  const [day, setDay] = useState(isoDate(new Date()));
  const [method, setMethod] = useState("account");
  const [account, setAccount] = useState("");
  const [path, setPath] = useState("/api/projection?horizon_days=90");
  const { data, error, loading, reload } = useApi<ProjectionPayload>(path);
  const hypothetical = Boolean(amountRupees.trim());

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>Does the floor hold?</h2>
          <p className="muted">
            A seeded walk over liquid balances: income, obligations, installments, card dues, goal
            contributions, and variable spend. The band is the P10–P90 spread of 1,000 futures.
          </p>
        </div>
        <button
          type="button"
          className="ask-link"
          onClick={() => askAgent("Explain this projection.")}
        >
          <MessageCircle size={13} aria-hidden="true" />
          Ask
        </button>
      </header>

      {loading && !data && <Loading label="Running the projection…" />}
      {error && (
        <div className="panel-error">
          <p className="error">{error}</p>
          <button type="button" className="ghost small" onClick={reload}>
            Retry
          </button>
        </div>
      )}

      {data && (
        <article className="projection-panel">
          <div className="section-head">
            <h3>{hypothetical ? "If you buy it" : "The next 90 days"}</h3>
            <span className="fine">
              {formatDate(data.as_of)} · {data.horizon_days} days
            </span>
            {loading && <Busy />}
          </div>
          <ProjectionBody payload={data} />
          {hypothetical && (
            <div className="button-row">
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  setAmountRupees("");
                  setPath(queryPath(horizon, "", day, method, account));
                }}
              >
                Clear the purchase
              </button>
            </div>
          )}
        </article>
      )}

      <details className="check-purchase">
        <summary>Check a purchase</summary>
        <form
          className="filters"
          onSubmit={(event) => {
            event.preventDefault();
            setPath(queryPath(horizon, amountRupees, day, method, account));
            window.setTimeout(() => {
              document
                .querySelector(".projection-panel")
                ?.scrollIntoView({ behavior: "smooth", block: "start" });
            }, 80);
          }}
        >
          <label>
            Horizon (days)
            <input
              type="number"
              min={90}
              max={3650}
              value={horizon}
              onChange={(event) => setHorizon(Number(event.target.value))}
            />
            <span className="fine">At least 90 days.</span>
          </label>
          <label>
            Amount (₹, optional)
            <input
              type="number"
              min={1}
              step="0.01"
              placeholder="68000"
              value={amountRupees}
              onChange={(event) => setAmountRupees(event.target.value)}
            />
          </label>
          <label>
            Purchase date
            <input
              type="date"
              value={day}
              onChange={(event) => setDay(event.target.value)}
              disabled={!amountRupees.trim()}
            />
          </label>
          <label>
            Method
            <select
              value={method}
              onChange={(event) => setMethod(event.target.value)}
              disabled={!amountRupees.trim()}
            >
              <option value="account">Bank account</option>
              <option value="card">Card</option>
              <option value="emi">Card EMI</option>
            </select>
          </label>
          <label>
            Card (optional)
            <input
              value={account}
              placeholder="Slice card"
              onChange={(event) => setAccount(event.target.value)}
              disabled={!amountRupees.trim() || method === "account"}
            />
          </label>
          <button type="submit" disabled={loading}>
            {loading ? (
              <>
                <Busy />
                Running…
              </>
            ) : hypothetical ? (
              "Check it"
            ) : (
              "Run"
            )}
          </button>
        </form>
      </details>
    </section>
  );
}
