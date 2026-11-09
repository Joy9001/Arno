import { formatPaise } from "../format";
import { displayText } from "./display-text";
import { TOOL_COPY, toolLabel } from "./tool-copy";

export type StepRow = { label: string; value: string; warn?: boolean };

export type StepSummary = {
  title: string;
  id: string;
  rows: StepRow[];
  tone: "done" | "running" | "warn";
  taskId?: string;
};

type Fields = Record<string, unknown>;

/** The same receipt `delegate-task-card.tsx` reads; both take the background task id from it. */
const TASK_RECEIPT = /^Task ([0-9a-f]{32}) is running in the background\./;

const NOUNS: Record<string, string> = {
  account: "account",
  balance: "balance",
  schedule: "schedule",
  goal: "goal",
  category: "category",
  payee: "payee",
  settings: "settings",
  scheduled_question: "scheduled question",
  baseline: "baseline",
};

function fields(value: unknown): Fields {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Fields)
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function string(value: unknown): string {
  return typeof value === "string" ? displayText(value) : "";
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A money value from the tools (`{ paise, text }`) or a bare paise count. */
function money(value: unknown): string | null {
  if (value !== null && typeof value === "object") {
    const row = fields(value);
    const paise = number(row.paise);
    if (paise !== null) return formatPaise(paise);
    const label = string(row.text);
    return label || null;
  }
  const paise = number(value);
  return paise === null ? null : formatPaise(paise);
}

/** Rupees as the tools accept them in setup writes. */
function rupees(value: unknown): string | null {
  const amount = number(value);
  return amount === null ? null : formatPaise(Math.round(amount * 100));
}

function shortDay(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return displayText(value);
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function span(from: unknown, to: unknown): string {
  const start = shortDay(from);
  const end = shortDay(to);
  if (start && end) return `${start} – ${end}`;
  return start || end;
}

function share(value: unknown): string {
  const amount = number(value);
  return amount === null ? "" : `${Math.round(amount * 100)}%`;
}

function titleCase(value: string): string {
  const clean = value.replaceAll("_", " ").trim();
  return clean ? clean[0]!.toUpperCase() + clean.slice(1) : "";
}

function rowsOf(entries: Array<StepRow | null>): StepRow[] {
  return entries.filter((row): row is StepRow => row !== null);
}

function contextStep(result: Fields): StepSummary {
  const rows = list(result.accounts)
    .slice(0, 3)
    .flatMap((raw) => {
      const account = fields(raw);
      const label = string(account.name);
      if (!label) return [];
      const balance = account.known === false ? null : money(account.balance);
      return [{ label, value: balance ?? "Not known" }];
    });
  return { title: "Read your money", id: "get_financial_context", rows, tone: "done" };
}

function spendingStep(result: Fields): StepSummary {
  const period = fields(result.period);
  const total = money(result.total);
  const top = fields(list(result.groups)[0]);
  const rows = rowsOf([
    span(period.from, period.to) ? { label: "Period", value: span(period.from, period.to) } : null,
    total ? { label: "Total", value: total } : null,
    string(top.key)
      ? { label: "Top group", value: `${string(top.key)} · ${money(top.amount) ?? "—"}` }
      : null,
  ]);
  return { title: "Totalled spending", id: "summarize_spending", rows, tone: "done" };
}

function movementsStep(result: Fields): StepSummary {
  const coverage = fields(result.coverage);
  const count = number(result.total_count);
  const period = span(coverage.from, coverage.to);
  const rows = rowsOf([
    count === null ? null : { label: "Count", value: String(count) },
    period ? { label: "Period", value: period } : null,
  ]);
  return { title: "Listed movements", id: "query_movements", rows, tone: "done" };
}

function projectionStep(result: Fields): StepSummary {
  const horizon = number(result.horizon_days);
  const low = fields(result.median_low);
  const baseline = fields(result.baseline);
  const floor = number(result.floor_probability);
  const threshold = number(result.floor_threshold);
  const was = number(baseline.floor_probability);
  const lowest = [money(low.amount), shortDay(low.date)].filter(Boolean).join(" · ");
  const runs = number(result.runs);
  const rows = rowsOf([
    floor === null
      ? null
      : {
          label: "Floor holds",
          value: `${share(floor)}${was === null ? "" : ` · was ${share(was)}`}`,
          warn: threshold !== null && floor < threshold,
        },
    lowest ? { label: "Lowest median", value: lowest } : null,
    runs === null ? null : { label: "Futures run", value: runs.toLocaleString("en-IN") },
  ]);
  return {
    title: horizon === null ? "Projected your cash" : `Projected ${horizon} days`,
    id: "project",
    rows,
    tone: "done",
  };
}

function recapStep(result: Fields): StepSummary {
  const period = fields(result.period);
  const story = list(result.stories)
    .map(fields)
    .find((item) => item.key === "total");
  const spent = story ? money(story.figure) : null;
  const rows = rowsOf([
    string(period.title) ? { label: "Period", value: string(period.title) } : null,
    spent ? { label: "Spent", value: spent } : null,
  ]);
  return { title: "Read the recap", id: "recap", rows, tone: "done" };
}

function setupTitle(action: string, entity: string): string {
  const noun = NOUNS[entity] ?? titleCase(entity);
  if (entity === "settings") return action === "add" ? "Saved your settings" : "Updated your settings";
  const verb = action === "add" ? "Added" : action === "archive" ? "Archived" : "Updated";
  const article = /^[aeiou]/i.test(noun) ? "an" : "a";
  return `${verb} ${article} ${noun}`;
}

function setupRows(entity: string, data: Fields): StepRow[] {
  const name = string(data.name);
  const kind = string(data.kind);
  if (entity === "account") {
    const balance = rupees(data.opening_balance_rupees);
    return rowsOf([
      name ? { label: "Account", value: name } : null,
      kind ? { label: "Kind", value: titleCase(kind) } : null,
      { label: "Balance", value: balance ?? "Not known yet", warn: balance === null },
    ]).slice(0, 3);
  }
  if (entity === "balance") {
    const balance = rupees(data.balance_rupees);
    const asOf = shortDay(data.as_of);
    return rowsOf([
      string(data.account) ? { label: "Account", value: string(data.account) } : null,
      balance ? { label: "Balance", value: balance } : null,
      asOf ? { label: "As of", value: asOf } : null,
    ]);
  }
  if (entity === "settings") {
    const floor = rupees(data.floor_rupees);
    return floor ? [{ label: "Floor", value: floor }] : [];
  }
  if (entity === "schedule") {
    const amount = rupees(data.amount_rupees);
    return rowsOf([
      name ? { label: "Schedule", value: name } : null,
      amount ? { label: "Amount", value: amount } : null,
    ]);
  }
  if (entity === "goal") {
    const target = rupees(data.target_rupees);
    return rowsOf([
      name ? { label: "Goal", value: name } : null,
      target ? { label: "Target", value: target } : null,
      shortDay(data.deadline) ? { label: "By", value: shortDay(data.deadline) } : null,
    ]);
  }
  return rowsOf([name ? { label: "Name", value: name } : null]);
}

/**
 * What one tool call did, in the agent's own past tense, and what it found. Rows are results,
 * never the arguments the model sent. Every string is display text; a malformed result yields
 * the tool's label instead of throwing.
 */
export function stepSummary(toolName: string, args: unknown, result: unknown): StepSummary {
  const input = fields(args);
  const output = fields(result);
  const failed =
    result !== undefined &&
    (output.status === "error" || output.status === "failed" || output.ok === false);
  const tone: StepSummary["tone"] =
    result === undefined ? "running" : failed ? "warn" : "done";
  try {
    switch (toolName) {
      case "get_financial_context":
        return { ...contextStep(output), tone };
      case "summarize_spending":
        return { ...spendingStep(output), tone };
      case "query_movements":
        return { ...movementsStep(output), tone };
      case "project":
        return { ...projectionStep(output), tone };
      case "recap":
        return { ...recapStep(output), tone };
      case "update_setup": {
        const entity = string(input.entity);
        const action = string(input.action) || "add";
        return {
          title: entity ? setupTitle(action, entity) : titleCase(action) + " setup",
          id: "update_setup",
          rows: setupRows(entity, fields(input.data)),
          tone,
        };
      }
      case "delegate_task": {
        const agent = string(input.agent);
        const receipt = typeof result === "string" ? TASK_RECEIPT.exec(result) : null;
        return {
          title:
            agent === "parser"
              ? "Reading your statement"
              : agent
                ? `Asked the ${agent} agent`
                : "Asked a background agent",
          id: agent ? `${agent} · background` : "background",
          rows: [],
          tone,
          ...(receipt ? { taskId: receipt[1]! } : {}),
        };
      }
      case "run_code": {
        const code = string(input.code);
        const comment = /^[ \t]*#[ \t]+(.+)$/m.exec(code)?.[1]?.trim();
        const calls =
          number(output.tool_calls) ??
          (Array.isArray(output.tool_calls) ? output.tool_calls.length : 0);
        const rows: StepRow[] =
          calls > 0 ? [{ label: "Tools called", value: String(calls) }] : [];
        return {
          title: comment ? comment.slice(0, 70) : "Worked through the numbers",
          id: "run_code",
          rows,
          tone,
        };
      }
      default:
        return {
          title: TOOL_COPY[toolName]?.done ?? toolLabel(toolName),
          id: toolName,
          rows: [],
          tone,
        };
    }
  } catch {
    return {
      title: TOOL_COPY[toolName]?.done ?? toolLabel(toolName),
      id: toolName,
      rows: [],
      tone,
    };
  }
}
