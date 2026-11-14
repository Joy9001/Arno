import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { FileSearch, SlidersHorizontal } from "lucide-react";
import { openPlanner } from "./evidence";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { TOOLTIP_PROPS } from "./chartTheme";
import { formatPaise } from "./format";
import { Orb } from "./Orb";
import {
  dataQualityNotes,
  lowAmount,
  OBLIGATION_WARNING,
  paymentMethodLabel,
  projectionReadiness,
} from "./projectionData";
import { SourceDetail } from "./SourceDetail";

export type Money = { paise: number; text: string };

export type ProjectionPoint = {
  date: string;
  median_paise: number;
  p10_paise: number;
  p90_paise: number;
};

export type ProjectionSource = {
  kind: string;
  id: string;
  name: string;
  detail?: string;
  amount_paise?: number;
};

export type ProjectionGoal = {
  id: string;
  name: string;
  target?: Money | null;
  saved?: Money | null;
  remaining_after_plan?: Money | null;
  deadline: string | null;
  completes_on: string | null;
  slips_days: number | null;
  reached: boolean;
};

export type ProjectionTradeoff = {
  kind: string;
  detail: string;
  date?: string;
  days?: number;
  method?: string;
  account?: string | null;
  due_date?: string | null;
  slips_days?: number | null;
  until?: string;
  categories?: { category: string; percent: number }[];
};

export type ProjectionDataQuality = {
  balance_ages?: {
    account: string;
    as_of: string | null;
    age_days: number | null;
    amount_paise?: number;
    known?: boolean;
  }[];
  baseline_sources?: {
    category: string;
    source: string;
    amount_per_day_paise: number;
    real_days?: number;
  }[];
  gaps?: string[];
  setup_gaps?: string[];
  simulation?: {
    categories_sampled?: string[];
    categories_flat?: string[];
    resampling?: string;
    runs?: number;
  };
};

