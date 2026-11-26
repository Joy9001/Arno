import { useEffect, useState } from "react";
import { animate, useReducedMotion } from "motion/react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatPaise } from "../../format";
import { displayText } from "../display-text";
import type { z } from "zod";
import { scenarios } from "./results";

export function MetricHero({ label, amount, baseline, queryId }: { label: string; amount: number; baseline?: number; queryId: string }) {
  const [value, setValue] = useState(amount);
  const reduced = useReducedMotion();
  useEffect(() => {
    if (reduced) return;
    const animation = animate(0, amount, { duration: 0.6, onUpdate: (next) => setValue(Math.round(next)) });
    return () => animation.stop();
  }, [amount, reduced]);
  return <section className="chat-card metric-hero">
    <p>{displayText(label)}</p><strong className="text-3xl">{formatPaise(reduced ? amount : value)}</strong>
    {baseline != null && <p>{formatPaise(amount - baseline)} change from baseline</p>}
    <button type="button" aria-controls={queryId} onClick={() => { const details = document.getElementById(queryId); if (details instanceof HTMLDetailsElement) { details.open = true; details.scrollIntoView?.({ block: "center" }); } }}>View ledger query</button>
  </section>;
}

export function InteractiveChart({ rows }: { rows: { label: string; amount: number }[] }) {
  const reduced = useReducedMotion();
  const [selected, setSelected] = useState<number | null>(null);
  const [kind, setKind] = useState("bar");
  const canDonut = rows.every((row) => row.amount >= 0) && rows.some((row) => row.amount > 0);
  return <section className="chat-card" aria-label="Query chart"><h3>Breakdown</h3>
    <label>Chart type <select value={kind} onChange={(event) => setKind(event.target.value)}><option value="bar">Bar</option><option value="area">Area</option>{canDonut && <option value="donut">Donut</option>}</select></label>
    <ResponsiveContainer width="100%" height={220}>{kind === "area" ? <AreaChart data={rows}>
      <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis tickFormatter={(value: number) => formatPaise(value)} />
      <Tooltip formatter={(value) => typeof value === "number" ? formatPaise(value) : "—"} /><Area dataKey="amount" stroke="var(--cyan)" fill="var(--cyan)" fillOpacity={0.3} isAnimationActive={!reduced} />
    </AreaChart> : kind === "donut" && canDonut ? <PieChart><Tooltip formatter={(value) => typeof value === "number" ? formatPaise(value) : "—"} /><Pie data={rows} dataKey="amount" nameKey="label" innerRadius="55%" outerRadius="80%" fill="var(--cyan)" isAnimationActive={!reduced} onClick={(_, index) => setSelected(index)} /></PieChart> : <BarChart data={rows}>
      <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="label" /><YAxis tickFormatter={(value: number) => formatPaise(value)} />
      <Tooltip formatter={(value) => typeof value === "number" ? formatPaise(value) : "—"} />
      <Bar dataKey="amount" fill="var(--cyan)" isAnimationActive={!reduced} onClick={(_, index) => setSelected(index)} />
    </BarChart>}</ResponsiveContainer>
    <label>Inspect category <select value={selected ?? ""} onChange={(event) => setSelected(event.target.value === "" ? null : Number(event.target.value))}><option value="">Choose one</option>{rows.map((row, index) => <option key={index} value={index}>{displayText(row.label)}</option>)}</select></label>
    {selected != null && rows[selected] && <p>{displayText(rows[selected].label)} · {formatPaise(rows[selected].amount)}</p>}
  </section>;
}

export function WaterfallChart({ rows }: { rows: { label: string; amount: number; from: number; to: number }[] }) {
  const [selected, setSelected] = useState(0);
  const low = Math.min(0, ...rows.flatMap((row) => [row.from, row.to]));
  const high = Math.max(0, ...rows.flatMap((row) => [row.from, row.to]));
  const span = high - low || 1;
  return <section className="chat-card" aria-label="Waterfall chart"><h3>Changes</h3>
    <svg width="100%" height="180" viewBox={`0 0 ${rows.length * 50} 180`} role="img" aria-label="Changes between supplied amounts">
      {rows.map((row, index) => <rect key={index} x={index * 50 + 5} y={10 + (high - Math.max(row.from, row.to)) / span * 150} width="35" height={Math.max(1, Math.abs(row.to - row.from) / span * 150)} fill={row.amount < 0 ? "var(--orange)" : "var(--cyan)"} />)}
    </svg>
    <label>Inspect change <select value={selected} onChange={(event) => setSelected(Number(event.target.value))}>{rows.map((row, index) => <option key={index} value={index}>{displayText(row.label)}</option>)}</select></label>
    <p>{displayText(rows[selected].label)} · {formatPaise(rows[selected].amount)} ({formatPaise(rows[selected].from)} to {formatPaise(rows[selected].to)})</p>
  </section>;
}

export function ComparisonMatrix({ data }: { data: z.infer<typeof scenarios> }) {
  const tenures = [...new Set(data.scenarios.map((row) => row.tenure_months))].sort((a, b) => a - b);
  const prepayments = [...new Set(data.scenarios.map((row) => row.prepayment_paise))].sort((a, b) => a - b);
  const [tenure, setTenure] = useState(tenures[0]);
  const [prepayment, setPrepayment] = useState(prepayments[0]);
  const rows = data.scenarios.filter((row) => row.tenure_months === tenure && row.prepayment_paise === prepayment);
  return <section className="chat-card" aria-label="Computed scenario comparison"><h3>Compare scenarios</h3>
    <label>Tenure <select value={tenure} onChange={(event) => setTenure(Number(event.target.value))}>{tenures.map((value) => <option key={value} value={value}>{value} months</option>)}</select></label>
    {tenures.length > 1 && <label>Adjust tenure<input type="range" min="0" max={tenures.length - 1} value={tenures.indexOf(tenure)} onChange={(event) => setTenure(tenures[Number(event.target.value)])} /></label>}
    <label>Prepayment <select value={prepayment} onChange={(event) => setPrepayment(Number(event.target.value))}>{prepayments.map((value) => <option key={value} value={value}>{formatPaise(value)}</option>)}</select></label>
    {prepayments.length > 1 && <label>Adjust prepayment<input type="range" min="0" max={prepayments.length - 1} value={prepayments.indexOf(prepayment)} onChange={(event) => setPrepayment(prepayments[Number(event.target.value)])} /></label>}
    <div role="region" tabIndex={0} aria-label="Scenario comparison" style={{ overflowX: "auto" }}><table><thead><tr><th>Scenario</th><th>Monthly</th><th>Interest</th><th>Total</th></tr></thead><tbody>{rows.map((row, index) => <tr key={index}><th scope="row">{displayText(row.label)}</th><td>{formatPaise(row.monthly_paise)}</td><td>{formatPaise(row.interest_paise)}</td><td>{formatPaise(row.total_paise)}</td></tr>)}</tbody></table></div>
    {!rows.length && <p>No calculation was returned for this combination.</p>}
    <p className="fine">Figures are calculated by the script; omitted costs are not included.</p>
  </section>;
}
