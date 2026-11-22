import { expect, it } from "vitest";
import { formatMoneyValue, formatPaise } from "./format";

it.each([NaN, Infinity, -Infinity, 99.5, Number.MAX_SAFE_INTEGER + 1])(
  "treats invalid paise %s as missing",
  (paise) => expect(formatPaise(paise)).toBe("—"),
);

it("formats signed integer paise without losing the fractional rupee", () => {
  expect(formatPaise(0)).toBe("₹0");
  expect(formatPaise(-150)).toBe("-₹1.50");
});

it("reads tool money objects and bare paise with one helper", () => {
  expect(formatMoneyValue({ paise: 45_000, text: "₹450.00" })).toBe("₹450");
  expect(formatMoneyValue({ text: "<outside>₹450</outside>" })).toBe("₹450");
  expect(formatMoneyValue(45_050)).toBe("₹450.50");
  expect(formatMoneyValue(null)).toBeNull();
  expect(formatMoneyValue({})).toBeNull();
});
