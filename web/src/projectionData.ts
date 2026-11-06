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

export type ProjectionReadiness = {
  ready: boolean;
  unknownAccounts: string[];
  message: string | null;
};

const NO_LIQUID = "no liquid account";
const NO_KNOWN_LIQUID = "no liquid account has a known balance";

function accountList(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

/**
 * A forecast is supported only by known account balances. A known zero and an old-but-stated
 * balance stay supported; only an explicit `known: false` or a liquid-account setup gap removes it.
 */
export function projectionReadiness(payload: ProjectionPayload): ProjectionReadiness {
  const quality = payload.data_quality;
  const setupGaps = quality?.setup_gaps ?? [];
  const unknownRows = (quality?.balance_ages ?? []).filter((row) => row.known === false);
  const unknownAccounts = [
    ...new Set(unknownRows.map((row) => row.account).filter(Boolean)),
  ];
  const noLiquid = setupGaps.includes(NO_LIQUID);
  const noKnownLiquid = setupGaps.includes(NO_KNOWN_LIQUID);
  if (!noLiquid && !noKnownLiquid && unknownRows.length === 0) {
    return { ready: true, unknownAccounts: [], message: null };
  }
  if (noLiquid) {
    return {
      ready: false,
      unknownAccounts,
      message:
        "No liquid account is set up, so the projection has no starting balance. Add a bank, cash, or wallet account with its balance in Money.",
    };
  }
  const names = accountList(unknownAccounts);
  return {
    ready: false,
    unknownAccounts,
    message: names
      ? `The projection needs known account balances. Add a statement balance or import a statement for ${names} in Money.`
      : "The projection has no known liquid balance. Add a statement balance or import a statement in Money.",
  };
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
