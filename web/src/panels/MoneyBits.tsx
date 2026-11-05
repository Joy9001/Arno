import type { ReactNode } from "react";
import { AlertCircle, ChevronLeft, ChevronRight, MessageCircle } from "lucide-react";
import { Button, Skeleton } from "@radix-ui/themes";
import { useReducedMotion } from "motion/react";
import { askAgent } from "../ask";
import { Busy } from "../Loading";
import type { SummaryCell } from "./moneyView";
import "./MoneyViews.css";

export function MoneyPageHeader({
  title,
  description,
  aside,
}: {
  title: string;
  description: string;
  aside?: ReactNode;
}) {
  return (
    <header className="moneyv-head">
      <div className="moneyv-head-copy">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {aside && <div className="moneyv-head-aside">{aside}</div>}
    </header>
  );
}

/** One strip per view: the values that frame the page, separated by meaning, not cards. */
export function MoneySummary({ cells, label }: { cells: SummaryCell[]; label: string }) {
  return (
    <dl className="moneyv-summary" aria-label={label}>
      {cells.map((cell) => (
        <div className="moneyv-summary-cell" key={cell.label}>
          <dt>{cell.label}</dt>
          <dd className={cell.tone ? `moneyv-value moneyv-${cell.tone}` : "moneyv-value"}>
            {cell.value}
          </dd>
          {cell.note && <dd className="moneyv-summary-note">{cell.note}</dd>}
        </div>
      ))}
    </dl>
  );
}

export function MoneySection({
  title,
  note,
  children,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="moneyv-section" aria-label={title}>
      <div className="moneyv-section-head">
        <h3>{title}</h3>
        {note && <span className="moneyv-count">{note}</span>}
      </div>
      {children}
    </section>
  );
}

export function MoneyAsk({ prompt, label = "Ask" }: { prompt: string; label?: string }) {
  const context = prompt.trim().replace(/[.:]\s*$/, "");
  return (
    <button
      type="button"
      className="ask-link moneyv-ask"
      aria-label={`${label}: ${context}`}
      onClick={() => askAgent(prompt)}
    >
      <MessageCircle size={13} strokeWidth={1.8} aria-hidden="true" />
      {label}
    </button>
  );
}

/** A refresh keeps the current data; the spinner plus status text says so to everyone. */
export function MoneyRefreshing() {
  const reducedMotion = useReducedMotion();
  return (
    <span className="moneyv-refreshing" role="status">
      {reducedMotion ? (
        <span className="moneyv-refresh-label">Refreshing…</span>
      ) : (
        <>
          <Busy />
          <span className="sr-only">Refreshing</span>
        </>
      )}
    </span>
  );
}

export function MoneyEmpty({ children, prompt, action }: { children: ReactNode; prompt: string; action: string }) {
  return (
    <div className="moneyv-empty">
      <p>{children}</p>
      <MoneyAsk prompt={prompt} label={action} />
    </div>
  );
}

export function MoneyError({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="moneyv-error-block" role="alert">
      <AlertCircle size={17} strokeWidth={1.8} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      <Button variant="soft" color="gray" size="2" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

export function MoneyPager({
  page,
  pageSize,
  total,
  noun,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  noun: string;
  onPage: (page: number) => void;
}) {
  const pages = Math.ceil(total / pageSize);
  if (pages <= 1) return null;
  const start = page * pageSize + 1;
  const end = Math.min(total, (page + 1) * pageSize);
  return (
    <nav className="moneyv-pager" aria-label={`${noun} pages`}>
      <span className="moneyv-pager-note" aria-live="polite">
        Showing {start}–{end} of {total}
      </span>
      <div className="moneyv-pager-actions">
        <Button variant="soft" color="gray" size="2" disabled={page <= 0} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />
          Previous
        </Button>
        <span className="moneyv-pager-count">
          {page + 1} / {pages}
        </span>
        <Button
          variant="soft"
          color="gray"
          size="2"
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
        >
          Next
          <ChevronRight size={14} strokeWidth={1.8} aria-hidden="true" />
        </Button>
      </div>
    </nav>
  );
}

/** The first load is shaped like the page it replaces, so the layout does not jump. */
export function MoneyPageSkeleton({
  title,
  description,
  summary = 4,
  rows = 4,
  chart = false,
}: {
  title: string;
  description: string;
  summary?: number;
  rows?: number;
  chart?: boolean;
}) {
  return (
    <section className="panel" aria-busy="true" aria-label={`Loading ${title.toLowerCase()}`}>
      <MoneyPageHeader title={title} description={description} />
      <div className="moneyv-summary moneyv-skeleton-summary">
        {Array.from({ length: summary }, (_, index) => (
          <div className="moneyv-summary-cell" key={index}>
            <Skeleton className="moneyv-sk moneyv-sk-label" />
            <Skeleton className="moneyv-sk moneyv-sk-value" />
            <Skeleton className="moneyv-sk moneyv-sk-note" />
          </div>
        ))}
      </div>
      {chart && (
        <div className="moneyv-section">
          <div className="moneyv-section-head">
            <Skeleton className="moneyv-sk moneyv-sk-heading" />
          </div>
          <Skeleton className="moneyv-sk moneyv-sk-chart" />
        </div>
      )}
      {rows > 0 && (
        <div className="moneyv-section">
          <div className="moneyv-section-head">
            <Skeleton className="moneyv-sk moneyv-sk-heading" />
          </div>
          <div className="moneyv-list">
            {Array.from({ length: rows }, (_, index) => (
              <div className="moneyv-row moneyv-skeleton-row" key={index}>
                <div className="moneyv-row-main">
                  <Skeleton className="moneyv-sk moneyv-sk-title" />
                  <Skeleton className="moneyv-sk moneyv-sk-meta" />
                </div>
                <Skeleton className="moneyv-sk moneyv-sk-amount" />
              </div>
            ))}
          </div>
        </div>
      )}
      <span className="sr-only">Loading…</span>
    </section>
  );
}
