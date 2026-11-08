import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { isoDate } from "./format";
import type { Movement, MovementList } from "./types";

const PAGE = 200;
// ponytail: 10 pages (2,000 rows) per window; the result says when it stopped short.
const MAX_PAGES = 10;

export type Spending = {
  start: string;
  end: string;
  rows: Movement[];
  /** True when the window had more rows than were read; totals then cover only the rows read. */
  truncated: boolean;
};

/** Every expense movement in [start, end], newest first, read through the ledger's cursor. */
export async function readExpenses(start: string, end: string): Promise<Spending> {
  const rows: Movement[] = [];
  let cursor: string | null = null;
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const params = new URLSearchParams({ start, end, kind: "expense", limit: String(PAGE) });
    if (cursor) params.set("cursor", cursor);
    const list: MovementList = await api<MovementList>(`/api/movements?${params.toString()}`);
    rows.push(...list.items.filter((movement) => !movement.reversed));
    cursor = list.next_cursor;
    if (!cursor) return { start, end, rows, truncated: false };
  }
  return { start, end, rows, truncated: true };
}

export function daysAgo(days: number, from = new Date()): string {
  const date = new Date(from);
  date.setDate(date.getDate() - days);
  return isoDate(date);
}

/** Sum of amounts by key, largest first. */
export function totalsBy(rows: Movement[], key: (movement: Movement) => string) {
  const totals = new Map<string, { key: string; paise: number; count: number }>();
  for (const movement of rows) {
    const name = key(movement);
    const entry = totals.get(name) ?? { key: name, paise: 0, count: 0 };
    entry.paise += movement.amount_paise;
    entry.count += 1;
    totals.set(name, entry);
  }
  return [...totals.values()].sort((a, b) => b.paise - a.paise);
}

export function useExpenses(start: string, end: string) {
  const [state, setState] = useState<{
    data: Spending | null;
    error: string | null;
    loading: boolean;
  }>({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setState((previous) => ({ ...previous, loading: true }));
    readExpenses(start, end)
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch(
        (caught: unknown) =>
          alive && setState({ data: null, error: errorMessage(caught), loading: false }),
      );
    return () => {
      alive = false;
    };
  }, [start, end]);
  return state;
}
