import { askAgent } from "../ask";
import { formatDate, formatPaise } from "../format";
import { goTo } from "../nav";
import { projectionReadiness } from "../projectionData";
import type { HomePayload } from "./AskHome";

export const BALANCE_PROMPT = "My savings balance is ₹";

export type Step = {
  key: string;
  title: string;
  ready: boolean;
  /** The real figure once it exists; nothing stands in for it before. */
  figure?: string;
  detail: string;
  action?: { label: string; primary?: boolean; run: () => void };
  preview: "cash" | "curve" | "story";
};

/** The three things the agent can show once money is in, each read from `/api/home`. */
export function firstRunSteps(data: HomePayload): Step[] {
  const cashKnown = data.cash_quality.status === "known";
  const readiness = projectionReadiness(data.projection);
  const empty = data.ledger.movements === 0;
  const names = readiness.unknownAccounts;
  const accounts = () => goTo("money", "accounts");
  // Some balances known is enough to show their sum, as long as the missing ones are named.
  const quality = data.cash_quality;
  const someKnown = quality.known_count > 0;
  const missing = quality.unknown_accounts;
  return [
    {
      key: "cash",
      title: "Cash on hand",
      ready: someKnown,
      figure: someKnown ? formatPaise(cashKnown ? data.spendable_paise : quality.known_paise) : undefined,
      detail: cashKnown
        ? `Liquid balances as of ${formatDate(data.as_of)}.`
        : someKnown
          ? `Known balances only; ${missing.slice(0, 2).join(" and ")}${missing.length > 2 ? ` and ${missing.length - 2} more` : ""} not counted.`
          : "Your bank, cash, and wallet balances, added up.",
      action: someKnown
        ? { label: "Open", run: accounts }
        : empty
          ? undefined
          : { label: "Add a balance", primary: true, run: () => askAgent(BALANCE_PROMPT) },
      preview: "cash",
    },
    {
      key: "afford",
      title: "“Can I afford it?”",
      ready: cashKnown && readiness.ready,
      detail:
        cashKnown && readiness.ready
          ? "Ask about any purchase before you buy it."
          : names.length
            ? `Needs a balance for ${names.slice(0, 2).join(" and ")}${names.length > 2 ? ` and ${names.length - 2} more` : ""}.`
            : "Needs a known balance for every account you spend from.",
      action:
        cashKnown && readiness.ready
          ? { label: "Try it", run: () => askAgent("Can I afford ") }
          : empty
            ? undefined
            : { label: "Fix", primary: true, run: accounts },
      preview: "curve",
    },
    {
      key: "recaps",
      title: "Recaps",
      ready: data.ledger.spending > 0,
      detail:
        data.ledger.spending > 0
          ? "Your stories are ready to play."
          : "Daily, weekly, and monthly stories, once there is spending to tell.",
      action: data.ledger.spending > 0 ? { label: "Watch", run: () => goTo("recaps") } : undefined,
      preview: "story",
    },
  ];
}
