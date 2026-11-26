import { describe, expect, it } from "vitest";
import { hasToolWidget } from "./results";

describe("native tool result widgets", () => {
  it("accepts query rows only when their shape is usable", () => {
    expect(hasToolWidget("run_query", { columns: ["amount_paise"], rows: [[123]], truncated: false })).toBe(true);
    expect(hasToolWidget("run_query", { columns: [], rows: [], truncated: false })).toBe(false);
    expect(hasToolWidget("run_query", { columns: ["x"], rows: [[{}]], truncated: false })).toBe(false);
  });

  it("does not treat arbitrary or sandbox output as a write card", () => {
    const result = { card: { card: "movement", undo_id: "group" } };
    expect(hasToolWidget("record_movement", result)).toBe(true);
    expect(hasToolWidget("run_python", result)).toBe(false);
    expect(hasToolWidget("record_movement", { card: "movement" })).toBe(false);
  });

  it("only accepts complete, safe integer scenario grids", () => {
    const scenario = { label: "Loan", tenure_months: 12, prepayment_paise: 0, monthly_paise: 100, interest_paise: 200, total_paise: 1200 };
    expect(hasToolWidget("run_python", { result_json: { scenarios: [scenario] } })).toBe(true);
    expect(hasToolWidget("run_python", { result_json: { scenarios: [{ ...scenario, total_paise: 1.5 }] } })).toBe(false);
  });
});
