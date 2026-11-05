import type { ProjectionPayload } from "./projection";

export const OBLIGATION_WARNING =
  "An obligation cannot be met on its date in some simulated futures.";

export function lowAmount(payload: ProjectionPayload): number | null {
  if (payload.median_low.amount?.paise !== undefined) return payload.median_low.amount.paise;
  if (payload.median_low.paise !== undefined) return payload.median_low.paise;
  return null;
}

export function paymentMethodLabel(method: string | undefined): string {
  if (!method) return "";
  if (method === "account") return "from an account";
  if (method === "card") return "by credit card";
  if (method === "emi") return "card EMI";
  return method.replaceAll("_", " ");
}

export function dataQualityNotes(payload: ProjectionPayload): string[] {
  const quality = payload.data_quality;
  return [
    ...(quality.gaps ?? []),
    ...(quality.setup_gaps ?? []).map((gap) => `setup: ${gap}`),
    ...(quality.simulation?.categories_flat?.length
      ? [
          `flat estimates for ${quality.simulation.categories_flat.join(", ")} (too few real spending days)`,
        ]
      : []),
    ...(quality.simulation?.categories_sampled?.length
      ? [`resampled from real days for ${quality.simulation.categories_sampled.join(", ")}`]
      : []),
  ];
}
