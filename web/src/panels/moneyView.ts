import type { ReactNode } from "react";

export type SummaryCell = {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "positive" | "negative" | "warning";
};

/** Explicit client-side paging: pages of a known, finite length and never a trailing page. */
export function pageSlice<T>(items: T[], page: number, pageSize: number): T[] {
  return items.slice(page * pageSize, page * pageSize + pageSize);
}

export function safePage(page: number, total: number, pageSize: number): number {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return Math.min(Math.max(page, 0), pages - 1);
}

export function countLabel(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
