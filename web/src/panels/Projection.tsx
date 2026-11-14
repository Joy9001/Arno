import { useState, type FormEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Button } from "@radix-ui/themes";
import { AlertTriangle, Calculator } from "lucide-react";
import { Busy } from "../Loading";
import { formatDate, formatPaise, isoDate, percent } from "../format";
import { ProjectionBody, type ProjectionPayload } from "../projection";
import {
  lowAmount,
  OBLIGATION_WARNING,
  paymentMethodLabel,
  projectionReadiness,
} from "../projectionData";
import { useApi } from "../useApi";
import { Timeline } from "./Timeline";
import {
  MoneyAsk,
  MoneyError,
  MoneyPageHeader,
  MoneyPageSkeleton,
  MoneyRefreshing,
  MoneySection,
  MoneySummary,
} from "./MoneyBits";

type Method = "account" | "card" | "emi";

type Purchase = {
  amountPaise: number;
  date: string;
  method: Method;
  emiMonths?: number;
  account?: string;
};

type PurchaseMeta = {
  amount_paise?: number;
  date?: string;
  method?: string;
  emi_months?: number | null;
  account?: string | null;
  card_name?: string;
  due_date?: string | null;
  feasible?: boolean;
};

const MIN_HORIZON_DAYS = 90;
const MAX_HORIZON_DAYS = 3650;
const EMI_TENURES = [3, 6, 9, 12, 18, 24, 36, 48];
const PROJECTION_DESCRIPTION =
  "The ledger behind today and thousands of seeded futures ahead of it: income, obligations, instalments, card dues, goal contributions, and variable spend. The band is the P10–P90 spread; the floor probability is not a guarantee.";

const VERDICT: Record<string, { text: string; tone: string }> = {
  yes: { text: "Yes", tone: "good" },
  "yes-but": { text: "Yes, with some caution", tone: "warn" },
  no: { text: "No", tone: "bad" },
};

function queryPath(horizon: number, purchase: Purchase | null): string {
  const params = new URLSearchParams({ horizon_days: String(horizon) });
  if (purchase) {
    params.set("amount_paise", String(purchase.amountPaise));
    params.set("date", purchase.date);
    params.set("method", purchase.method);
    if (purchase.method === "emi") {
      params.set("emi_months", String(purchase.emiMonths ?? 3));
    }
    if (purchase.method !== "account" && purchase.account) {
      params.set("account", purchase.account);
    }
  }
  return `/api/projection?${params.toString()}`;
}

function validateHorizon(value: string): number | null {
  const days = Number(value);
  if (!Number.isInteger(days) || days < MIN_HORIZON_DAYS || days > MAX_HORIZON_DAYS) {
    return null;
  }
  return days;
}

