import { describe, expect, it } from "vitest";
import { formatPaise } from "./format";
import { parseProjectionReceipt, parseVerdictReceipt } from "./projectionReceipt";

/** The live `GET /api/projection` shape: bare `*_paise`, no card/title, no `request`. */
const live = {
  as_of: "2026-11-06",
  horizon_days: 90,
  curve: [],
  floor_paise: 100_000,
  floor_set: true,
  floor_probability: 0.97,
  floor_threshold: 0.95,
  median_low: { date: "2026-11-20", paise: 300_000 },
  first_floor_crossing: null,
  data_quality: {},
  safe_to_spend: { amount_paise: 200_000, binding: "floor", binding_date: "2026-11-10" },
};

const scenario = {
  changes: [{ kind: "stop_income", label: "Stop income", occurrences: 2, amount_paise: 100_000 }],
  assumptions: [],
  runway_days: 44,
  baseline: {
    floor_probability: 0.96,
    median_low: { date: "2026-11-10", paise: 210_000 },
    first_floor_crossing: null,
    safe_to_spend: { amount_paise: 150_000, binding: "floor", binding_date: "2026-11-10" },
  },
};

describe("parseProjectionReceipt", () => {
  it("normalizes the live bare-paise shape once", () => {
    const receipt = parseProjectionReceipt({ ...live, scenario });
    expect(receipt).not.toBeNull();
    expect(receipt?.floor).toEqual({ paise: 100_000, text: formatPaise(100_000) });
    expect(receipt?.floor_paise).toBe(100_000);
    expect(receipt?.median_low.paise).toBe(300_000);
    expect(receipt?.safe_to_spend?.amount_paise).toBe(200_000);
    expect(receipt?.scenario?.baseline.median_low?.paise).toBe(210_000);
    expect(receipt?.scenario?.baseline.safe_to_spend?.amount_paise).toBe(150_000);
  });

  it("normalizes the chat card money-object shape and leaves request untouched", () => {
    const request = { horizon_days: 180, changes: [{ kind: "stop_income", start: "2026-12-01" }] };
    const receipt = parseProjectionReceipt({
      card: "projection",
      title: "Projection",
      ...live,
      floor_paise: undefined,
      floor: { paise: 100_000, text: "₹1,000.00" },
      median_low: { date: "2026-11-20", amount: { paise: 300_000, text: "₹3,000.00" } },
      safe_to_spend: {
        amount: { paise: 200_000, text: "₹2,000.00" },
        binding: "floor",
        binding_date: "2026-11-10",
      },
      goals: [{
        id: "g1", name: "Trip", target: { paise: 500_000, text: "₹5,000.00" },
        saved: null, remaining_after_plan: null, deadline: null, completes_on: null,
        slips_days: null, reached: false,
      }],
      request,
    });
    expect(receipt).not.toBeNull();
    expect(receipt?.floor?.paise).toBe(100_000);
    expect(receipt?.floor?.text).toBe("₹1,000.00");
    expect(receipt?.floor_paise).toBe(100_000);
    expect(receipt?.median_low.paise).toBe(300_000);
    expect(receipt?.safe_to_spend?.amount_paise).toBe(200_000);
    expect(receipt?.goals?.[0].name).toBe("Trip");
    expect(receipt?.request).toEqual(request);
  });

  it("accepts older transcripts without safe_to_spend and with bare balance ages", () => {
    const legacy = {
      ...live,
      data_quality: {
        balance_ages: [{
          account: "Bank", as_of: null, age_days: null, amount_paise: 0, known: false,
        }],
      },
    };
    delete (legacy as Record<string, unknown>).safe_to_spend;
    const receipt = parseProjectionReceipt(legacy);
    expect(receipt).not.toBeNull();
    expect(receipt?.safe_to_spend).toBeUndefined();
    expect(receipt?.data_quality.balance_ages?.[0].amount_paise).toBe(0);
  });

  it("rejects malformed receipt fields", () => {
    const changes: Array<(receipt: Record<string, unknown>) => void> = [
      (receipt) => { receipt.verdict_reason = { bad: "value" }; },
      (receipt) => {
        (receipt.data_quality as Record<string, unknown>).simulation = { categories_flat: [42] };
      },
      (receipt) => { receipt.goals = [{ name: {} }]; },
      (receipt) => { receipt.hypothetical = { amount_paise: {} }; },
      (receipt) => { receipt.tradeoffs = [{ kind: "test", detail: {} }]; },
      (receipt) => {
        receipt.scenario = {
          changes: [{ kind: "stop_income", label: {} }], assumptions: [],
          runway_days: null, baseline: {},
        };
      },
      (receipt) => { receipt.safe_to_spend = { amount_paise: 150_000 }; },
    ];
    for (const change of changes) {
      const bad = structuredClone(live) as Record<string, unknown>;
      change(bad);
      expect(parseProjectionReceipt(bad)).toBeNull();
    }
  });

  it("strips the outside envelope from receipt display fields", () => {
    const receipt = parseProjectionReceipt({
      ...live,
      verdict: "yes",
      verdict_reason: "<outside>Plan holds</outside> because income arrived.",
      tradeoffs: [{
        kind: "cut_<outside>spend</outside>",
        detail: "Trim <outside>Food</outside> by 10%",
      }],
      goals: [{
        id: "g1", name: "<outside>Holiday</outside> fund", deadline: null,
        completes_on: null, slips_days: null, reached: false,
      }],
      sources: [{
        kind: "schedule", id: "sch_1", name: "<outside>Netflix</outside>",
        detail: "Monthly <outside>auto-debit</outside>",
      }],
    });
    expect(receipt?.verdict_reason).toBe("Plan holds because income arrived.");
    expect(receipt?.tradeoffs?.[0].kind).toBe("cut_spend");
    expect(receipt?.tradeoffs?.[0].detail).toBe("Trim Food by 10%");
    expect(receipt?.goals?.[0].name).toBe("Holiday fund");
    expect(receipt?.sources?.[0].name).toBe("Netflix");
    expect(receipt?.sources?.[0].detail).toBe("Monthly auto-debit");
  });
});

describe("FlowView receipt gating", () => {
  it("only treats a parsed receipt with a verdict as a Flow receipt", () => {
    expect(parseVerdictReceipt(live)).toBeNull();
    expect(parseVerdictReceipt({ ...live, verdict: "yes" })).not.toBeNull();
    expect(parseVerdictReceipt({ card: "projection", ...live, verdict: "no" })).not.toBeNull();
    expect(parseVerdictReceipt({ nonsense: 1 })).toBeNull();
  });
});
