import { Fragment, useId, useMemo, useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProjectionCard, type ProjectionPayload } from "../projection";
import {
  parseAnswerCard,
  parseResultCard,
  type AnswerCell,
  type AnswerNode,
  type AnswerResult,
} from "./answer-data";
import { ChartCard } from "./chart";
import { displayText } from "./display-text";
import { formatRupees } from "./format";
import { AnswerBoundary } from "./answer-boundary";
import {
  fallbackViews,
  type AnswerView,
  type SupportingItem,
} from "./supporting-data";

const INITIAL_TABLE_ROWS = 10;
const CONTEXT_LABELS = new Set(["period", "as of"]);

export function Unavailable({ what }: { what: string }) {
  return (
    <p className="answer-unavailable" role="status">
      {what} could not be displayed. Ask again to rebuild it.
    </p>
  );
}

function cellText(cell: AnswerCell): ReactNode {
  if (cell.type === "money") return <span className="tabular">{formatRupees(cell.paise)}</span>;
  if (cell.type === "number") {
    return <span className="tabular">{new Intl.NumberFormat("en-IN").format(cell.value)}</span>;
  }
  if (cell.type === "text") return displayText(cell.text);
  return <span className="answer-unknown">Unknown</span>;
}

function periodText(result: AnswerResult): string | null {
  const period = result.period;
  if (!period) return null;
  if (period.from && period.to) {
    return displayText(`${period.from} to ${period.to}`);
  }
  return period.label ? displayText(period.label) : null;
}

function resultContext(result: AnswerResult): string | null {
  const title = displayText(result.title);
  const period = periodText(result);
  if (period && !title.includes(period)) return period;
  const asOf = result.as_of ? displayText(result.as_of) : null;
  if (asOf && !title.includes(asOf)) return `As of ${asOf}`;
  return null;
}

function CoverageNote({ result }: { result: AnswerResult }) {
  const gaps = (result.coverage?.gaps ?? []).map(displayText);
  if (!gaps.length && result.coverage?.complete !== false) return null;
  return (
    <p className="answer-coverage">
      {gaps.length
        ? `No statement covers ${gaps.join(", ")}. Values from those periods are not a complete total.`
        : "Statement coverage is incomplete. These values may not represent the full period."}
    </p>
  );
}

function hasEvidence(result: AnswerResult): boolean {
  return Boolean(
    result.notes?.length ||
      result.assumptions?.length ||
      result.sql ||
      result.sources?.length ||
      Object.keys(result.source_ids ?? {}).length ||
      Object.values(result.filters ?? {}).some(
        (value) => value !== null && value !== undefined && value !== "",
      ),
  );
}

function AnswerSources({ result }: { result: AnswerResult }) {
  const notes = (result.notes ?? [])
    .map(displayText)
    .filter((note) => !note.startsWith("No statement covers"));
  const sourceIds = Object.entries(result.source_ids ?? {}).flatMap(([kind, ids]) =>
    ids.map((id) => `${displayText(kind)}: ${displayText(id)}`),
  );
  const sources = result.sources ?? [];
  const assumptions = (result.assumptions ?? []).map(displayText);
  const filterText = result.filters
    ? Object.entries(result.filters)
        .filter(([key, value]) => key !== "period" && value !== null && value !== undefined && value !== "")
        .map(([key, value]) => `${displayText(key.replaceAll("_", " "))}: ${displayText(String(value))}`)
        .join(" · ")
    : "";
  const hasDetails =
    notes.length || sources.length || assumptions.length || result.sql || sourceIds.length || filterText;

  if (!hasDetails) return null;
  return (
    <details className="answer-sources">
      <summary>{assumptions.length || notes.length ? "Sources and assumptions" : "Sources"}</summary>
      {filterText ? <p className="fine">Filters: {filterText}</p> : null}
      {notes.length ? (
        <ul>
          {notes.map((note, index) => (
            <li key={`${note}-${index}`}>{note}</li>
          ))}
        </ul>
      ) : null}
      {sources.length ? (
        sources.map((source) => <SourceEvidence key={source.id} source={source} />)
      ) : sourceIds.length ? (
        <p className="fine answer-source-ids">{sourceIds.join(" · ")}</p>
      ) : null}
      {assumptions.length ? (
        <ul>
          {assumptions.map((note, index) => (
            <li key={`${note}-${index}`}>{note}</li>
          ))}
        </ul>
      ) : null}
      {result.sql ? <pre className="sql">{displayText(result.sql)}</pre> : null}
    </details>
  );
}

