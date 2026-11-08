import type { CSSProperties } from "react";

/** Recharts tooltips default to a white box; every chart shares this dark one instead. */
export const TOOLTIP_PROPS = {
  separator: ": ",
  cursor: { stroke: "rgba(246, 247, 240, 0.28)", strokeWidth: 1 },
  contentStyle: {
    padding: "10px 12px",
    border: "1px solid rgba(255, 255, 255, 0.1)",
    borderRadius: 14,
    background: "rgba(23, 24, 20, 0.96)",
    boxShadow: "0 12px 32px rgba(0, 0, 0, 0.45)",
  } satisfies CSSProperties,
  labelStyle: {
    marginBottom: 4,
    color: "#a3a79a",
    fontSize: 12,
    fontWeight: 600,
  } satisfies CSSProperties,
  itemStyle: {
    padding: "1px 0",
    fontSize: 13,
    fontWeight: 700,
    fontVariantNumeric: "tabular-nums",
  } satisfies CSSProperties,
};
