import { describe, expect, it } from "vitest";
import { ledgerCitations } from "./ledger-citation";

describe("ledger citations", () => {
  it("uses only complete rows returned by the same run_query", () => {
    const part = {
      type: "tool-call",
      toolName: "run_query",
      result: {
        columns: ["movement_id", "payee_name", "amount_paise", "date"],
        rows: [
          ["mov_123", "Shop", 50000, "2026-01-01"],
          ["mov_bad", "Shop", null, "2026-01-01"],
        ],
      },
    };
    expect(ledgerCitations([part])).toEqual([
      {
        id: "mov_123",
        label: "Shop",
        amount: 50000,
        date: "2026-01-01",
        url: "https://arno.invalid/movement/mov_123",
      },
    ]);
    expect(ledgerCitations([{ ...part, toolName: "other" }])).toEqual([]);
  });
});