function SourceEvidence({ source }: { source: NonNullable<AnswerResult["sources"]>[number] }) {
  const ids = Object.entries(source.source_ids ?? {}).flatMap(([kind, values]) =>
    values.map((id) => `${displayText(kind)}: ${displayText(id)}`),
  );
  const period = source.period;
  return (
    <details className="answer-source-evidence">
      <summary>{displayText(source.title)}</summary>
      {period ? (
        <p>
          Period:{" "}
          {period.from && period.to
            ? displayText(`${period.from} to ${period.to}`)
            : period.label
              ? displayText(period.label)
              : "the period"}
        </p>
      ) : null}
      {source.as_of ? <p>As of: {displayText(source.as_of)}</p> : null}
      {source.filters ? (
        <p>
          Filters:{" "}
          {Object.entries(source.filters)
            .map(([key, value]) => `${displayText(key)}: ${displayText(String(value))}`)
            .join(" · ")}
        </p>
      ) : null}
      {source.coverage?.complete === false ? <p>Statement coverage is incomplete.</p> : null}
      {[...(source.notes ?? []), ...(source.assumptions ?? [])].map((note, index) => (
        <p key={index}>{displayText(note)}</p>
      ))}
      {ids.length ? <p className="answer-source-ids">{ids.join(" · ")}</p> : null}
      {source.sql ? <pre className="sql">{displayText(source.sql)}</pre> : null}
    </details>
  );
}