export type ProjectionPayload = {
  card?: string;
  as_of: string;
  horizon_days: number;
  curve: ProjectionPoint[];
  floor?: Money | null;
  floor_paise?: number;
  floor_set?: boolean;
  floor_probability: number;
  floor_threshold: number;
  median_low: { date: string; amount?: Money; paise?: number };
  first_floor_crossing: string | null;
  goals?: ProjectionGoal[];
  obligations_met?: boolean;
  data_quality: ProjectionDataQuality;
  runs?: number;
  sources?: ProjectionSource[];
  source_ids?: Record<string, string[]>;
  optimistic?: {
    floor_probability: number;
    median_low?: { date: string; paise: number };
    income?: { date: string; name: string; amount_paise: number }[];
  } | null;
  verdict?: "yes" | "yes-but" | "no";
  verdict_reason?: string;
  tradeoffs?: ProjectionTradeoff[];
  goal_slips?: { name: string; slips_days: number | null }[];
  hypothetical?: Record<string, unknown>;
  baseline?: { floor_probability: number };
};

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "not within the horizon";
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function ProjectionChart({
  payload,
  height = 260,
}: {
  payload: ProjectionPayload;
  height?: number;
}) {
  const reducedMotion = useReducedMotion();
  const readiness = projectionReadiness(payload);
  if (!readiness.ready) {
    return (
      <div role="img" aria-label={readiness.message ?? "Projection unavailable"}>
        <p className="empty-inline">{readiness.message}</p>
      </div>
    );
  }
  const rows = payload.curve.map((point) => ({
    date: point.date,
    label: new Date(`${point.date}T00:00:00`).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
    }),
    band: [point.p10_paise, point.p90_paise],
    median: point.median_paise,
  }));
  const floor = payload.floor?.paise ?? payload.floor_paise ?? null;
  const floorSet = payload.floor_set ?? (floor !== null && floor > 0);
  const showFloor = floorSet && floor !== null;
  const firstPoint = rows[0];
  const lastPoint = rows[rows.length - 1];
  const chartDescription = lastPoint
    ? `${payload.horizon_days}-day balance projection. Median ${formatPaise(
        firstPoint.median,
      )} on ${dateLabel(payload.as_of)} to ${formatPaise(lastPoint.median)} on ${dateLabel(
        lastPoint.date,
      )}.`
    : `${payload.horizon_days}-day balance projection with no curve points.`;
  const chartLabel = payload.obligations_met === false
    ? `${chartDescription} ${OBLIGATION_WARNING}`
    : chartDescription;
  return (
    <div
      className="chart-box projection-chart"
      role="img"
      aria-label={chartLabel}
    >
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#22241e" vertical={false} />
          <XAxis dataKey="label" stroke="#82867a" fontSize={12} minTickGap={28} />
          <YAxis
            stroke="#82867a"
            fontSize={12}
            width={78}
            tickFormatter={(value: number) => formatPaise(value).replace(".00", "")}
          />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(value: unknown) => {
              // Simulated balances: whole rupees, so the tooltip doesn't imply paise precision.
              const whole = (paise: unknown) => formatPaise(Math.round(Number(paise) / 100) * 100);
              return Array.isArray(value) ? `${whole(value[0])} to ${whole(value[1])}` : whole(value);
            }}
            labelFormatter={(_label, entry) => {
              const row = entry?.[0]?.payload as { date?: string } | undefined;
              return row?.date ? dateLabel(row.date) : "";
            }}
          />
          <Area
            type="monotone"
            dataKey="band"
            stroke="none"
            fill="#1ec8a5"
            fillOpacity={0.22}
            name="P10–P90"
            isAnimationActive={!reducedMotion}
            animationDuration={550}
            animationEasing="ease-out"
          />
          <Line
            type="monotone"
            dataKey="median"
            stroke="#d4ff3a"
            strokeWidth={2}
            dot={false}
            name="Median"
            isAnimationActive={!reducedMotion}
            animationDuration={550}
            animationEasing="ease-out"
          />
          {showFloor && (
            <ReferenceLine
              y={floor}
              stroke="#ff6a2b"
              strokeDasharray="4 4"
              label={{ value: "floor", fill: "#ff6a2b", fontSize: 12, position: "insideBottomRight" }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

const VERDICT_LABEL: Record<string, { text: string; className: string }> = {
  yes: { text: "Yes", className: "badge good" },
  "yes-but": { text: "Yes, with some caution", className: "badge warn" },
  no: { text: "No", className: "badge danger" },
};

function Tradeoffs({ tradeoffs }: { tradeoffs: ProjectionTradeoff[] }) {
  if (!tradeoffs.length) return null;
  return (
    <div className="projection-block">
      <h4>What would make it work</h4>
      <ul className="projection-list">
        {tradeoffs.map((tradeoff, index) => (
          <li key={`${tradeoff.kind}-${index}`}>
            <strong>{tradeoff.kind.replaceAll("_", " ")}</strong> {tradeoff.detail}
            {tradeoff.categories?.length ? (
              <span className="fine">
                {" "}
                {tradeoff.categories
                  .map((row) => `${row.category} ${Math.round(row.percent)}%`)
                  .join(", ")}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Goals({ goals }: { goals: ProjectionGoal[] }) {
  if (!goals.length) return null;
  return (
    <div className="projection-block">
      <h4>Goals</h4>
      <ul className="projection-list">
        {goals.map((goal) => (
          <li key={goal.id}>
            <strong>{goal.name}</strong>{" "}
            {goal.reached
              ? `reaches its target on ${dateLabel(goal.completes_on)}`
              : `does not reach its target within the horizon`}
            {goal.slips_days !== null && goal.slips_days !== undefined
              ? goal.slips_days > 0
                ? ` · ${goal.slips_days} days past the deadline`
                : " · on time"
              : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function DataQuality({ payload }: { payload: ProjectionPayload }) {
  const notes = dataQualityNotes(payload);
  if (!notes.length) return null;
  return (
    <details className="projection-block">
      <summary>
        <strong>Data quality</strong> · {notes.length} {notes.length === 1 ? "note" : "notes"}
      </summary>
      <ul className="projection-list">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </details>
  );
}

function Citations({ payload }: { payload: ProjectionPayload }) {
  const [selected, setSelected] = useState<ProjectionSource | null>(null);
  const sources = payload.sources ?? [];
  if (!sources.length) return null;
  return (
    <div className="projection-block">
      <h4>Sources</h4>
      <div className="citation-chips">
        {sources.map((source) => (
          <button
            key={`${source.kind}-${source.id}`}
            type="button"
            className="citation"
            title={source.detail}
            onClick={() => setSelected(source)}
          >
            {source.kind}: {source.name}
          </button>
        ))}
      </div>
      {selected && (
        <SourceDetail
          key={`${selected.kind}-${selected.id}`}
          source={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

export function ProjectionSummary({ payload }: { payload: ProjectionPayload }) {
  const verdict = payload.verdict ? VERDICT_LABEL[payload.verdict] : null;
  const medianLow = lowAmount(payload);
  const floorText =
    payload.floor?.text ??
    (payload.floor_paise !== undefined ? formatPaise(payload.floor_paise) : null);
  const floorSet = payload.floor_set ?? (payload.floor_paise ?? 0) > 0;
  const aboveThreshold = payload.floor_probability >= payload.floor_threshold;
  return (
    <>
      <div className="projection-head">
        <div>
          {floorSet ? (
            <>
              <span className={`badge ${aboveThreshold ? "good" : "danger"}`}>
                {percent(payload.floor_probability)} above floor
              </span>{" "}
              <span className="fine">threshold {percent(payload.floor_threshold)}</span>
            </>
          ) : (
            <>
              <span className="badge muted">No floor set</span>{" "}
              <a className="inline-link" href="#settings/profile">
                Set a floor
              </a>
            </>
          )}
        </div>
        {verdict && <span className={verdict.className}>{verdict.text}</span>}
      </div>
      <p className="projection-facts">
        {floorSet && floorText ? `Floor ${floorText} · ` : ""}
        Lowest median point {formatPaise(medianLow)} on {dateLabel(payload.median_low.date)}
        {payload.first_floor_crossing
          ? ` · 1 in 10 futures goes below ${floorSet ? "the floor" : "₹0"} by ${dateLabel(payload.first_floor_crossing)}`
          : ` · fewer than 1 in 10 futures go below ${floorSet ? "the floor" : "₹0"}`}
        {floorSet && payload.optimistic
          ? ` · with unconfirmed income: ${percent(payload.optimistic.floor_probability)} above floor (optimistic only)`
          : ""}
      </p>
      {payload.verdict_reason && <p className="fine">{payload.verdict_reason}</p>}
    </>
  );
}

export function ProjectionBody({ payload }: { payload: ProjectionPayload }) {
  const readiness = projectionReadiness(payload);
  if (!readiness.ready) {
    return (
      <>
        <p className="empty-inline">{readiness.message}</p>
        <DataQuality payload={payload} />
        <Citations payload={payload} />
      </>
    );
  }
  return (
    <>
      <ProjectionSummary payload={payload} />
      <ProjectionChart payload={payload} />
      {payload.tradeoffs?.length ? <Tradeoffs tradeoffs={payload.tradeoffs} /> : null}
      {payload.goals?.length ? <Goals goals={payload.goals} /> : null}
      <DataQuality payload={payload} />
      <Citations payload={payload} />
    </>
  );
}

export function ProjectionCard({
  data,
  onEvidence,
}: {
  data: Record<string, unknown>;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const payload = data as unknown as ProjectionPayload;
  const readiness = projectionReadiness(payload);
  const gated = !readiness.ready;
  const title = data.title ? String(data.title) : "Projection";
  const median = lowAmount(payload);
  const floorSet = payload.floor_set ?? (payload.floor_paise ?? 0) > 0;
  const floorText =
    payload.floor?.text ??
    (payload.floor_paise !== undefined ? formatPaise(payload.floor_paise) : null);
  const verdict = payload.verdict ? VERDICT_LABEL[payload.verdict] : null;
  const floorBadge = floorSet
    ? `${percent(payload.floor_probability)} above floor`
    : "No floor set";
  const badgeTone =
    gated || !floorSet ? "muted" : payload.floor_probability >= payload.floor_threshold ? "good" : "danger";
  const purchase = payload.hypothetical as
    | { amount_paise?: number; date?: string; method?: string; emi_months?: number }
    | undefined;
  const [open, setOpen] = useState(false);
  const reducedMotion = useReducedMotion();

  return (
    <motion.figure
      className="chat-card receipt"
      initial={reducedMotion ? false : { opacity: 0, y: 8, scale: 0.995 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
    >
      <div className="receipt-top">
        <div className="receipt-title">
          <Orb size={20} />
          <strong>{gated ? title : (verdict?.text ?? title)}</strong>
        </div>
        <span className={`badge ${badgeTone}`}>
          {gated ? "Balance unknown" : floorBadge}
        </span>
      </div>

      {gated ? (
        <p className="receipt-note">{readiness.message}</p>
      ) : (
        payload.verdict_reason && <p className="receipt-note">{payload.verdict_reason}</p>
      )}
      {purchase?.amount_paise !== undefined && (
        <p className="receipt-note">
          Purchase of {formatPaise(purchase.amount_paise)}
          {purchase.date ? ` on ${dateLabel(purchase.date)}` : ""}
          {purchase.method ? ` · ${paymentMethodLabel(purchase.method)}` : ""}
          {purchase.emi_months ? ` over ${purchase.emi_months} months` : ""}.
        </p>
      )}

      {!gated && (
        <dl className="receipt-metrics">
          <div>
            <dt>Chance above floor</dt>
            <dd>{floorSet ? percent(payload.floor_probability) : "—"}</dd>
            <span>{percent(payload.floor_threshold)} threshold</span>
          </div>
          <div>
            <dt>Lowest median balance</dt>
            <dd>{median === null ? "—" : formatPaise(median)}</dd>
            <span>{dateLabel(payload.median_low.date)}</span>
          </div>
        </dl>
      )}

      <div className="receipt-foot">
        <span className="fine">
          {payload.horizon_days}-day projection
          {!gated && floorSet && floorText ? ` · floor ${floorText}` : ""}
        </span>
        {onEvidence && (
          <span className="receipt-actions">
            {!gated && (
              <button type="button" className="ghost small" onClick={() => openPlanner(payload)}>
                <SlidersHorizontal size={14} strokeWidth={2} aria-hidden="true" />
                Planner
              </button>
            )}
            <button
              type="button"
              className="ghost small"
              onClick={() => onEvidence(payload)}
            >
              <FileSearch size={14} strokeWidth={1.8} aria-hidden="true" />
              Sources and assumptions
            </button>
          </span>
        )}
      </div>

      {!onEvidence && (
        <details
          className="receipt-more"
          open={open}
          onToggle={(event) => setOpen(event.currentTarget.open)}
        >
          <summary>{gated ? "Data quality and sources" : "Open the curve, tradeoffs, and sources"}</summary>
          {open && (
            <>
              {!gated && <ProjectionChart payload={payload} />}
              {!gated && payload.tradeoffs?.length ? (
                <Tradeoffs tradeoffs={payload.tradeoffs} />
              ) : null}
              {!gated && payload.goals?.length ? <Goals goals={payload.goals} /> : null}
              <DataQuality payload={payload} />
              <Citations payload={payload} />
            </>
          )}
        </details>
      )}
    </motion.figure>
  );
}
