import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, errorMessage } from "../api";
import { formatRupees } from "./format";

const COLORS = ["#4cc38a", "#5aa9e6", "#f6c85f", "#ff8a80", "#b48ead", "#8fd694"];

function unitValue(value: number, unit: string): string {
  if (unit === "paise") return formatRupees(value);
  if (unit === "percent") return `${value}%`;
  return new Intl.NumberFormat("en-IN").format(value);
}

type Point = { x: string; y: number };
type Series = { name: string; points: Point[] };

function chartRows(series: Series[]): Record<string, string | number>[] {
  const rows = new Map<string, Record<string, string | number>>();
  for (const line of series) {
    for (const point of line.points) {
      const row = rows.get(point.x) ?? { x: point.x };
      row[line.name] = point.y;
      rows.set(point.x, row);
    }
  }
  return [...rows.values()];
}

export function ChartCard({ data }: { data: Record<string, unknown> }) {
  const type = String(data.type ?? "line");
  const title = String(data.title ?? "Chart");
  const unit = String(data.unit ?? "paise");
  const series = (data.series as Series[] | undefined) ?? [];
  const asOf = data.as_of ? String(data.as_of) : null;
  const rows = chartRows(series);
  const tick = (value: number) => unitValue(value, unit);

  return (
    <figure className="chat-card chart-card">
      <figcaption>
        <strong>{title}</strong>
        {asOf && <span className="fine"> as of {asOf}</span>}
      </figcaption>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={240}>
          {type === "donut" ? (
            <PieChart>
              <Pie
                data={series[0]?.points.map((point) => ({ name: point.x, value: point.y })) ?? []}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
              >
                {(series[0]?.points ?? []).map((point, index) => (
                  <Cell key={point.x} fill={COLORS[index % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(value) => tick(Number(value))} />
              <Legend />
            </PieChart>
          ) : type === "bar" || type === "stacked_bar" ? (
            <BarChart data={rows}>
              <CartesianGrid stroke="#1f2a37" vertical={false} />
              <XAxis dataKey="x" stroke="#93a2b4" fontSize={12} />
              <YAxis stroke="#93a2b4" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Bar
                  key={line.name}
                  dataKey={line.name}
                  stackId={type === "stacked_bar" ? "stack" : undefined}
                  fill={COLORS[index % COLORS.length]}
                />
              ))}
            </BarChart>
          ) : (
            <LineChart data={rows}>
              <CartesianGrid stroke="#1f2a37" vertical={false} />
              <XAxis dataKey="x" stroke="#93a2b4" fontSize={12} />
              <YAxis stroke="#93a2b4" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Line
                  key={line.name}
                  type="monotone"
                  dataKey={line.name}
                  stroke={COLORS[index % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function UndoButton({ undoId }: { undoId?: string | null }) {
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  if (!undoId) return null;
  if (state === "done") return <span className="fine">Undone.</span>;
  return (
    <div>
      <button
        type="button"
        className="ghost small"
        disabled={state === "busy"}
        onClick={async () => {
          setState("busy");
          try {
            await api(`/api/undo/${undoId}`, { method: "POST" });
            setState("done");
          } catch (caught) {
            setError(errorMessage(caught));
            setState("error");
          }
        }}
      >
        {state === "busy" ? "Undoing…" : "Undo"}
      </button>
      {error && <span className="error fine"> {error}</span>}
    </div>
  );
}

export function Card({ data }: { data: Record<string, unknown> }) {
  const card = String(data.card ?? "card");
  if (card === "chart") return <ChartCard data={data} />;

  if (card === "movement" || card === "correction" || card === "undo") {
    const title = String(data.title ?? "Logged");
    const summary = data.summary ? String(data.summary) : "";
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{title}</strong>
          {Boolean(data.amount && typeof data.amount === "object") && (
            <span className="money">{String((data.amount as Record<string, unknown>).text ?? "")}</span>
          )}
        </div>
        {summary && <p>{summary}</p>}
        {Boolean(data.rule) && <p className="fine">Rule: {String(data.rule)}</p>}
        {typeof data.rows_changed === "number" && data.rows_changed > 0 && (
          <p className="fine">{data.rows_changed} past row(s) changed.</p>
        )}
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "preference") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{String(data.title ?? "Remembered")}</strong>
        </div>
        <p>{String(data.text ?? "")}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "setup") {
    return (
      <div className="chat-card">
        <div className="chat-card-head">
          <strong>{String(data.title ?? "Setup saved")}</strong>
        </div>
        <p>{String(data.summary ?? "")}</p>
        <UndoButton undoId={data.undo_id as string | undefined} />
      </div>
    );
  }

  if (card === "query") {
    return (
      <details className="chat-card">
        <summary>{String(data.row_count ?? 0)} row(s) from read-only SQL</summary>
        <pre className="sql">{String(data.sql ?? "")}</pre>
      </details>
    );
  }

  if (card === "amortize") {
    const emi = data.emi as { text?: string } | undefined;
    const interest = data.total_interest as { text?: string } | undefined;
    return (
      <div className="chat-card">
        <strong>Loan schedule</strong>
        <p>
          EMI {emi?.text ?? "—"} · total interest {interest?.text ?? "—"} ·{" "}
          {String(data.months ?? "—")} months
        </p>
        {Boolean(data.prepay && typeof data.prepay === "object") && (
          <p className="fine">
            Prepay saves {(data.prepay as Record<string, { text?: string }>).interest_saved?.text ??
              "—"}
          </p>
        )}
      </div>
    );
  }

  if (card === "compound") {
    const final = data.final as { text?: string } | undefined;
    return (
      <div className="chat-card">
        <strong>Growth at an assumed rate</strong>
        <p>{final?.text ?? "—"} at the assumed rate.</p>
        <p className="fine">{Array.isArray(data.assumptions) ? data.assumptions.join(" · ") : ""}</p>
      </div>
    );
  }

  return (
    <details className="chat-card">
      <summary>{card}</summary>
      <pre>{JSON.stringify(data, null, 2)}</pre>
    </details>
  );
}

export function NumberFlags({ amounts }: { amounts: string[] }) {
  if (!amounts.length) return null;
  return (
    <p className="note">
      Couldn't trace {amounts.join(", ")}. Ask again with the statement it should come from.
    </p>
  );
}
