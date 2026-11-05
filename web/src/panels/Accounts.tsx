import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { formatDate, formatPaise } from "../format";
import type { Account, AccountSummary } from "../types";
import { useApi } from "../useApi";
import {
  MoneyAsk,
  MoneyEmpty,
  MoneyError,
  MoneyPageHeader,
  MoneyPageSkeleton,
  MoneyPager,
  MoneyRefreshing,
  MoneySection,
  MoneySummary,
} from "./MoneyBits";
import { countLabel, pageSlice, safePage } from "./moneyView";

const ACCOUNT_PAGE_SIZE = 12;

const KIND_LABEL: Record<Account["kind"], string> = {
  bank: "Bank account",
  card: "Credit card",
  cash: "Cash",
  wallet: "Wallet",
  loan: "Loan",
  asset: "Holding",
  goal: "Goal",
  person: "Person",
};

const ACCOUNTS_DESCRIPTION =
  "Balances across your financial accounts, with the last stated balance shown beside the ledger balance.";

function cardPosition(paise: number): string {
  if (paise < 0) return `${formatPaise(-paise)} owed`;
  if (paise > 0) return `${formatPaise(paise)} credit`;
  return formatPaise(0);
}

function nextDueLabel(day: number | null): string {
  if (!day) return "not recorded";
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const dueOn = (year: number, month: number) => {
    const last = new Date(year, month + 1, 0).getDate();
    return new Date(year, month, Math.min(day, last));
  };
  let due = dueOn(now.getFullYear(), now.getMonth());
  if (due < today) due = dueOn(now.getFullYear(), now.getMonth() + 1);
  return due.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function statementNote(account: Account, asCard: boolean): string | null {
  if (account.statement_balance_paise === null) return "No statement balance recorded";
  const amount = asCard
    ? cardPosition(account.statement_balance_paise)
    : formatPaise(account.statement_balance_paise);
  if (account.statement_balance_as_of === null) {
    return `Statement ${amount} · date not recorded`;
  }
  const age =
    account.statement_age_days !== null && account.statement_age_days > 30
      ? ` · ${account.statement_age_days} days old`
      : "";
  return `Statement ${amount} · ${formatDate(account.statement_balance_as_of)}${age}`;
}

function cardDisagreement(account: Account): string | null {
  const stated = account.statement_balance_paise;
  if (stated === null) return null;
  const difference = account.balance_paise - stated;
  if (difference === 0) return null;
  const bothOwed = account.balance_paise < 0 && stated < 0;
  const mixed = account.balance_paise !== 0 && stated !== 0 && bothOwed === false;
  return [
    `Ledger and statement differ by ${formatPaise(Math.abs(difference))}.`,
    mixed
      ? "The ledger and statement point in different directions."
      : bothOwed
        ? "Both are amounts owed."
        : null,
    "They may cover different transactions.",
  ]
    .filter(Boolean)
    .join(" ");
}

function AccountRow({ account }: { account: Account }) {
  const meta = [account.institution, account.last4 ? `ending ${account.last4}` : null]
    .filter(Boolean)
    .join(" · ");
  const isCard = account.kind === "card";
  const isLoan = account.kind === "loan";
  const disagreement = isCard ? cardDisagreement(account) : null;
  const statement = statementNote(account, isCard);

  return (
    <article className="moneyv-row">
      <div className="moneyv-row-main">
        <strong>{account.name}</strong>
        <span className="moneyv-meta">
          {KIND_LABEL[account.kind]}
          {meta ? ` · ${meta}` : ""}
        </span>
        <span className="moneyv-meta">
          {account.balance_as_of
            ? `Ledger updated ${formatDate(account.balance_as_of)}`
            : "No ledger activity recorded yet"}
        </span>
      </div>
      <div className="moneyv-figures">
        {isCard ? (
          <>
            <strong>{cardPosition(account.balance_paise)}</strong>
            <span className="moneyv-meta">Ledger balance</span>
            <span className="moneyv-meta">{statement}</span>
            <span className="moneyv-meta">
              Available {formatPaise(account.available_credit_paise ?? null)} · next due{" "}
              {nextDueLabel(account.due_day)}
            </span>
            {disagreement && (
              <span className="moneyv-inline-warn">
                <AlertTriangle size={13} strokeWidth={1.8} aria-hidden="true" />
                {disagreement}
              </span>
            )}
          </>
        ) : isLoan ? (
          <>
            <strong>{formatPaise(account.loan_outstanding_paise ?? Math.max(-account.balance_paise, 0))}</strong>
            <span className="moneyv-meta">Outstanding principal</span>
            <span className="moneyv-meta">
              {account.rate_bps === null ? "Rate not recorded" : `${account.rate_bps / 100}% a year`} ·{" "}
              {account.tenure_months ? `${account.tenure_months} months` : "tenure not recorded"}
            </span>
          </>
        ) : (
          <>
            <strong className={account.balance_paise < 0 ? "negative" : undefined}>
              {formatPaise(account.balance_paise)}
            </strong>
            <span className="moneyv-meta">
              {account.kind === "asset" ? "Hand-updated balance" : "Ledger balance"}
            </span>
            {account.kind !== "asset" && account.statement_balance_paise !== null && statement && (
              <span className="moneyv-meta">{statement}</span>
            )}
          </>
        )}
      </div>
      <MoneyAsk prompt={`Tell me about ${account.name}.`} />
    </article>
  );
}

function AccountGroup({
  title,
  accounts,
  noun,
  emptyPrompt,
  emptyAction,
  emptyCopy,
}: {
  title: string;
  accounts: Account[];
  noun: string;
  emptyPrompt?: string;
  emptyAction?: string;
  emptyCopy?: string;
}) {
  const [page, setPage] = useState(0);
  const currentPage = safePage(page, accounts.length, ACCOUNT_PAGE_SIZE);
  return (
    <MoneySection title={title} note={accounts.length > 0 ? countLabel(accounts.length, noun) : undefined}>
      {accounts.length === 0 ? (
        emptyPrompt && emptyAction && emptyCopy ? (
          <MoneyEmpty prompt={emptyPrompt} action={emptyAction}>
            {emptyCopy}
          </MoneyEmpty>
        ) : null
      ) : (
        <>
          <div className="moneyv-list">
            {pageSlice(accounts, currentPage, ACCOUNT_PAGE_SIZE).map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </div>
          <MoneyPager
            page={currentPage}
            pageSize={ACCOUNT_PAGE_SIZE}
            total={accounts.length}
            noun={noun}
            onPage={setPage}
          />
        </>
      )}
    </MoneySection>
  );
}

export function AccountsPanel() {
  const { data, error, loading, reload } = useApi<AccountSummary>("/api/accounts");

  if (loading && !data) {
    return (
      <MoneyPageSkeleton title="Accounts" description={ACCOUNTS_DESCRIPTION} rows={5} />
    );
  }

  const header = (
    <MoneyPageHeader
      title="Accounts"
      description={ACCOUNTS_DESCRIPTION}
      aside={
        data ? (
          <>
            As of {formatDate(data.as_of)}
            {loading && <MoneyRefreshing />}
          </>
        ) : undefined
      }
    />
  );

  if (!data) {
    return (
      <section className="panel">
        {header}
        <MoneyError
          title="Couldn’t load accounts"
          message={error ?? "The accounts request did not return data."}
          onRetry={reload}
        />
      </section>
    );
  }

  const cards = data.accounts.filter((account) => account.kind === "card");
  const loans = data.accounts.filter((account) => account.kind === "loan");
  const banking = data.accounts.filter((account) =>
    ["bank", "cash", "wallet"].includes(account.kind),
  );
  const holdings = data.accounts.filter((account) => account.kind === "asset");
  const liquidAccounts = banking.filter((account) => account.liquid && account.active);
  const assets = holdings.reduce((total, account) => total + account.balance_paise, 0);
  const visibleAccountCount = cards.length + loans.length + banking.length + holdings.length;

  return (
    <section className="panel">
      {header}
      {error && (
        <MoneyError
          title="Couldn’t refresh accounts"
          message={error}
          onRetry={reload}
        />
      )}

      {visibleAccountCount === 0 ? (
        <MoneyEmpty prompt="Add a bank account: " action="Ask to add one">
          No bank, cash, card, loan, or holding accounts yet. Start with the account you use every
          day.
        </MoneyEmpty>
      ) : (
        <>
          <MoneySummary
            label="Account summary"
            cells={[
              {
                label: "Cash on hand",
                value: formatPaise(data.liquid_total_paise),
                note: liquidAccounts.length
                  ? `Sum of ${countLabel(liquidAccounts.length, "liquid balance")}`
                  : "No liquid accounts",
              },
              {
                label: "Card balance",
                value: formatPaise(data.card_owed_paise),
                note: cards.length ? `Owed on ${countLabel(cards.length, "card")}` : "No cards",
              },
              {
                label: "Loan outstanding",
                value: formatPaise(data.loan_outstanding_paise),
                note: loans.length ? `Across ${countLabel(loans.length, "loan")}` : "No loans",
              },
              {
                label: "Holdings",
                value: formatPaise(assets),
                note: holdings.length
                  ? countLabel(holdings.length, "hand-updated asset")
                  : "No holdings",
              },
            ]}
          />
          <p className="moneyv-basis">
            Cash on hand adds the ledger balances of your liquid bank, cash, and wallet accounts. It
            does not deduct card dues or upcoming bills, so it is not money free to spend.
          </p>

          <AccountGroup
            title="Bank and cash"
            accounts={banking}
            noun="account"
            emptyPrompt="Add a bank account: "
            emptyAction="Ask to add one"
            emptyCopy="No bank, cash, or wallet accounts yet."
          />

          {cards.length > 0 && <AccountGroup title="Credit cards" accounts={cards} noun="card" />}
          {loans.length > 0 && <AccountGroup title="Loans" accounts={loans} noun="loan" />}

          <AccountGroup
            title="Holdings"
            accounts={holdings}
            noun="holding"
            emptyPrompt="Add an asset: "
            emptyAction="Ask to add one"
            emptyCopy="No hand-updated holdings yet."
          />
        </>
      )}
    </section>
  );
}
