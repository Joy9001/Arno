import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { formatDate, formatPaise, isoDate, percent } from "../format";
import { Busy } from "../Loading";
import { Orb } from "../Orb";
import { ProjectionChart, type ProjectionPayload } from "../projection";
import { lowAmount, projectionReadiness } from "../projectionData";
import { useApi } from "../useApi";

type Method = "account" | "card" | "emi";

const METHODS: Array<{ id: Method; label: string }> = [
  { id: "account", label: "From an account" },
  { id: "card", label: "Card in full" },
  { id: "emi", label: "Card EMI" },
];
const EMI_TENURES = [3, 6, 9, 12, 18, 24];
const MAX_DAYS = 60;
const VERDICT: Record<string, string> = { yes: "Yes", "yes-but": "Yes, with some caution", no: "No" };

type Purchase = { amount_paise?: number; date?: string; method?: string; emi_months?: number | null };

function addDays(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return isoDate(date);
}

function daysFromToday(value: string | undefined): number {
  if (!value) return 0;
  const diff = Math.round(
    (new Date(`${value}T00:00:00`).getTime() - new Date(`${isoDate(new Date())}T00:00:00`).getTime()) /
      86_400_000,
  );
  return Math.min(MAX_DAYS, Math.max(0, diff));
}

/** Debounces a value so a dragged slider asks the engine once it rests. */
function useSettled<T>(value: T, delay = 350): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const handle = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(handle);
  }, [value, delay]);
  return settled;
}

/**
 * A what-if tool on the canvas: price, date, and payment method run through the projection
 * engine (`/api/projection` with a hypothetical). The model computes nothing here.
 */
export function PlannerPanel({ seed, onClose }: { seed: ProjectionPayload; onClose: () => void }) {
  const start = (seed.hypothetical ?? {}) as Purchase;
  const [rupees, setRupees] = useState(Math.round((start.amount_paise ?? 5_000_000) / 100));
  const [day, setDay] = useState(daysFromToday(start.date));
  const [method, setMethod] = useState<Method>(
    start.method === "card" || start.method === "emi" ? start.method : "account",
  );
  const [emi, setEmi] = useState(start.emi_months ?? 3);
  const query = useSettled({ rupees, day, method, emi });
  const params = new URLSearchParams({
    horizon_days: String(Math.max(90, seed.horizon_days)),
    amount_paise: String(query.rupees * 100),
    date: addDays(query.day),
    method: query.method,
  });
  if (query.method === "emi") params.set("emi_months", String(query.emi));
  const result = useApi<ProjectionPayload>(`/api/projection?${params.toString()}`);
  const data = result.data;
  const readiness = data ? projectionReadiness(data) : null;
  const floorSet = data ? (data.floor_set ?? (data.floor_paise ?? 0) > 0) : false;
  const low = data ? lowAmount(data) : null;
  const probability = data?.floor_probability ?? 0;
  const arc = Math.round(Math.min(1, Math.max(0, probability)) * 360);

  return (
    <section className="planner" aria-label="Purchase planner">
      <header className="planner-head">
        <Orb size={22} live={result.loading} thinking={result.loading} />
        <h2>Purchase planner</h2>
        {result.loading && <Busy />}
        <button type="button" className="planner-close" aria-label="Close the planner" onClick={onClose}>
          <X size={16} aria-hidden="true" />
        </button>
      </header>

      <div className="planner-controls">
        <label className="planner-field">
          <span>
            Price <b>{formatPaise(rupees * 100)}</b>
          </span>
          <input
            type="range"
            min={1000}
            max={500000}
            step={1000}
            value={rupees}
            onChange={(event) => setRupees(Number(event.target.value))}
          />
          <span className="planner-ticks">
            <i>₹1,000</i>
            <i>₹5,00,000</i>
          </span>
        </label>
        <label className="planner-field">
          <span>
            Buy on <b>{formatDate(addDays(day))}</b>
          </span>
          <input
            type="range"
            min={0}
            max={MAX_DAYS}
            step={1}
            value={day}
            onChange={(event) => setDay(Number(event.target.value))}
          />
          <span className="planner-ticks">
            <i>Today</i>
            <i>In {MAX_DAYS} days</i>
          </span>
        </label>
        <div className="planner-field">
          <span>Pay with</span>
          <div className="planner-seg" role="radiogroup" aria-label="Payment method">
            {METHODS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={method === item.id}
                className={method === item.id ? "on" : ""}
                onClick={() => setMethod(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {method === "emi" && (
            <div className="planner-seg small" role="radiogroup" aria-label="EMI tenure">
              {EMI_TENURES.map((months) => (
                <button
                  key={months}
                  type="button"
                  role="radio"
                  aria-checked={emi === months}
                  className={emi === months ? "on" : ""}
                  onClick={() => setEmi(months)}
                >
                  {months} mo
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {result.error && <p className="error">{result.error}</p>}

      {data && readiness && !readiness.ready && <p className="planner-gate">{readiness.message}</p>}

      {data && readiness?.ready && (
        <div className="planner-result">
          <div className="planner-gauge" style={{ ["--arc" as string]: `${arc}deg` }}>
            <strong>{percent(probability)}</strong>
            <span>{floorSet ? "chance the floor holds" : "chance of staying above ₹0"}</span>
          </div>
          <p className="planner-verdict">
            {data.verdict ? VERDICT[data.verdict] : floorSet ? "—" : "No floor set"}
            {data.verdict_reason && <span>{data.verdict_reason}</span>}
          </p>
          <dl className="planner-facts">
            <div>
              <dt>Lowest median</dt>
              <dd>{low === null ? "—" : formatPaise(low)}</dd>
            </div>
            <div>
              <dt>Tightest day</dt>
              <dd>{formatDate(data.median_low.date)}</dd>
            </div>
            <div>
              <dt>Threshold</dt>
              <dd>{percent(data.floor_threshold)}</dd>
            </div>
          </dl>
          <ProjectionChart payload={data} height={170} />
          <p className="planner-foot">
            Computed by the projection engine
            {data.runs ? ` · ${data.runs.toLocaleString("en-IN")} runs per change` : ""}. Not advice;
            the agent never moves money.
          </p>
        </div>
      )}
    </section>
  );
}
