import { useState } from "react";
import { useReducedMotion } from "motion/react";
import { Download } from "lucide-react";
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
import { displayText } from "./chat/display-text";
import { formatPaise } from "./format";
import type { ProjectionReceipt } from "./projectionReceipt";
import {
  baselineLowAmount,
  baselineSafeToSpendAmount,
  dataQualityNotes,
  lowAmount,
  OBLIGATION_WARNING,
  projectionReadiness,
  workbookUrl,
} from "./projectionData";
import { SourceDetail } from "./SourceDetail";
import { SafeToSpend } from "./SafeToSpend";

export type Money = { paise: number; text: string };

export type SafeToSpend = {
  amount?: Money;
  amount_paise?: number;
  binding: "floor" | "obligation";
  binding_date: string;
};

export type ScenarioChange = {
  kind: string;
  label: string;
  occurrences: number;
  amount?: Money;
  amount_paise?: number;
};

export type ScenarioBaseline = {
  floor_probability: number;
  median_low: { date: string; amount?: Money; paise?: number } | null;
  first_floor_crossing: string | null;
  safe_to_spend?: SafeToSpend | null;
};

export type ProjectionScenario = {
  changes: ScenarioChange[];
  assumptions: string[];
  holds?: boolean;
  reason?: string;
  runway_days: number | null;
  baseline: ScenarioBaseline;
};

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

/** The arguments one projection was asked with, echoed by the tool for the workbook link. */
export type ProjectionRequest = {
  horizon_days?: number | null;
  cut_percent?: number;
  hypothetical?: {
    amount_paise?: number;
    date?: string;
    method?: string;
    emi_months?: number | null;
    account?: string | null;
    emi_rate_percent?: number | null;
    emi_fee_paise?: number | null;
    no_cost?: boolean;
  };
  changes?: Record<string, unknown>[];
};

/** The one normalized receipt shape, owned by the ingress and shared with every renderer. */
export type ProjectionPayload = ProjectionReceipt;

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

/** The workbook endpoint takes the same query parameters as `GET /api/projection`. */
export function WorkbookLink({ request }: { request?: ProjectionRequest }) {
  return (
    <a className="ghost small" href={workbookUrl(request)} download>
      <Download size={14} strokeWidth={2} aria-hidden="true" />
      Download workbook (.xlsx)
    </a>
  );
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
  const floor = payload.floor?.paise ?? null;
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
            tickFormatter={(value: number) => formatPaise(Math.round(value)).replace(".00", "")}
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
          <li key={note}>{displayText(note)}</li>
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

function ScenarioBlock({ scenario }: { scenario: ProjectionScenario }) {
  if (!scenario.changes.length && !scenario.assumptions.length) return null;
  return (
    <div className="projection-block">
      {scenario.changes.length > 0 && (
        <>
          <h4>Assumed changes</h4>
          <ul className="projection-list">
            {scenario.changes.map((change, index) => (
              <li key={`${change.kind}-${index}`}>{displayText(change.label)}</li>
            ))}
          </ul>
        </>
      )}
      {scenario.assumptions.length > 0 && (
        <ul className="projection-list">
          {scenario.assumptions.map((note, index) => (
            <li key={`assumption-${index}`}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function scenarioRunwayText(payload: ProjectionPayload, scenario: ProjectionScenario): string {
  return scenario.runway_days !== null
    ? `Runway: ${scenario.runway_days} days · first crossing ${dateLabel(payload.first_floor_crossing)}`
    : `Runway: no crossing within ${payload.horizon_days} days`;
}

function scenarioBaselineText(payload: ProjectionPayload, scenario: ProjectionScenario): string {
  const parts: string[] = [];
  const floorSet = payload.floor_set ?? (payload.floor?.paise ?? 0) > 0;
  const baseMedian = baselineLowAmount(payload);
  const baseSafe = baselineSafeToSpendAmount(payload);
  if (floorSet) {
    parts.push(
      `Before: ${percent(scenario.baseline.floor_probability)} above floor · After: ${percent(payload.floor_probability)}`,
    );
  }
  if (baseMedian !== null) {
    parts.push(`baseline low ${formatPaise(baseMedian)}`);
  }
  if (scenario.baseline.first_floor_crossing) {
    parts.push(`baseline crossing ${dateLabel(scenario.baseline.first_floor_crossing)}`);
  }
  if (baseSafe !== null) {
    parts.push(`baseline safe to spend ${formatPaise(baseSafe)}`);
  }
  return parts.join(" · ");
}

export function ProjectionSummary({ payload }: { payload: ProjectionPayload }) {
  const verdict = payload.verdict ? VERDICT_LABEL[payload.verdict] : null;
  const medianLow = lowAmount(payload);
  const scenario = payload.scenario;
  const floorText = payload.floor?.text ?? null;
  const floorSet = payload.floor_set ?? (payload.floor?.paise ?? 0) > 0;
  const aboveThreshold = payload.floor_probability >= payload.floor_threshold;
  const baselineSummary = scenario ? scenarioBaselineText(payload, scenario) : "";
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
      <SafeToSpend payload={payload} className="projection-facts" showNotes={false} />
      {scenario && (scenario.holds !== undefined || scenario.reason) && (
        <p className="projection-facts">
          {scenario.holds !== undefined ? (scenario.holds ? "Plan holds" : "Plan falls short") : ""}
          {scenario.holds !== undefined && scenario.reason ? " · " : ""}
          {scenario.reason ?? ""}
        </p>
      )}
      {scenario && <p className="projection-facts">{scenarioRunwayText(payload, scenario)}</p>}
      {baselineSummary && <p className="projection-facts">{baselineSummary}</p>}
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
        {payload.scenario ? <ScenarioBlock scenario={payload.scenario} /> : null}
        <DataQuality payload={payload} />
        <Citations payload={payload} />
      </>
    );
  }
  return (
    <>
      <ProjectionSummary payload={payload} />
      {payload.scenario ? <ScenarioBlock scenario={payload.scenario} /> : null}
      <ProjectionChart payload={payload} />
      {payload.tradeoffs?.length ? <Tradeoffs tradeoffs={payload.tradeoffs} /> : null}
      {payload.goals?.length ? <Goals goals={payload.goals} /> : null}
      <DataQuality payload={payload} />
      <Citations payload={payload} />
    </>
  );
}
