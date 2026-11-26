import { useEffect, useId, useMemo, useState } from "react";
import { z } from "zod";
import { displayText } from "../display-text";
import { formatDate, formatPaise, isoDate, percent } from "../../format";
import { ProjectionBody, type ProjectionPayload } from "../../projection";
import { projectionReadiness, workbookUrl } from "../../projectionData";
import { parseProjectionReceipt } from "../../projectionReceipt";
import { Timeline } from "../../panels/Timeline";
import { ImportsPanel } from "../../panels/Imports";
import { RecapsPage } from "../../pages/Recaps";
import { api, errorMessage } from "../../api";
import { cardOf, queryResult, scenarios } from "./results";
import { GoalProgressRing } from "./goal";
import { renderWidget } from "./registry";
import { MovementDetails } from "./movement";
import { ActionReceipt } from "./action";

export { GoalProgressRing } from "./goal";
export { ActionReceipt } from "./action";
export { ApprovalCard as ActionConfirmCard } from "@/components/approval-card";

export function BreakdownTable({ data, id }: { data: z.infer<typeof queryResult>; id?: string }) {
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<number | null>(null);
  const [descending, setDescending] = useState(false);
  const [movementId, setMovementId] = useState<string | null>(null);
  const movementColumn = data.columns.indexOf("movement_id");
  const amountColumn = data.columns.indexOf("amount_paise");
  const peak = amountColumn < 0 ? 0 : Math.max(0, ...data.rows.map((row) => typeof row[amountColumn] === "number" ? Math.abs(Number(row[amountColumn])) : 0));
  const rows = useMemo(() => {
    const needle = filter.toLocaleLowerCase();
    const filtered = data.rows.filter((row) => row.some((value) => String(value ?? "").toLocaleLowerCase().includes(needle)));
    if (sort === null) return filtered;
    return [...filtered].sort((a, b) => {
      const left = a[sort];
      const right = b[sort];
      const comparison = typeof left === "number" && typeof right === "number"
        ? left - right : String(left ?? "").localeCompare(String(right ?? ""));
      return descending ? -comparison : comparison;
    });
  }, [data.rows, filter, sort, descending]);
  return <section id={id} className="chat-card" aria-label="Query results">
    <p className="fine">Query snapshot. Movement details show the current record.</p>
    <label>Filter results <input value={filter} onChange={(event) => setFilter(event.target.value)} /></label>
    <div role="region" aria-label="Query results table" tabIndex={0} style={{ overflowX: "auto" }}>
      <table><thead><tr>{data.columns.map((column, index) => <th key={`${column}-${index}`} scope="col" aria-sort={sort === index ? descending ? "descending" : "ascending" : "none"}>
        <button type="button" onClick={() => { setDescending(sort === index && !descending); setSort(index); }}>{displayText(column)}</button>
      </th>)}{movementColumn >= 0 && <th scope="col">Details</th>}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{data.columns.map((column, cell) => <td key={cell}>
        {row[cell] == null ? "—" : column.endsWith("_paise") && typeof row[cell] === "number" ? formatPaise(row[cell]) : displayText(String(row[cell]))}
        {cell === amountColumn && peak > 0 && typeof row[cell] === "number" && <span aria-hidden="true" style={{ display: "block", height: 3, width: `${Math.abs(Number(row[cell])) / peak * 100}%`, background: "var(--cyan)" }} />}
      </td>)}{movementColumn >= 0 && <td>{typeof row[movementColumn] === "string" && <button type="button" onClick={() => setMovementId(String(row[movementColumn]))}>Inspect</button>}</td>}</tr>)}</tbody></table>
    </div>
    {movementId && <MovementDetails key={movementId} id={movementId} />}
    {data.truncated && <p>Results are truncated at 1,000 rows.</p>}
  </section>;
}

function dateIn(days: number): string {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return isoDate(date);
}

function daysFromToday(value: string | undefined): number {
  if (!value) return 0;
  const days = Math.round((new Date(`${value}T00:00:00`).getTime() - new Date(`${dateIn(0)}T00:00:00`).getTime()) / 86_400_000);
  return Number.isFinite(days) ? days : 0;
}

