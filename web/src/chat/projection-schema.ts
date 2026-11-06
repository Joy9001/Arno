import { z } from "zod";

const number = z.number().finite();
const money = z.object({ paise: number.safe(), text: z.string() });
const optionalText = z.string().nullish().transform((value) => value ?? undefined);

/** Validate every field consumed by the receipt, its expanded details, and evidence panel. */
export const projectionSchema = z.object({
  card: z.string().optional(),
  title: optionalText,
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
  median_low: z.object({ date: z.string(), amount: money.optional(), paise: number.optional() }),
  first_floor_crossing: z.string().nullable().default(null),
  goals: z.array(z.object({
    id: z.string(), name: z.string(), target: money.nullable().optional(),
    saved: money.nullable().optional(), remaining: money.nullable().optional(),
    deadline: z.string().nullable(), completes_on: z.string().nullable(),
    slips_days: number.nullable(), reached: z.boolean(),
  })).optional(),
  obligations_met: z.boolean().optional(),
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
  verdict_reason: optionalText,
  tradeoffs: z.array(z.object({
    kind: z.string(), detail: z.string(), date: optionalText, days: number.optional(),
    method: optionalText, account: z.string().nullable().optional(), due_date: optionalText,
    slips_days: number.nullable().optional(), until: optionalText,
    categories: z.array(z.object({ category: z.string(), percent: number })).optional(),
  })).optional(),
  goal_slips: z.array(z.object({ name: z.string(), slips_days: number.nullable() })).optional(),
  hypothetical: z.object({
    amount_paise: number.optional(), date: optionalText, method: optionalText,
    emi_months: number.nullable().optional(),
  }).loose().optional(),
  baseline: z.object({ floor_probability: number.min(0).max(1) }).optional(),
}).loose();
