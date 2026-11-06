import { parseAnswerCard, parseResultCard, type AnswerResult } from "./answer-data";

export type AnswerView = "Summary" | "Table" | "Chart" | "Receipt";
export type MessageCoverage = Map<string, Set<AnswerView>>;

export type CardPart = {
  readonly type: string;
  readonly name?: string;
  readonly data?: unknown;
};

export type SupportingItem =
  | { kind: "result"; index: number; result: AnswerResult; views: AnswerView[] }
  | { kind: "query"; index: number; sql: string; rowCount: number; truncated: boolean }
  | { kind: "unreadable"; index: number };

export type SupportingPlan = {
  items: SupportingItem[];
  /** The first read owns the disclosure when there is no composed answer. */
  ownerIndex: number;
  /** When a valid composed answer exists, the disclosure follows its last card. */
  answerOwnerIndex: number | null;
};

function cardData(part: CardPart): Record<string, unknown> | null {
  if (part.type !== "data" || part.name !== "card") return null;
  return part.data && typeof part.data === "object"
    ? (part.data as Record<string, unknown>)
    : null;
}

export function isRoutineRead(data: Record<string, unknown>): boolean {
  return data.card === "result" || data.card === "query";
}

/** The result views a saved answer already shows, keyed by result_id. */
export function messageCoverage(parts: readonly CardPart[]): MessageCoverage {
  const coverage: MessageCoverage = new Map();
  for (const part of parts) {
    const data = cardData(part);
    if (!data || data.card !== "answer") continue;
    const parsed = parseAnswerCard(data);
    if (!parsed) continue;
    const walk = (node: typeof parsed.display) => {
      if (node.result_id && node._type !== "Stack" && node._type !== "Comparison") {
        const views = coverage.get(node.result_id) ?? new Set<AnswerView>();
        views.add(node._type);
        coverage.set(node.result_id, views);
      }
      for (const child of node.children ?? []) walk(child);
    };
    walk(parsed.display);
  }
  return coverage;
}

export function coveredByNativeCard(
  card: string,
  resultId: string,
  coverage: MessageCoverage,
): boolean {
  const views = coverage.get(resultId);
  if (!views) return false;
  if (card === "projection") return views.has("Receipt");
  if (card === "chart") return views.has("Chart");
  if (card === "query") return views.has("Table") || views.has("Summary");
  return views.size > 0;
}

/** The one view a routine fallback leads with. A table beats a headline count. */
export function fallbackViews(result: AnswerResult): AnswerView[] {
  if (result.receipt) return ["Receipt"];
  if (result.kind === "chart" && result.chart) return ["Chart"];
  if (result.table) return ["Table"];
  if (result.summary) return ["Summary"];
  if (result.chart) return ["Chart"];
  return [];
}

function normalizeSql(sql: string): string {
  // Quoted values can differ by case or whitespace. Only ignore surrounding
  // space when matching the legacy card to its saved result.
  return sql.trim();
}

/**
 * One closed disclosure per assistant message: routine reads in order, minus
 * views the composed answer already shows and legacy SQL cards the result
 * snapshot replaces. The first surviving read is the single render owner.
 */
export function buildSupportingPlan(
  parts: readonly CardPart[],
  coverage: MessageCoverage,
): SupportingPlan | null {
  const resultSql = new Set<string>();
  let answerOwnerIndex: number | null = null;
  parts.forEach((part, index) => {
    const data = cardData(part);
    if (!data) return;
    if (data.card === "answer" && parseAnswerCard(data)) answerOwnerIndex = index;
    if (data.card !== "result") return;
    const result = parseResultCard(data.snapshot);
    const sql = result && typeof result.sql === "string" ? normalizeSql(result.sql) : "";
    if (sql) resultSql.add(sql);
  });

  const seenResults = new Set<string>();
  const seenQueries = new Set<string>();
  const items: SupportingItem[] = [];

  parts.forEach((part, index) => {
    const data = cardData(part);
    if (!data) return;

    if (data.card === "result") {
      const result = parseResultCard(data.snapshot);
      if (!result) {
        items.push({ kind: "unreadable", index });
        return;
      }
      if (seenResults.has(result.result_id)) return;
      seenResults.add(result.result_id);
      const covered = coverage.get(result.result_id);
      // A composed answer already showed this emptiness; a second empty
      // section in Supporting data would just repeat it.
      if (result.empty && covered?.size) return;
      const views = fallbackViews(result).filter((view) => !covered?.has(view));
      if (!views.length) return;
      items.push({ kind: "result", index, result, views });
      return;
    }

    if (data.card === "query") {
      const sql = typeof data.sql === "string" ? data.sql : "";
      const normalized = normalizeSql(sql);
      if (normalized && resultSql.has(normalized)) return;
      const key = normalized || `rows:${String(data.row_count ?? "")}`;
      if (seenQueries.has(key)) return;
      seenQueries.add(key);
      const rowCount = Number(data.row_count);
      items.push({
        kind: "query",
        index,
        sql,
        rowCount: Number.isFinite(rowCount) ? rowCount : 0,
        truncated: Boolean(data.truncated),
      });
    }
  });

  if (!items.length) return null;
  return { items, ownerIndex: items[0].index, answerOwnerIndex };
}