/** All what-if figures come from the same projection endpoint as the Money page. */
export function AffordabilityLab({ seed, initialTimeline = false }: { seed: ProjectionPayload; initialTimeline?: boolean }) {
  const purchaseSeed = seed.request?.hypothetical ?? seed.hypothetical;
  const initialPrice = purchaseSeed?.amount_paise ?? 5000000;
  const initialDays = daysFromToday(purchaseSeed?.date);
  const [price, setPrice] = useState(initialPrice);
  const [days, setDays] = useState(initialDays);
  const [method, setMethod] = useState(purchaseSeed?.method ?? "account");
  const [emi, setEmi] = useState(purchaseSeed?.emi_months ?? 3);
  const [cut, setCut] = useState(seed.request?.cut_percent ?? 0);
  const [hasPurchase, setHasPurchase] = useState(Boolean(purchaseSeed));
  const [edited, setEdited] = useState(false);
  const [data, setData] = useState<ProjectionPayload | null>(seed);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [timeline, setTimeline] = useState(initialTimeline);
  const [workbook, setWorkbook] = useState(() => workbookUrl(seed.request));
  useEffect(() => {
    if (!edited) return;
    let active = true;
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({ horizon_days: String(seed.horizon_days) });
      if (hasPurchase) {
        params.set("amount_paise", String(price));
        params.set("date", dateIn(days));
        params.set("method", method);
        if (method === "emi") params.set("emi_months", String(emi));
      }
      const purchase = seed.request?.hypothetical;
      if (hasPurchase && purchase?.account) params.set("account", purchase.account);
      if (hasPurchase && method === "emi" && purchase?.emi_rate_percent != null) params.set("emi_rate_percent", String(purchase.emi_rate_percent));
      if (hasPurchase && method === "emi" && purchase?.emi_fee_paise != null) params.set("emi_fee_paise", String(purchase.emi_fee_paise));
      if (hasPurchase && method === "emi" && purchase?.no_cost) params.set("no_cost", "true");
      if (cut) params.set("cut_percent", String(cut));
      if (seed.request?.changes?.length) params.set("changes", JSON.stringify(seed.request.changes));
      setLoading(true);
      api<unknown>(`/api/projection?${params}`).then((result) => {
        if (!active) return;
        const parsed = parseProjectionReceipt(result);
        setData(parsed);
        if (parsed) setWorkbook(`/api/projection/workbook?${params}`);
        setError(parsed ? null : "The projection response could not be read.");
      }).catch((caught: unknown) => { if (active) setError(errorMessage(caught)); })
        .finally(() => { if (active) setLoading(false); });
    }, 350);
    return () => { active = false; window.clearTimeout(timer); };
  }, [edited, hasPurchase, price, days, method, emi, cut, seed.horizon_days, seed.request]);
  const readiness = data ? projectionReadiness(data) : null;
  return <section className="chat-card planner [&_input[type=range]]:accent-[var(--lime)]" aria-label="Affordability lab">
    <h3>Affordability lab</h3>
    {!edited && <p>The result below is the original projection. Adjust a control to explore a purchase.</p>}
    <label>Price {formatPaise(price)}<input type="range" min={Math.min(100000, initialPrice)} max={Math.max(50000000, initialPrice)} step="1" value={price} onChange={(event) => { setLoading(true); setEdited(true); setHasPurchase(true); setPrice(Number(event.target.value)); }} /></label>
    <label>Purchase date {formatDate(dateIn(days))}<input type="range" min={Math.min(0, initialDays)} max={Math.max(60, initialDays)} value={days} onChange={(event) => { setLoading(true); setEdited(true); setHasPurchase(true); setDays(Number(event.target.value)); }} /></label>
    <label>Payment method <select value={method} onChange={(event) => { setLoading(true); setEdited(true); setHasPurchase(true); setMethod(event.target.value); }}><option value="account">From an account</option><option value="card">Card in full</option><option value="emi">Card EMI</option></select></label>
    {method === "emi" && <label>EMI months <select value={emi} onChange={(event) => { setLoading(true); setEdited(true); setEmi(Number(event.target.value)); }}>{[3, 6, 9, 12, 18, 24].map((n) => <option key={n}>{n}</option>)}</select></label>}
    <label>Reducible spending cut {cut}%<input type="range" min="0" max="50" step="5" value={cut} onChange={(event) => { setLoading(true); setEdited(true); setCut(Number(event.target.value)); }} /></label>
    {loading && <p role="status">Updating projection…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && data && readiness && <>
      {readiness.ready && (data.floor_set ?? (data.floor_paise ?? 0) > 0) && <div className="planner-gauge" style={{ ["--arc" as string]: `${Math.round(data.floor_probability * 360)}deg` }}><strong>{percent(data.floor_probability)}</strong><span>chance the floor holds</span></div>}
      <button type="button" onClick={() => setTimeline(!timeline)}>{timeline ? "Show projection details" : "Show spending timeline"}</button>
      {timeline ? <TimelineStrip payload={data} /> : <ProjectionBody payload={data} />}
      {readiness.ready && <GoalProgressRing payload={data} />}
      <a href={workbook} download>Download workbook (.xlsx)</a>
    </>}
  </section>;
}