function SummaryBody({ result }: { result: AnswerResult }) {
  const facts = (result.summary?.facts ?? []).map((fact) => ({
    ...fact,
    label: displayText(fact.label),
  }));
  const useful = facts.filter(
    (fact) => !CONTEXT_LABELS.has(fact.label.trim().toLowerCase()),
  );
  const description = result.summary?.description
    ? displayText(result.summary.description)
    : null;
  if (!useful.length) {
    return description ? <p className="answer-summary-note">{description}</p> : null;
  }
  const [primary, ...rest] = useful;
  return (
    <div className="answer-summary">
      <dl className="answer-primary">
        <div>
          <dt>{primary.label}</dt>
          <dd>{cellText(primary.value)}</dd>
        </div>
      </dl>
      {rest.length ? (
        <dl className="answer-facts">
          {rest.map((fact, index) => (
            <div key={`${fact.label}-${index}`}>
              <dt>{fact.label}</dt>
              <dd>{cellText(fact.value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {description ? <p className="answer-summary-note">{description}</p> : null}
    </div>
  );
}

function TableBody({
  result,
  table,
}: {
  result: AnswerResult;
  table: NonNullable<AnswerResult["table"]>;
}) {
  const [expanded, setExpanded] = useState(false);
  const tableId = useId();
  const rows = expanded ? table.rows : table.rows.slice(0, INITIAL_TABLE_ROWS);
  const hiddenRows = table.rows.length - rows.length;
  return (
    <>
      <div className="answer-table-wrap">
        <table id={tableId}>
          <caption className="aui-sr-only">
            {displayText(table.caption ?? result.title)}
          </caption>
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={
                    column.type === "money" || column.type === "number" ? "numeric" : undefined
                  }
                >
                  {displayText(column.label)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td
                    key={table.columns[cellIndex]?.key ?? cellIndex}
                    className={
                      table.columns[cellIndex]?.type === "money" ||
                      table.columns[cellIndex]?.type === "number"
                        ? "numeric"
                        : undefined
                    }
                  >
                    {cellText(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.rows.length > INITIAL_TABLE_ROWS ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="answer-show-all"
          aria-expanded={expanded}
          aria-controls={tableId}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show fewer rows" : `Show all ${table.rows.length} rows`}
          <span className="aui-sr-only">{hiddenRows ? `, ${hiddenRows} hidden` : ""}</span>
        </Button>
      ) : null}
      {table.note && !table.note.startsWith("No statement covers") ? (
        <p className="answer-table-note">{displayText(table.note)}</p>
      ) : null}
    </>
  );
}

function ResultSection({
  result,
  views,
  showTitle,
  showSources,
  onEvidence,
}: {
  result: AnswerResult;
  views: AnswerView[];
  showTitle: boolean;
  showSources: boolean;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const table = result.table;
  const hasReceipt = views.includes("Receipt") && Boolean(result.receipt);
  const hasChart = views.includes("Chart") && Boolean(result.chart);
  const hasTable = views.includes("Table") && Boolean(table);
  const hasSummary = views.includes("Summary") && Boolean(result.summary) && !hasReceipt;
  const rowCount = table?.rows.length ?? 0;
  const empty = !hasReceipt && (result.empty === true || (hasTable && rowCount === 0 && !hasChart));

  if (!hasReceipt && !hasChart && !hasTable && !hasSummary) {
    return <Unavailable what="This result" />;
  }

  const context = resultContext(result);
  return (
    <section className="answer-result" aria-label={displayText(result.title)}>
      {hasReceipt ? <ProjectionCard data={result.receipt!} onEvidence={onEvidence} /> : null}
      {!hasReceipt && showTitle ? (
        <h3 className="answer-result-title">{displayText(result.title)}</h3>
      ) : null}
      {!hasReceipt && showTitle && context ? (
        <p className="answer-context">{context}</p>
      ) : null}
      {empty ? <p className="answer-empty">No rows were returned for this result.</p> : null}
      {!empty && hasSummary ? <SummaryBody result={result} /> : null}
      {!empty && hasTable && table ? <TableBody result={result} table={table} /> : null}
      {!empty && hasChart && result.chart ? (
        <ChartCard data={result.chart} showTitle={!showTitle} />
      ) : null}
      {!hasReceipt ? <CoverageNote result={result} /> : null}
      {showSources && !hasReceipt ? <AnswerSources result={result} /> : null}
    </section>
  );
}

function claimTitle(seen: { titles: Set<string> }, resultId: string): boolean {
  if (seen.titles.has(resultId)) return false;
  seen.titles.add(resultId);
  return true;
}

function claimSources(
  seen: { sources: Set<string> },
  result: AnswerResult,
): boolean {
  if (result.receipt || seen.sources.has(result.result_id)) return false;
  seen.sources.add(result.result_id);
  return hasEvidence(result);
}

type StackGroup =
  | { kind: "leaves"; resultId: string; views: AnswerView[] }
  | { kind: "node"; node: AnswerNode };

function isLeaf(node: AnswerNode): node is AnswerNode & { _type: AnswerView } {
  return (
    node._type === "Summary" ||
    node._type === "Table" ||
    node._type === "Chart" ||
    node._type === "Receipt"
  );
}

/** Adjacent views of one result read as one section with one title. */
function groupStack(children: AnswerNode[]): StackGroup[] {
  const groups: StackGroup[] = [];
  for (const child of children) {
    if (!isLeaf(child)) {
      groups.push({ kind: "node", node: child });
      continue;
    }
    const resultId = child.result_id!;
    const last = groups.at(-1);
    if (last?.kind === "leaves" && last.resultId === resultId) {
      if (!last.views.includes(child._type)) last.views.push(child._type);
    } else {
      groups.push({ kind: "leaves", resultId, views: [child._type] });
    }
  }
  return groups;
}

function renderNode(
  node: AnswerNode,
  byId: Map<string, AnswerResult>,
  seen: { titles: Set<string>; sources: Set<string> },
  onEvidence?: (payload: ProjectionPayload) => void,
): ReactNode {
  if (node._type === "Stack") {
    return (
      <div className="answer-stack">
        {groupStack(node.children ?? []).map((group, index) => {
          if (group.kind === "node") {
            return (
              <Fragment key={index}>
                {renderNode(group.node, byId, seen, onEvidence)}
              </Fragment>
            );
          }
          const result = byId.get(group.resultId);
          if (!result) return null;
          return (
            <ResultSection
              key={`${group.resultId}-${index}`}
              result={result}
              views={group.views}
              showTitle={claimTitle(seen, group.resultId)}
              showSources={claimSources(seen, result)}
              onEvidence={onEvidence}
            />
          );
        })}
      </div>
    );
  }

  if (node._type === "Comparison") {
    return (
      <div className="answer-comparison" role="group" aria-label="Comparison">
        {(node.children ?? []).map((child, index) => (
          <Fragment key={index}>{renderNode(child, byId, seen, onEvidence)}</Fragment>
        ))}
      </div>
    );
  }

  const result = node.result_id ? byId.get(node.result_id) : undefined;
  if (!result) return null;
  return (
    <ResultSection
      result={result}
      views={[node._type]}
      showTitle={claimTitle(seen, result.result_id)}
      showSources={claimSources(seen, result)}
      onEvidence={onEvidence}
    />
  );
}

function ComposedAnswer({
  display,
  results,
  onEvidence,
}: {
  display: AnswerNode;
  results: AnswerResult[];
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const byId = useMemo(
    () => new Map(results.map((result) => [result.result_id, result])),
    [results],
  );
  const seen = { titles: new Set<string>(), sources: new Set<string>() };
  return (
    <div className="answer-block answer-card">
      {renderNode(display, byId, seen, onEvidence)}
    </div>
  );
}

export function AnswerCard({
  data,
  onEvidence,
}: {
  data: Record<string, unknown>;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const parsed = useMemo(() => parseAnswerCard(data), [data]);
  if (!parsed) return <Unavailable what="This answer" />;
  return (
    <AnswerBoundary resetKey={data} fallback={<Unavailable what="This answer" />}>
      <ComposedAnswer
        display={parsed.display}
        results={parsed.results}
        onEvidence={onEvidence}
      />
    </AnswerBoundary>
  );
}

function LegacyQuery({ item }: { item: Extract<SupportingItem, { kind: "query" }> }) {
  return (
    <section className="answer-result">
      <h3 className="answer-result-title">Read-only query</h3>
      <p className="answer-context">
        {item.rowCount} row{item.rowCount === 1 ? "" : "s"}
        {item.truncated ? " · truncated" : ""}
      </p>
      {item.sql ? (
        <details className="answer-sql">
          <summary>SQL</summary>
          <pre className="sql">{displayText(item.sql)}</pre>
        </details>
      ) : (
        <p className="answer-empty">No SQL was saved for this read.</p>
      )}
    </section>
  );
}

/** One closed disclosure for every routine read in an assistant message. */
export function SupportingData({
  items,
  onEvidence,
}: {
  items: SupportingItem[];
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  if (!items.length) return null;
  return (
    <details className="supporting-data">
      <summary>
        <ChevronRight className="supporting-chevron" aria-hidden="true" size={14} />
        <span>Supporting data</span>
        <span className="supporting-count">
          {items.length} {items.length === 1 ? "read" : "reads"}
        </span>
      </summary>
      <div className="supporting-body">
        {items.map((item, index) => {
          if (item.kind === "result") {
            return (
              <ResultSection
                key={`${item.result.result_id}-${index}`}
                result={item.result}
                views={item.views}
                showTitle
                showSources
                onEvidence={onEvidence}
              />
            );
          }
          if (item.kind === "query") {
            return <LegacyQuery key={`query-${item.index}`} item={item} />;
          }
          return (
            <p key={`unreadable-${item.index}`} className="answer-unavailable">
              This result could not be displayed.
            </p>
          );
        })}
      </div>
    </details>
  );
}

/** The readable fallback for an ordinary read, before any composed answer. */
export function ResultCard({
  data,
  onEvidence,
}: {
  data: Record<string, unknown>;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const result = useMemo(() => parseResultCard(data.snapshot), [data.snapshot]);
  if (!result) return <Unavailable what="This result" />;
  const views = fallbackViews(result);
  if (!views.length) return <Unavailable what="This result" />;
  return (
    <AnswerBoundary resetKey={data} fallback={<Unavailable what="This result" />}>
      <div className="answer-block">
        <ResultSection
          result={result}
          views={views}
          showTitle
          showSources
          onEvidence={onEvidence}
        />
      </div>
    </AnswerBoundary>
  );
}
