import { useEffect, useState } from "react";
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
import { api, errorMessage } from "./api";
import { formatPaise } from "./format";

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
  remaining?: Money | null;
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
  floor_probability: number;
  floor_threshold: number;
  median_low: { date: string; amount?: Money; paise?: number };
  first_floor_crossing: string | null;
  goals?: ProjectionGoal[];
  obligations_met?: boolean;
  data_quality: ProjectionDataQuality;
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

function lowAmount(payload: ProjectionPayload): number | null {
  if (payload.median_low.amount?.paise !== undefined) return payload.median_low.amount.paise;
  if (payload.median_low.paise !== undefined) return payload.median_low.paise;
  return null;
}

export function ProjectionChart({
  payload,
  height = 260,
}: {
  payload: ProjectionPayload;
  height?: number;
}) {
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
  return (
    <div className="chart-box projection-chart">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#1f2a37" vertical={false} />
          <XAxis dataKey="label" stroke="#93a2b4" fontSize={11} minTickGap={28} />
          <YAxis
            stroke="#93a2b4"
            fontSize={11}
            width={78}
            tickFormatter={(value: number) => formatPaise(value)}
          />
          <Tooltip
            formatter={(value: unknown) => {
              if (Array.isArray(value)) {
                return `${formatPaise(Number(value[0]))} – ${formatPaise(Number(value[1]))}`;
              }
              return formatPaise(Number(value));
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
            fill="#4cc38a"
            fillOpacity={0.16}
            name="P10–P90"
          />
          <Line
            type="monotone"
            dataKey="median"
            stroke="#4cc38a"
            strokeWidth={2}
            dot={false}
            name="Median"
          />
          {floor !== null && (
            <ReferenceLine
              y={floor}
              stroke="#ff8a80"
              strokeDasharray="4 4"
              label={{ value: "floor", fill: "#ff8a80", fontSize: 11, position: "insideTopLeft" }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

const VERDICT_LABEL: Record<string, { text: string; className: string }> = {
  yes: { text: "Yes", className: "badge good" },
  "yes-but": { text: "Yes, with a change", className: "badge warn" },
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
  const quality = payload.data_quality;
  const notes = [
    ...(quality.gaps ?? []),
    ...(quality.setup_gaps ?? []).map((gap) => `setup: ${gap}`),
    ...(quality.simulation?.categories_flat?.length
      ? [
          `flat estimates for ${quality.simulation.categories_flat.join(", ")} (too few real spending days)`,
        ]
      : []),
    ...(quality.simulation?.categories_sampled?.length
      ? [
          `resampled from real days for ${quality.simulation.categories_sampled.join(", ")}`,
        ]
      : []),
  ];
  if (!notes.length) return null;
  return (
    <details className="projection-block" open>
      <summary>
        <strong>Data quality</strong>
      </summary>
      <ul className="projection-list">
        {notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </details>
  );
}

function citationEndpoint(source: ProjectionSource): string | null {
  if (source.kind === "account" || source.kind === "card") return `/api/accounts/${source.id}`;
  if (source.kind === "schedule") return `/api/schedules/${source.id}`;
  if (source.kind === "goal") return `/api/goals/${source.id}`;
  if (source.kind === "payee") return "/api/payees";
  return null;
}

function CitationDetail({
  source,
  onClose,
}: {
  source: ProjectionSource;
  onClose: () => void;
}) {
  const endpoint = citationEndpoint(source);
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(endpoint));

  useEffect(() => {
    if (!endpoint) return;
    api<unknown>(endpoint)
      .then((value) => {
        if (source.kind === "payee" && Array.isArray(value)) {
          const rows = value as { id?: string }[];
          setDetail(rows.find((row) => row.id === source.id) ?? value);
        } else {
          setDetail(value);
        }
      })
      .catch((caught: unknown) => setError(errorMessage(caught)))
      .finally(() => setLoading(false));
  }, [endpoint, source]);

  return (
    <div className="citation-detail">
      <div className="citation-detail-head">
        <strong>
          {source.kind} · {source.name}
        </strong>
        <button type="button" className="ghost small" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="fine">{source.detail}</p>
      {loading && <p className="muted">Opening the underlying row…</p>}
      {error && <p className="error">{error}</p>}
      {detail !== null && <pre className="citation-json">{JSON.stringify(detail, null, 2)}</pre>}
    </div>
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
        <CitationDetail
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
  return (
    <>
      <div className="projection-head">
        <div>
          <span className="badge good">{percent(payload.floor_probability)} above floor</span>{" "}
          <span className="fine">threshold {percent(payload.floor_threshold)}</span>
        </div>
        {verdict && <span className={verdict.className}>{verdict.text}</span>}
      </div>
      <p className="projection-facts">
        Lowest median point {formatPaise(medianLow)} on {dateLabel(payload.median_low.date)}
        {payload.first_floor_crossing
          ? ` · median crosses the floor on ${dateLabel(payload.first_floor_crossing)}`
          : " · the median never crosses the floor"}
        {payload.optimistic
          ? ` · with unconfirmed income: ${percent(payload.optimistic.floor_probability)} above floor (optimistic only)`
          : ""}
      </p>
      {payload.verdict_reason && <p className="fine">{payload.verdict_reason}</p>}
    </>
  );
}

export function ProjectionBody({ payload }: { payload: ProjectionPayload }) {
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

export function ProjectionCard({ data }: { data: Record<string, unknown> }) {
  const payload = data as unknown as ProjectionPayload;
  return (
    <figure className="chat-card projection-card">
      <figcaption>
        <strong>{data.title ? String(data.title) : "Projection"}</strong>
        <span className="fine">
          {" "}
          {payload.horizon_days} days from {dateLabel(payload.as_of)}
        </span>
      </figcaption>
      <ProjectionBody payload={payload} />
    </figure>
  );
}
