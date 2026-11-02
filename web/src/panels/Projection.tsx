import { useState } from "react";
import { formatDate } from "../format";
import { ProjectionBody, type ProjectionPayload } from "../projection";
import { useApi } from "../useApi";

function queryPath(
  horizon: number,
  amountRupees: string,
  day: string,
  method: string,
  account: string,
): string {
  const params = new URLSearchParams({ horizon_days: String(horizon) });
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
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10));
  const [method, setMethod] = useState("account");
  const [account, setAccount] = useState("");
  const [path, setPath] = useState("/api/projection?horizon_days=90");
  const { data, error, loading, reload } = useApi<ProjectionPayload>(path);

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Projection</p>
          <h2>Cash against your floor</h2>
          <p className="muted">
            A seeded walk over liquid balances: income, obligations, installments, card dues, goal
            contributions, and variable spend. The band is the P10–P90 spread of 1,000 futures.
          </p>
        </div>
        <button type="button" className="ghost" onClick={reload}>
          Refresh
        </button>
      </header>

      <form
        className="filters"
        onSubmit={(event) => {
          event.preventDefault();
          setPath(queryPath(horizon, amountRupees, day, method, account));
        }}
      >
        <label>
          Horizon (days)
          <input
            type="number"
            min={1}
            max={3650}
            value={horizon}
            onChange={(event) => setHorizon(Number(event.target.value))}
          />
        </label>
        <label>
          Hypothetical amount (₹, optional)
          <input
            type="number"
            min={1}
            step="0.01"
            placeholder="200000"
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
        <button type="submit">Run</button>
      </form>

      {loading && !data && <p className="muted">Running the projection…</p>}
      {error && <p className="error">{error}</p>}
      {data && (
        <article className="projection-panel">
          <div className="panel-head">
            <div>
              <h3>{data.verdict ? "Verdict and projection" : "Projection"}</h3>
              <p className="fine">
                {formatDate(data.as_of)} · {data.horizon_days} days ·{" "}
                {Math.round(data.floor_probability * 100)}% above floor
              </p>
            </div>
          </div>
          <ProjectionBody payload={data} />
        </article>
      )}
    </section>
  );
}
