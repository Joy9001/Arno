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
  const floorSet = payload.floor_set ?? (floor !== null && floor > 0);
  const showFloor = floorSet && floor !== null;
  return (
    <div className="chart-box projection-chart">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid stroke="#2a2a2c" vertical={false} />
          <XAxis dataKey="label" stroke="#8b8b90" fontSize={12} minTickGap={28} />
          <YAxis
            stroke="#8b8b90"
            fontSize={12}
            width={78}
            tickFormatter={(value: number) => formatPaise(value).replace(".00", "")}
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
            fill="#0b6cf0"
            fillOpacity={0.16}
            name="P10–P90"
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="median"
            stroke="#0b6cf0"
            strokeWidth={2}
            dot={false}
            name="Median"
            isAnimationActive={false}
          />
          {showFloor && (
            <ReferenceLine
              y={floor}
              stroke="#ff453a"
              strokeDasharray="4 4"
              label={{ value: "floor", fill: "#ff453a", fontSize: 12, position: "insideBottomRight" }}
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

function citationEndpoint(source: ProjectionSource): string | null {
  if (source.kind === "account" || source.kind === "card") return `/api/accounts/${source.id}`;
  if (source.kind === "schedule") return `/api/schedules/${source.id}`;
  if (source.kind === "goal") return `/api/goals/${source.id}`;
  if (source.kind === "payee") return "/api/payees";
  return null;
}

function SourceFacts({ value }: { value: unknown }) {
  if (!value || typeof value !== "object") return <p className="fine">{String(value)}</p>;
  const row = value as Record<string, unknown>;
  const keys = [
    "id",
    "kind",
    "name",
    "institution",
    "last4",
    "balance_paise",
    "amount_paise",
    "date",
    "as_of",
    "status",
    "description",
    "rate_bps",
    "tenure_months",
  ];
  return (
    <>
      <dl className="citation-facts">
        {keys.map((key) => {
          const raw = row[key];
          if (raw === undefined || raw === null) return null;
          const text = key.endsWith("_paise") && typeof raw === "number"
            ? formatPaise(raw)
            : key === "rate_bps" && typeof raw === "number"
              ? `${raw / 100}%`
              : String(raw);
          const label = key.endsWith("_paise")
            ? key.slice(0, -"_paise".length).replaceAll("_", " ")
            : key === "rate_bps"
              ? "rate"
              : key.replaceAll("_", " ");
          return (
            <div key={key}>
              <dt>{label}</dt>
              <dd>{text}</dd>
            </div>
          );
        })}
      </dl>
      <details className="citation-raw">
        <summary>Raw row</summary>
        <pre className="citation-json">{JSON.stringify(value, null, 2)}</pre>
      </details>
    </>
  );
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
        if (source.kind === "payee") {
          const rows = Array.isArray(value)
            ? (value as { id?: string }[])
            : ((value as { items?: { id?: string }[] }).items ?? []);
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
      {detail !== null && <SourceFacts value={detail} />}
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
          ? ` · median crosses ${floorSet ? "the floor" : "₹0"} on ${dateLabel(payload.first_floor_crossing)}`
          : ` · the median never crosses ${floorSet ? "the floor" : "₹0"}`}
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
  const runs = payload.data_quality?.simulation?.runs;
  const sources = payload.sources?.length ?? 0;
  const [open, setOpen] = useState(false);
  return (
    <figure className="chat-card projection-card">
      <figcaption>
        <strong>{data.title ? String(data.title) : "Projection"}</strong>
        <span className="fine">
          {payload.horizon_days} days · {sources} sources
          {runs ? ` · ${runs.toLocaleString("en-IN")} runs` : ""}
        </span>
      </figcaption>
      <ProjectionSummary payload={payload} />
      <details
        className="receipt-more"
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>Open the curve, tradeoffs, and sources</summary>
        {open && (
          <>
            <ProjectionChart payload={payload} />
            {payload.tradeoffs?.length ? <Tradeoffs tradeoffs={payload.tradeoffs} /> : null}
            {payload.goals?.length ? <Goals goals={payload.goals} /> : null}
            <DataQuality payload={payload} />
            <Citations payload={payload} />
          </>
        )}
      </details>
    </figure>
  );
}