/** Money's first view: the timeline, then the projection's figures, assumptions, and a purchase scenario. */
export function ProjectionPanel() {
  const [horizonDraft, setHorizonDraft] = useState(String(MIN_HORIZON_DAYS));
  const [horizon, setHorizon] = useState(MIN_HORIZON_DAYS);
  const [amountRupees, setAmountRupees] = useState("");
  const [day, setDay] = useState(isoDate(new Date()));
  const [method, setMethod] = useState<Method>("account");
  const [emiMonths, setEmiMonths] = useState("3");
  const [account, setAccount] = useState("");
  const [path, setPath] = useState(queryPath(MIN_HORIZON_DAYS, null));
  const [formError, setFormError] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi<ProjectionPayload>(path);
  const reducedMotion = useReducedMotion();
  const hypothetical = Boolean(amountRupees.trim());
  const today = isoDate(new Date());

  function runProjection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const nextHorizon = validateHorizon(horizonDraft);
    if (nextHorizon === null) {
      setFormError(`Enter a whole number of days from ${MIN_HORIZON_DAYS} to ${MAX_HORIZON_DAYS}.`);
      return;
    }

    let purchase: Purchase | null = null;
    if (amountRupees.trim()) {
      const rupees = Number(amountRupees);
      if (!Number.isFinite(rupees) || rupees <= 0) {
        setFormError("Enter a purchase amount above ₹0.");
        return;
      }
      if (!day) {
        setFormError("Choose a purchase date.");
        return;
      }
      if (day < today) {
        setFormError("Choose today or a later purchase date.");
        return;
      }
      const tenure = Number(emiMonths);
      if (method === "emi" && !EMI_TENURES.includes(tenure)) {
        setFormError("Choose an EMI tenure from the listed options.");
        return;
      }
      purchase = {
        amountPaise: Math.round(rupees * 100),
        date: day,
        method,
        emiMonths: method === "emi" ? tenure : undefined,
        account: method === "account" ? undefined : account.trim() || undefined,
      };
      if (purchase.amountPaise <= 0) {
        setFormError("Enter a purchase amount above ₹0.");
        return;
      }
    }

    setFormError(null);
    setHorizon(nextHorizon);
    setPath(queryPath(nextHorizon, purchase));
  }

  function clearPurchase() {
    setAmountRupees("");
    setFormError(null);
    setPath(queryPath(horizon, null));
  }

  if (loading && !data) {
    return (
      <MoneyPageSkeleton
        title="Timeline"
        description={PROJECTION_DESCRIPTION}
        chart
        rows={0}
      />
    );
  }

  const header = (
    <MoneyPageHeader
      title="Timeline"
      description={PROJECTION_DESCRIPTION}
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

  const floorPaise = data?.floor?.paise ?? data?.floor_paise ?? null;
  const floorSet = data ? (data.floor_set ?? (floorPaise ?? 0) > 0) : false;
  const floorText = data?.floor?.text ?? (floorPaise !== null ? formatPaise(floorPaise) : null);
  const medianLow = data ? lowAmount(data) : null;
  const lowPoint = data?.curve.find((point) => point.date === data.median_low.date);
  const purchase = data?.hypothetical as PurchaseMeta | undefined;
  const verdict = data?.verdict ? VERDICT[data.verdict] : null;
  const aboveThreshold = data ? data.floor_probability >= data.floor_threshold : false;
  const readiness = data ? projectionReadiness(data) : { ready: true, message: null };
  const gated = !readiness.ready;
  const staleBalances =
    data?.data_quality.balance_ages?.filter(
      (row) => row.known !== false && (row.age_days ?? 0) > 30,
    ) ?? [];
  const staleNote = staleBalances.length
    ? `Balance data is stale for ${staleBalances.map((row) => row.account).join(", ")}. The projection uses the recorded balances.`
    : null;

  return (
    <section className="panel">
      {header}
      {error && (
        <MoneyError
          title="Couldn’t run the projection"
          message={error}
          onRetry={reload}
        />
      )}

      {data && (
        <motion.div
          initial={reducedMotion ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
        >
          {!gated && verdict && (
            <div className="moneyv-verdict">
              <span className={`moneyv-chip ${verdict.tone}`}>{verdict.text}</span>
              {purchase?.feasible === false && <span className="moneyv-chip warn">Not feasible as entered</span>}
              {data.verdict_reason && <p className="moneyv-verdict-note">{data.verdict_reason}</p>}
            </div>
          )}

          {!gated && <Timeline payload={data} />}

          {gated && (
            <MoneySection title="Why there is no forecast" note="A required balance is missing">
              <ProjectionBody payload={data} />
            </MoneySection>
          )}

          {purchase?.amount_paise !== undefined && (
            <p className="moneyv-assumption">
              {formatPaise(purchase.amount_paise)} on {formatDate(purchase.date)} ·{" "}
              {paymentMethodLabel(purchase.method)}
              {purchase.method === "emi" && purchase.emi_months
                ? ` over ${purchase.emi_months} months`
                : ""}
              {purchase.card_name ? ` · ${purchase.card_name}` : ""}
              {purchase.due_date ? ` · bill due ${formatDate(purchase.due_date)}` : ""}
            </p>
          )}

          {!gated && (
            <MoneySummary
              label="Projection summary"
              cells={[
                floorSet
                  ? {
                      label: "Chance above floor",
                      value: percent(data.floor_probability),
                      note: `Threshold ${percent(data.floor_threshold)}${
                        data.runs ? ` · ${data.runs.toLocaleString("en-IN")} seeded futures` : " · seeded futures"
                      }`,
                      tone: aboveThreshold ? "positive" : "negative",
                    }
                  : {
                      label: "Chance above zero",
                      value: percent(data.floor_probability),
                      note: "No floor is set; this measures staying at or above ₹0",
                    },
                {
                  label: "Lowest median balance",
                  value: medianLow === null ? "—" : formatPaise(medianLow),
                  note: `On ${formatDate(data.median_low.date)}`,
                },
                {
                  label: "Floor",
                  value: floorSet && floorText ? floorText : "No floor",
                  note: floorSet
                    ? "Set in Profile"
                    : <MoneyAsk prompt="Set my financial floor." label="Ask to set one" />,
                },
                {
                  label: "10th–90th percentile",
                  value: lowPoint
                    ? `${formatPaise(lowPoint.p10_paise)} – ${formatPaise(lowPoint.p90_paise)}`
                    : "—",
                  note: lowPoint ? "Range at the lowest median point" : "Curve not available",
                },
              ]}
            />
          )}
          {!gated && (
            <p className="moneyv-facts">
              {data.first_floor_crossing
                ? `1 in 10 futures goes below ${floorSet ? "the floor" : "zero"} by ${formatDate(data.first_floor_crossing)}`
                : `Fewer than 1 in 10 futures go below ${floorSet ? "the floor" : "zero"} within the horizon`}
              {data.optimistic
                ? ` · with unconfirmed income: ${percent(data.optimistic.floor_probability)} above ${
                    floorSet ? "floor" : "zero"
                  } (optimistic only)`
                : ""}
            </p>
          )}
          {!gated && data.obligations_met === false && (
            <p className="moneyv-basis moneyv-warning">
              <AlertTriangle size={15} aria-hidden="true" /> {OBLIGATION_WARNING}
            </p>
          )}
          {staleNote && <p className="moneyv-basis moneyv-warning">{staleNote}</p>}

          <MoneySection title="Assumptions" note="From the projection engine">
            <ul className="evidence-list">
              <li>
                {data.horizon_days} days from {formatDate(data.as_of)}
              </li>
              {data.runs !== undefined && (
                <li>
                  {data.runs.toLocaleString("en-IN")} simulated runs ·{" "}
                  {percent(data.floor_threshold)} threshold
                </li>
              )}
              {purchase?.amount_paise !== undefined && (
                <li>
                  Purchase of {formatPaise(purchase.amount_paise)}
                  {purchase.date ? ` on ${formatDate(purchase.date)}` : ""}
                  {paymentMethodLabel(purchase.method)
                    ? ` · ${paymentMethodLabel(purchase.method)}`
                    : ""}
                  {purchase.emi_months ? ` over ${purchase.emi_months} months` : ""}
                </li>
              )}
              {!gated && data.optimistic && (
                <li>
                  Unconfirmed income is shown separately:{" "}
                  {percent(data.optimistic.floor_probability)} above{" "}
                  {floorSet ? "floor" : "zero"}, optimistic only
                </li>
              )}
              {!gated && data.obligations_met === false && <li>{OBLIGATION_WARNING}</li>}
            </ul>
          </MoneySection>

          {!gated && (
            <MoneySection title={data.hypothetical ? "If you buy it" : "The projection"} note={`Next ${data.horizon_days} days`}>
              <p className="moneyv-legend">
                <span>
                  <i aria-hidden="true" /> Median
                </span>
                <span>
                  <i className="band" aria-hidden="true" /> 10th–90th percentile
                </span>
                {floorSet && (
                  <span>
                    <i className="floor" aria-hidden="true" /> {floorText ?? "Floor"}
                  </span>
                )}
              </p>
              {/* The shared body's summary repeats the strip above; its chart, tradeoffs, goals,
                  data quality, and sources follow here. */}
              <div className="moneyv-projection-body">
                <ProjectionBody payload={data} />
              </div>
            </MoneySection>
          )}
        </motion.div>
      )}

      <MoneySection title="Try a purchase" note="Optional scenario">
        <p className="moneyv-basis">
          See how a purchase could change the projection. The payment method and EMI tenure are part
          of the assumptions.
        </p>
        <form className="moneyv-form" onSubmit={runProjection} noValidate>
          <label className="moneyv-field">
            <span>Amount (₹, optional)</span>
            <input
              type="number"
              min={1}
              step="0.01"
              placeholder="68000"
              value={amountRupees}
              onChange={(event) => setAmountRupees(event.target.value)}
            />
          </label>

          <label className="moneyv-field">
            <span>Purchase date</span>
            <input
              type="date"
              min={today}
              value={day}
              onChange={(event) => setDay(event.target.value)}
            />
          </label>

          <label className="moneyv-field">
            <span>Payment method</span>
            <select value={method} onChange={(event) => setMethod(event.target.value as Method)}>
              <option value="account">Bank account</option>
              <option value="card">Credit card</option>
              <option value="emi">Card EMI</option>
            </select>
          </label>

          {method === "emi" && (
            <label className="moneyv-field">
              <span>EMI tenure</span>
              <select
                value={emiMonths}
                onChange={(event) => setEmiMonths(event.target.value)}
                aria-describedby="moneyv-emi-hint"
              >
                {EMI_TENURES.map((months) => (
                  <option key={months} value={months}>
                    {months} months
                  </option>
                ))}
              </select>
              <span className="moneyv-field-hint" id="moneyv-emi-hint">
                Instalments in months; 3 by default.
              </span>
            </label>
          )}

          {method !== "account" && (
            <label className="moneyv-field">
              <span>Card name (optional)</span>
              <input
                value={account}
                placeholder="Best available card"
                onChange={(event) => setAccount(event.target.value)}
              />
            </label>
          )}

          <label className="moneyv-field">
            <span>Projection horizon (days)</span>
            <input
              type="number"
              min={MIN_HORIZON_DAYS}
              max={MAX_HORIZON_DAYS}
              step={1}
              value={horizonDraft}
              onChange={(event) => setHorizonDraft(event.target.value)}
              aria-describedby="moneyv-horizon-hint"
            />
            <span className="moneyv-field-hint" id="moneyv-horizon-hint">
              {MIN_HORIZON_DAYS}–{MAX_HORIZON_DAYS} days. A later purchase date extends the horizon.
            </span>
          </label>

          <div className="moneyv-form-actions">
            <Button type="submit" disabled={loading}>
              {loading ? (
                <>
                  <Busy />
                  Updating…
                </>
              ) : (
                <>
                  <Calculator size={15} strokeWidth={1.8} aria-hidden="true" />
                  {hypothetical ? "Check this purchase" : "Update projection"}
                </>
              )}
            </Button>
            {data?.hypothetical && (
              <button type="button" className="ghost small" onClick={clearPurchase}>
                Clear the purchase
              </button>
            )}
          </div>
        </form>
        {formError && (
          <p className="moneyv-error" role="alert">
            {formError}
          </p>
        )}
      </MoneySection>
    </section>
  );
}
