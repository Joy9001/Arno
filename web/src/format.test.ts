import { expect, it } from "vitest";
import { formatPaise } from "./format";

it.each([NaN, Infinity, -Infinity, 99.5, Number.MAX_SAFE_INTEGER + 1])(
  "treats invalid paise %s as missing",
  (paise) => expect(formatPaise(paise)).toBe("—"),
);

it("formats signed integer paise without losing the fractional rupee", () => {
  expect(formatPaise(0)).toBe("₹0");
  expect(formatPaise(-150)).toBe("-₹1.50");
});
