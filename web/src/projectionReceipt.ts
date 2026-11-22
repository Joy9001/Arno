import { z } from "zod";
import { displayText } from "./chat/display-text";
import { formatPaise } from "./format";

const number = z.number().finite();
const money = z.object({ paise: number.safe(), text: z.string() });
const optionalText = z.string().nullish().transform((value) => value ?? undefined);
const displayString = z.string().transform(displayText);
const displayOptional = z.string().nullish().transform((value) =>
  value == null ? undefined : displayText(value),
);

/**
 * The lowest median arrives as `{ amount: { paise, text } }` in a chat card and as a bare
 * `paise` in `GET /api/projection`. Normalize to `paise` once, here at the ingress.
 */
const medianLow = z
  .object({
    date: z.string(),
    amount: money.optional(),
    paise: number.optional(),
  })
  .transform((value) => ({ ...value, paise: value.amount?.paise ?? value.paise }));

/** Same dual shape for safe to spend; keep `binding_date` so an amount without it stays hidden. */
const safeToSpend = z
  .object({
    amount: money.optional(),
    amount_paise: number.optional(),
    binding: z.enum(["floor", "obligation"]),
    binding_date: z.string(),
  })
  .refine((value) => value.amount !== undefined || value.amount_paise !== undefined, {
    message: "safe_to_spend requires amount or amount_paise",
  })
  .transform((value) => ({ ...value, amount_paise: value.amount?.paise ?? value.amount_paise ?? 0 }));

const scenarioChange = z
  .object({
    kind: z.string(),
    label: z.string(),
    occurrences: number.int().min(0),
    amount: money.optional(),
    amount_paise: number.optional(),
  })
  .refine((value) => value.amount !== undefined || value.amount_paise !== undefined, {
    message: "scenario change requires amount or amount_paise",
  });

const scenario = z.object({
  changes: z.array(scenarioChange),
  assumptions: z.array(z.string()),
  holds: z.boolean().optional(),
  reason: displayOptional,
  runway_days: number.int().min(0).nullable(),
  baseline: z.object({
    floor_probability: number.min(0).max(1),
    median_low: medianLow.nullable(),
    first_floor_crossing: z.string().nullable().default(null),
    safe_to_spend: safeToSpend.nullable().optional(),
  }),
});

/** The arguments one projection was asked with, echoed by the tool for the workbook link. */
const request = z.object({
  horizon_days: number.int().nullable().optional(),
  hypothetical: z.object({
    amount_paise: number.optional(),
    date: optionalText,
    method: optionalText,
    emi_months: number.nullable().optional(),
    account: optionalText,
    emi_rate_percent: number.nullable().optional(),
    emi_fee_paise: number.nullable().optional(),
    no_cost: z.boolean().optional(),
  }).loose().optional(),
  changes: z.array(z.record(z.string(), z.unknown())).optional(),
});

/**
 * Validate every field consumed by the receipt, its expanded details, and evidence panel.
 * Untrusted display strings lose their internal `<outside>` envelope here; the raw `request`
 * is left untouched so the workbook link still echoes exactly what was asked.
 */
export const projectionSchema = z
  .object({
    card: z.string().optional(),
    title: displayOptional,
    as_of: z.string(),
    horizon_days: number.int().positive(),
    curve: z.array(z.object({
      date: z.string(), median_paise: number, p10_paise: number, p90_paise: number,
    })).max(4000),
    floor: money.nullable().optional(),
    floor_paise: number.optional(),
    floor_set: z.boolean().optional(),
    floor_probability: number.min(0).max(1),
    floor_threshold: number.min(0).max(1),
    median_low: medianLow,
    first_floor_crossing: z.string().nullable().default(null),
    goals: z.array(z.object({
      id: z.string(), name: displayString, target: money.nullable().optional(),
      saved: money.nullable().optional(), remaining_after_plan: money.nullable().optional(),
      deadline: z.string().nullable(), completes_on: z.string().nullable(),
      slips_days: number.nullable(), reached: z.boolean(),
    })).optional(),
    obligations_met: z.boolean().optional(),
    safe_to_spend: safeToSpend.nullable().optional(),
    data_quality: z.object({
      balance_ages: z.array(z.object({
        account: z.string(), as_of: z.string().nullable(), age_days: number.nullable(),
        amount_paise: number.optional(), known: z.boolean().optional(),
      })).optional(),
      baseline_sources: z.array(z.object({
        category: z.string(), source: z.string(), amount_per_day_paise: number,
        real_days: number.optional(),
      })).optional(),
      gaps: z.array(z.string()).optional(), setup_gaps: z.array(z.string()).optional(),
      simulation: z.object({
        categories_sampled: z.array(z.string()).optional(),
        categories_flat: z.array(z.string()).optional(),
        resampling: optionalText, runs: number.optional(),
      }).optional(),
    }),
    runs: number.optional(),
    sources: z.array(z.object({
      kind: z.string(), id: z.string(), name: z.string(), detail: optionalText,
      amount_paise: number.optional(),
    }).loose()).optional(),
    source_ids: z.record(z.string(), z.array(z.string())).optional(),
    optimistic: z.object({
      floor_probability: number.min(0).max(1),
      median_low: z.object({ date: z.string(), paise: number }).optional(),
      income: z.array(z.object({ date: z.string(), name: z.string(), amount_paise: number })).optional(),
    }).nullable().optional(),
    verdict: z.enum(["yes", "yes-but", "no"]).optional(),
    verdict_reason: displayOptional,
    tradeoffs: z.array(z.object({
      kind: displayString, detail: displayString, date: optionalText, days: number.optional(),
      method: optionalText, account: z.string().nullable().optional(), due_date: optionalText,
      slips_days: number.nullable().optional(), until: optionalText,
      categories: z.array(z.object({ category: z.string(), percent: number })).optional(),
    })).optional(),
    goal_slips: z.array(z.object({ name: displayString, slips_days: number.nullable() })).optional(),
    hypothetical: z.object({
      amount_paise: number.optional(), date: optionalText, method: optionalText,
      emi_months: number.nullable().optional(),
    }).loose().optional(),
    baseline: z.object({ floor_probability: number.min(0).max(1) }).optional(),
    scenario: scenario.optional(),
    request: request.optional(),
  })
  .loose()
  .transform((value) => {
    const floor =
      value.floor ??
      (typeof value.floor_paise === "number"
        ? { paise: value.floor_paise, text: formatPaise(value.floor_paise) }
        : value.floor);
    return { ...value, floor, floor_paise: value.floor_paise ?? floor?.paise };
  });

/** The one normalized receipt every renderer reads. */
export type ProjectionReceipt = z.infer<typeof projectionSchema>;

/** The single ingress for live API responses, chat cards, and older saved transcripts. */
export function parseProjectionReceipt(value: unknown): ProjectionReceipt | null {
  const parsed = projectionSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

/** Flow shows a receipt only once the engine has reached an affordability verdict. */
export function parseVerdictReceipt(value: unknown): ProjectionReceipt | null {
  const receipt = parseProjectionReceipt(value);
  return receipt?.verdict ? receipt : null;
}
