import { describe, expect, it } from "vitest";
import { formatRupees } from "./format";

describe("formatRupees", () => {
  it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 100.5])(
    "renders %s as missing data, never as a rupee amount",
    (paise) => {
      expect(formatRupees(paise)).toBe("no data");
    },
  );

  it("keeps whole rupees short and shows paise when they matter", () => {
    expect(formatRupees(45_000)).toBe("₹450");
    expect(formatRupees(45_050)).toBe("₹450.50");
  });
});