export function TimelineStrip({ payload }: { payload: ProjectionPayload }) {
  const readiness = projectionReadiness(payload);
  return readiness.ready ? <Timeline payload={payload} /> : <p>{readiness.message}</p>;
}

export function StoryCard({ periodId }: { periodId: string }) {
  return <RecapsPage inline periodId={periodId} />;
}

export function ImportReviewWidget({ importId }: { importId?: string | null }) {
  return <ImportsPanel importId={importId} reviewOnly />;
}

export function ToolWidgets({ toolName, result }: { toolName: string; args: unknown; result: unknown }) {
  const queryId = useId();
  if (toolName === "run_query") {
    const parsed = queryResult.safeParse(result);
    if (!parsed.success) return null;
    const { columns, rows } = parsed.data;
    const label = columns.indexOf("label");
    const metric = columns.indexOf("metric_paise");
    const baseline = columns.indexOf("baseline_paise");
    const amount = columns.indexOf("amount_paise");
    const from = columns.indexOf("from_paise");
    const to = columns.indexOf("to_paise");
    const first = rows[0];
    const recap = columns.indexOf("recap_id");
    if (rows.length === 1 && recap >= 0 && typeof first[recap] === "string") {
      return <><StoryCard periodId={first[recap]} /><BreakdownTable data={parsed.data} /></>;
    }
    if (rows.length === 1 && label >= 0 && metric >= 0 && typeof first[label] === "string" && typeof first[metric] === "number" && Number.isSafeInteger(first[metric])) {
      const previous = baseline >= 0 && typeof first[baseline] === "number" && Number.isSafeInteger(first[baseline]) && Number.isSafeInteger(first[metric] - first[baseline]) ? first[baseline] : undefined;
      return <>{renderWidget({ _type: "MetricHero", label: first[label], amount: first[metric], baseline: previous, queryId })}<BreakdownTable id={queryId} data={parsed.data} /></>;
    }
    if (label >= 0 && amount >= 0 && from >= 0 && to >= 0 && rows.length > 0 && rows.every((row) => typeof row[label] === "string" && [row[amount], row[from], row[to]].every((value) => typeof value === "number" && Number.isSafeInteger(value)) && Number(row[to]) - Number(row[from]) === row[amount])) {
      const chart = rows.map((row) => ({ label: String(row[label]), amount: Number(row[amount]), from: Number(row[from]), to: Number(row[to]) }));
      return <>{renderWidget({ _type: "WaterfallChart", rows: chart })}<BreakdownTable data={parsed.data} /></>;
    }
    if (label >= 0 && amount >= 0 && rows.length > 0 && rows.every((row) => typeof row[label] === "string" && typeof row[amount] === "number" && Number.isSafeInteger(row[amount]))) {
      const chart = rows.map((row) => ({ label: String(row[label]), amount: Number(row[amount]) }));
      return <>{renderWidget({ _type: "InteractiveChart", rows: chart })}<BreakdownTable data={parsed.data} /></>;
    }
    return <BreakdownTable data={parsed.data} />;
  }
  if (toolName === "run_python") {
    const output = typeof result === "object" && result !== null && "result_json" in result ? result.result_json : null;
    const parsed = scenarios.safeParse(output);
    return parsed.success ? renderWidget({ _type: "ComparisonMatrix", data: parsed.data }) : null;
  }
  const card = cardOf(result);
  if (card?.card === "import" && typeof card.import_id === "string") return <ImportReviewWidget importId={card.import_id} />;
  return card ? <ActionReceipt data={card} /> : null;
}
