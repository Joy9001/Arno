import { useAuiState } from "@assistant-ui/react";
import { HoverCard } from "radix-ui";
import { useId, useState } from "react";
import { formatPaise } from "../../format";

export type LedgerCitation = {
  id: string;
  label: string;
  date: string;
  amount: number;
  url: string;
};

export function ledgerCitations(parts: readonly unknown[]): LedgerCitation[] {
  const found: LedgerCitation[] = [];
  for (const part of parts) {
    if (
      !part ||
      typeof part !== "object" ||
      !("type" in part) ||
      part.type !== "tool-call" ||
      !("toolName" in part) ||
      part.toolName !== "run_query" ||
      !("result" in part)
    )
      continue;
    const result = part.result;
    if (
      !result ||
      typeof result !== "object" ||
      !("columns" in result) ||
      !("rows" in result) ||
      !Array.isArray(result.columns) ||
      !Array.isArray(result.rows)
    )
      continue;
    const columns = result.columns;
    const id = columns.indexOf("movement_id");
    const name = columns.indexOf("payee_name");
    const description = columns.indexOf("description");
    const amount = columns.indexOf("amount_paise");
    const date = columns.indexOf("date");
    if (id < 0 || (name < 0 && description < 0) || amount < 0 || date < 0)
      continue;
    for (const row of result.rows) {
      if (!Array.isArray(row)) continue;
      const label = row[name] || row[description];
      if (
        typeof row[id] !== "string" ||
        !/^mov_[\w-]+$/.test(row[id]) ||
        typeof label !== "string" ||
        typeof row[amount] !== "number" ||
        !Number.isSafeInteger(row[amount]) ||
        typeof row[date] !== "string"
      )
        continue;
      found.push({
        id: row[id],
        label,
        date: row[date],
        amount: row[amount],
        url: `https://arno.invalid/movement/${row[id]}`,
      });
    }
  }
  return found.filter(
    (item, index) => found.findIndex((other) => other.id === item.id) === index,
  );
}

export function useLedgerCitations(): LedgerCitation[] {
  return ledgerCitations(useAuiState((state) => state.message.parts));
}

export function LedgerCitationPreview({ source }: { source: LedgerCitation }) {
  const [open, setOpen] = useState(false);
  const descriptionId = useId();
  return (
    <HoverCard.Root open={open} onOpenChange={setOpen}>
      <HoverCard.Trigger asChild>
        <button
          type="button"
          aria-expanded={open}
          aria-describedby={descriptionId}
          onClick={() => setOpen(!open)}
          onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}
          className="rounded-full border border-white/20 px-2 text-[var(--lime)] underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--lime)]"
        >
          {source.label} · {formatPaise(source.amount)}
        </button>
      </HoverCard.Trigger>
      <span id={descriptionId} className="sr-only">{source.date} · {source.id}</span>
      <HoverCard.Portal>
        <HoverCard.Content
          sideOffset={6}
          className="z-50 rounded-xl border border-white/10 bg-[var(--surface-2)] p-3 text-sm text-[var(--text)] shadow-xl"
        >
          <strong>{source.label}</strong>
          <p>
            {formatPaise(source.amount)} · {source.date}
          </p>
          <p className="text-xs text-[var(--sub)]">{source.id}</p>
        </HoverCard.Content>
      </HoverCard.Portal>
    </HoverCard.Root>
  );
}
