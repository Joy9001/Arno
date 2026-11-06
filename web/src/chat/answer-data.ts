import { z } from "zod";
import { projectionSchema } from "./projection-schema";

const ANSWER_TYPES = ["Stack", "Comparison", "Summary", "Table", "Chart", "Receipt"] as const;
export const LEAF_TYPES = new Set(["Summary", "Table", "Chart", "Receipt"]);

export type AnswerNode = {
  _type: (typeof ANSWER_TYPES)[number];
  children?: AnswerNode[];
  result_id?: string;
};

const nodeSchema: z.ZodType<AnswerNode> = z.lazy(() =>
  z.strictObject({
    _type: z.enum(ANSWER_TYPES),
    children: z.array(nodeSchema).optional(),
    result_id: z.string().min(1).max(80).optional(),
  }),
);

const moneyCellSchema = z.object({
  type: z.literal("money"),
  paise: z.number().safe(),
  text: z.string(),
});
const numberCellSchema = z.object({ type: z.literal("number"), value: z.number().finite() });
const textCellSchema = z.object({ type: z.literal("text"), text: z.string() });
const unknownCellSchema = z.object({ type: z.literal("unknown") });
const cellSchema = z.discriminatedUnion("type", [
  moneyCellSchema,
  numberCellSchema,
  textCellSchema,
  unknownCellSchema,
]);

const columnSchema = z.object({
  key: z.string(),
  label: z.string(),
  type: z.enum(["money", "number", "text", "date"]),
});

const tableSchema = z
  .object({
    caption: z.string().optional(),
    columns: z.array(columnSchema).min(1).max(12),
    rows: z.array(z.array(cellSchema)).max(50),
    shown_rows: z.number().optional(),
    total_rows: z.number().optional(),
    truncated: z.boolean().optional(),
    note: z.string().nullable().optional(),
  })
  .loose().refine((table) =>
    new Set(table.columns.map((column) => column.key)).size === table.columns.length &&
    table.rows.every((row) => row.length === table.columns.length),
  );

const summarySchema = z
  .object({
    facts: z.array(z.object({ label: z.string(), value: cellSchema })).optional(),
    description: z.string().nullable().optional(),
  })
  .loose();

const coverageSchema = z.object({
  complete: z.boolean().optional(), gaps: z.array(z.string()).optional(),
}).loose();
const periodSchema = z.object({
  from: z.string().optional(), to: z.string().optional(), label: z.string().optional(),
}).loose();
const evidenceSchema = z.object({
  notes: z.array(z.string()).optional(),
  coverage: coverageSchema.optional(),
  assumptions: z.array(z.string()).optional(),
  source_ids: z.record(z.string(), z.array(z.string())).optional(),
  as_of: z.string().nullable().optional(),
  period: periodSchema.optional(),
  filters: z.record(z.string(), z.unknown()).optional(),
  sql: z.string().optional(),
});
export const chartSchema = z.object({
  type: z.enum(["line", "bar", "stacked_bar", "donut"]),
  title: z.string(), unit: z.enum(["paise", "count", "percent"]),
  series: z.array(z.object({
    name: z.string(),
    points: z.array(z.object({ x: z.string(), y: z.number().finite() })).max(400),
  })).min(1).max(8),
  as_of: z.string().nullable().optional(), computed_at: z.string().nullable().optional(),
  script: z.string().optional(), source: z.string().optional(),
}).loose();

const resultSchema = z
  .object({
    result_id: z.string(),
    kind: z.string(),
    title: z.string(),
    views: z.array(z.enum(["Summary", "Table", "Chart", "Receipt"])).min(1).max(4),
    summary: summarySchema.optional(),
    table: tableSchema.optional(),
    chart: chartSchema.optional(),
    receipt: projectionSchema.optional(),
    ...evidenceSchema.shape,
    sources: z.array(evidenceSchema.extend({
      id: z.string(), title: z.string(), views: z.array(z.string()).optional(),
    })).max(48).optional(),
    empty: z.boolean().optional(),
    source_result_ids: z.array(z.string()).optional(),
  })
  .loose().refine((result) => result.views.every((view) => {
    if (view === "Summary") return result.summary !== undefined;
    if (view === "Table") return result.table !== undefined;
    if (view === "Chart") return result.chart !== undefined;
    return result.receipt !== undefined;
  }));

const answerSchema = z
  .object({
    card: z.literal("answer"),
    version: z.literal(1),
    display: nodeSchema,
    results: z.array(resultSchema).min(1).max(12),
  })
  .loose();

export type AnswerResult = z.infer<typeof resultSchema>;
export type AnswerCell = z.infer<typeof cellSchema>;

export type ParsedAnswer = {
  display: AnswerNode;
  results: AnswerResult[];
};

function isLeaf(node: AnswerNode): boolean {
  return LEAF_TYPES.has(node._type);
}

// Bound work before the recursive schema parser touches untrusted saved content.
function boundedTree(value: unknown): boolean {
  const pending = [{ node: value, depth: 0 }];
  let count = 0;
  while (pending.length) {
    const entry = pending.pop()!;
    if (++count > 12 || entry.depth > 3 || !entry.node || typeof entry.node !== "object") {
      return false;
    }
    const children = (entry.node as Record<string, unknown>).children;
    if (children !== undefined) {
      if (!Array.isArray(children) || children.length > 12) return false;
      pending.push(...children.map((node) => ({ node, depth: entry.depth + 1 })));
    }
  }
  return true;
}

function validTree(
  node: AnswerNode,
  byId: Map<string, AnswerResult>,
  containers: number,
  count: { nodes: number },
): boolean {
  count.nodes += 1;
  if (count.nodes > 12) return false;
  if (!isLeaf(node)) {
    const nextContainers = containers + 1;
    if (nextContainers > 3 || node.result_id || !node.children?.length) return false;
    if (node._type === "Comparison") {
      if (node.children.length < 2 || node.children.some((child) => !isLeaf(child))) {
        return false;
      }
    }
    return node.children.every((child) =>
      validTree(child, byId, nextContainers, count),
    );
  }
  if (node.children?.length || !node.result_id) return false;
  const result = byId.get(node.result_id);
  return Boolean(result?.views.some((view) => view === node._type));
}

/** Validates a saved answer card. A malformed card renders an unavailable message, never data. */
export function parseAnswerCard(data: unknown): ParsedAnswer | null {
  if (!data || typeof data !== "object" || !boundedTree((data as Record<string, unknown>).display)) {
    return null;
  }
  const parsed = answerSchema.safeParse(data);
  if (!parsed.success) return null;
  const byId = new Map(parsed.data.results.map((result) => [result.result_id, result]));
  if (byId.size !== parsed.data.results.length) return null;
  if (!validTree(parsed.data.display, byId, 0, { nodes: 0 })) return null;
  return { display: parsed.data.display, results: parsed.data.results };
}

export function parseResultCard(data: unknown): AnswerResult | null {
  const parsed = resultSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}
