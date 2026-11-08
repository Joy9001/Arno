import { Code2 } from "lucide-react";
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
import { displayText } from "./display-text";
import { formatRupees } from "./format";
import { formatMoment } from "../format";

const COLORS = ["#d4ff3a", "#1ec8a5", "#2b59ff", "#ff6a2b", "#ffd23a", "#3ee07f"];

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
      const label = displayText(point.x);
      const row = rows.get(label) ?? { x: label };
      row[line.name] = point.y;
      rows.set(label, row);
    }
  }
  return [...rows.values()];
}

export function ChartCard({
  data,
  showTitle = true,
}: {
  data: Record<string, unknown>;
  showTitle?: boolean;
}) {
  const type = String(data.type ?? "line");
  const title = String(data.title ?? "Chart");
  const unit = String(data.unit ?? "paise");
  const series = (data.series as Series[] | undefined) ?? [];
  const asOf = data.as_of ? String(data.as_of) : null;
  const computedAt = data.computed_at ? String(data.computed_at) : null;
  const script = typeof data.script === "string" ? data.script : null;
  const source = typeof data.source === "string" ? data.source : null;
  const rows = chartRows(series);
  const tick = (value: number) => unitValue(value, unit);

  return (
    <figure className="chat-card chart-card">
      <figcaption>
        {showTitle ? <strong>{displayText(title)}</strong> : null}
        <span className="fine">
          {computedAt ? `Calculated ${formatMoment(computedAt)}` : ""}
          {computedAt && asOf ? " · " : ""}
          {asOf ? `as of ${asOf}` : ""}
        </span>
      </figcaption>
      <div className="chart-box">
        <ResponsiveContainer width="100%" height={240}>
          {type === "donut" ? (
            <PieChart>
              <Pie
                data={series[0]?.points.map((point) => ({ name: displayText(point.x), value: point.y })) ?? []}
                dataKey="value"
                nameKey="name"
                innerRadius={55}
                outerRadius={90}
                paddingAngle={2}
                isAnimationActive={false}
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
              <CartesianGrid stroke="#22241e" vertical={false} />
              <XAxis dataKey="x" stroke="#82867a" fontSize={12} />
              <YAxis stroke="#82867a" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Bar
                  key={line.name}
                  dataKey={line.name}
                  name={displayText(line.name)}
                  stackId={type === "stacked_bar" ? "stack" : undefined}
                  fill={COLORS[index % COLORS.length]}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          ) : (
            <LineChart data={rows}>
              <CartesianGrid stroke="#22241e" vertical={false} />
              <XAxis dataKey="x" stroke="#82867a" fontSize={12} />
              <YAxis stroke="#82867a" fontSize={12} tickFormatter={tick} width={80} />
              <Tooltip formatter={(value) => tick(Number(value))} />
              {series.map((line, index) => (
                <Line
                  key={line.name}
                  type="monotone"
                  dataKey={line.name}
                  name={displayText(line.name)}
                  stroke={COLORS[index % COLORS.length]}
                  dot={false}
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      <details>
        <summary className="fine">
          <Code2 size={14} strokeWidth={1.8} aria-hidden="true" />{" "}
          {script ? "Script behind this chart" : source ? "Source data" : "Source unavailable"}
        </summary>
        {script ? (
          <pre className="sql">{displayText(script)}</pre>
        ) : source ? (
          <p className="fine">{displayText(source)}</p>
        ) : (
          <p className="fine">
            The script that calculated this chart was not saved for this reply.
          </p>
        )}
      </details>
    </figure>
  );
}
