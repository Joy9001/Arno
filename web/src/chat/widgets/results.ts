import { z } from "zod";

export const queryResult = z.object({
  columns: z.array(z.string()).min(1),
  rows: z.array(z.array(z.union([z.string(), z.number(), z.boolean(), z.null()]))),
  truncated: z.boolean(),
});

export const scenarios = z.object({ scenarios: z.array(z.object({
  label: z.string(), tenure_months: z.number().int().positive(),
  prepayment_paise: z.number().int().safe().nonnegative(), monthly_paise: z.number().int().safe().nonnegative(),
  interest_paise: z.number().int().safe().nonnegative(), total_paise: z.number().int().safe().nonnegative(),
})).min(1).max(200) });

const record = (value: unknown): Record<string, unknown> | null =>
  typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;

export function cardOf(result: unknown): Record<string, unknown> | null {
  return record(record(result)?.card);
}

export function hasToolWidget(toolName: string, result: unknown): boolean {
  if (toolName === "run_query") return queryResult.safeParse(result).success;
  if (toolName === "run_python") return scenarios.safeParse(record(result)?.result_json).success;
  return ["record_movement", "record_movements", "correct_movement", "update_setup", "preference", "import_document", "undo"]
    .includes(toolName) && typeof cardOf(result)?.card === "string";
}
