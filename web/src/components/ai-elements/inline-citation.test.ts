import { describe, expect, it } from "vitest";
import { safeSourceUrl } from "./inline-citation";

describe("citation URLs", () => {
  it("accepts only absolute web links", () => {
    expect(safeSourceUrl("https://example.com/article")).toBe(true);
    expect(safeSourceUrl("http://example.com")).toBe(true);
    expect(safeSourceUrl("javascript:alert(1)")).toBe(false);
    expect(safeSourceUrl("/api/movements/1")).toBe(false);
  });
});
