import { useEffect, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, CreditCard, FileSearch, Landmark, X } from "lucide-react";
import { Orb } from "../Orb";
import { api, errorMessage } from "../api";
import { formatDate, formatMoment, formatPaise } from "../format";
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
  "Ledger balances alongside the statements behind them.";

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

type AccountDetail = Account & {
  created_at?: string | null;
  emi_schedule_id?: string | null;
};

function AccountDetailDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<AccountDetail>(`/api/accounts/${account.id}`)
      .then((value) => {
        if (alive) setDetail(value);
      })
      .catch((caught: unknown) => {
        if (alive) setError(errorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [account.id]);

  const row: AccountDetail = detail ?? account;
  const isCard = row.kind === "card";
  const isLoan = row.kind === "loan";
  const statement = row.statement_balance_paise;

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="movement-dialog" aria-describedby={undefined}>
          <div className="queue-head">
            <Dialog.Title>{account.name}</Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                className="ghost small icon-only"
                aria-label="Close details"
                title="Close details"
              >
                <X size={15} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!detail && !error && <p className="muted fine">Opening the account row…</p>}
          <dl className="detail-grid">
            <dt>Type</dt>
            <dd>{KIND_LABEL[row.kind]}</dd>
            <dt>Institution</dt>
            <dd>{row.institution ?? "not recorded"}</dd>
            {row.last4 && (
              <>
                <dt>Number</dt>
                <dd>ending {row.last4}</dd>
              </>
            )}
            <dt>Status</dt>
            <dd>
              {row.active ? "Active" : "Archived"}
              {row.liquid ? " · liquid" : ""}
            </dd>
            <dt>{isLoan ? "Outstanding principal" : "Ledger balance"}</dt>
            <dd>
              {isCard
                ? cardPosition(row.balance_paise)
                : isLoan
                  ? formatPaise(row.loan_outstanding_paise ?? Math.max(-row.balance_paise, 0))
                  : formatPaise(row.balance_paise)}
            </dd>
            <dt>Balance updated</dt>
            <dd>{row.balance_as_of ? formatDate(row.balance_as_of) : "No ledger activity recorded"}</dd>
            <dt>Statement balance</dt>
            <dd>
              {statement === null
                ? "Not recorded"
                : isCard
                  ? cardPosition(statement)
                  : formatPaise(statement)}
            </dd>
            <dt>Statement as of</dt>
            <dd>
              {row.statement_balance_as_of
                ? `${formatDate(row.statement_balance_as_of)}${
                    row.statement_age_days !== null ? ` · ${row.statement_age_days} days old` : ""
                  }`
                : "Not recorded"}
            </dd>
            {isCard && (
              <>
                <dt>Credit limit</dt>
                <dd>{formatPaise(row.credit_limit_paise)}</dd>
                <dt>Available credit</dt>
                <dd>
                  {row.available_credit_paise == null
                    ? "Unknown"
                    : formatPaise(row.available_credit_paise)}
                </dd>
                <dt>Statement day</dt>
                <dd>{row.statement_day ? `Day ${row.statement_day}` : "Not recorded"}</dd>
                <dt>Next due</dt>
                <dd>
                  {row.due_day ? `${nextDueLabel(row.due_day)} · day ${row.due_day}` : "Not recorded"}
                </dd>
              </>
            )}
            {isLoan && (
              <>
                <dt>Interest rate</dt>
                <dd>{row.rate_bps === null ? "Not recorded" : `${row.rate_bps / 100}% a year`}</dd>
                <dt>Tenure</dt>
                <dd>{row.tenure_months ? `${row.tenure_months} months` : "Not recorded"}</dd>
              </>
            )}
            {detail?.created_at && (
              <>
                <dt>Opened</dt>
                <dd>{formatMoment(detail.created_at)}</dd>
              </>
            )}
            <dt>Account ID</dt>
            <dd>{row.id}</dd>
          </dl>
          <div className="button-row">
            <MoneyAsk prompt={`Tell me about ${account.name}.`} />
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function AccountRow({ account }: { account: Account }) {
  const [open, setOpen] = useState(false);
  const meta = [account.institution, account.last4 ? `ending ${account.last4}` : null]
    .filter(Boolean)
    .join(" · ");
  const isCard = account.kind === "card";
  const isLoan = account.kind === "loan";
  const disagreement = isCard ? cardDisagreement(account) : null;
  const statement = statementNote(account, isCard);

  return (
    <article className={`account-tile${isCard ? " is-card" : ""}`}>
      <div className="account-identity">
        <span className="account-symbol" aria-hidden="true">
          {isCard ? <CreditCard size={22} /> : <Landmark size={20} />}
        </span>
        <div className="moneyv-row-main">
        <strong>{account.name}</strong>
        <span className="moneyv-meta">
          {KIND_LABEL[account.kind]}
          {meta ? ` · ${meta}` : ""}
        </span>
        </div>
      </div>
      <div className="account-balance">
        {isCard ? (
          <>
            <strong>{cardPosition(account.balance_paise)}</strong>
            <span className="moneyv-meta">Ledger balance</span>
            <span className="moneyv-meta">{statement}</span>
            <span className="moneyv-meta">
              Available{" "}
              {account.available_credit_paise == null
                ? "Unknown"
                : formatPaise(account.available_credit_paise)}{" "}
              · next due {nextDueLabel(account.due_day)}
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
      <span className="moneyv-meta">
        {account.balance_as_of
          ? `Ledger updated ${formatDate(account.balance_as_of)}`
          : "No ledger activity recorded yet"}
      </span>
      <div className="moneyv-actions">
        <button
          type="button"
          className="pill-button"
          onClick={() => setOpen(true)}
          aria-label={`Details for ${account.name}`}
        >
          <FileSearch size={13} strokeWidth={1.8} aria-hidden="true" />
          Details
        </button>
        <MoneyAsk prompt={`Tell me about ${account.name}.`} />
      </div>
      {open && <AccountDetailDialog account={account} onClose={() => setOpen(false)} />}
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
  lead,
}: {
  title: string;
  accounts: Account[];
  noun: string;
  emptyPrompt?: string;
  emptyAction?: string;
  emptyCopy?: string;
  lead?: ReactNode;
}) {
  const [page, setPage] = useState(0);
  const currentPage = safePage(page, accounts.length, ACCOUNT_PAGE_SIZE);
  return (
    <MoneySection title={title} note={accounts.length > 0 ? countLabel(accounts.length, noun) : undefined}>
      {accounts.length === 0 && !lead ? (
        emptyPrompt && emptyAction && emptyCopy ? (
          <MoneyEmpty prompt={emptyPrompt} action={emptyAction}>
            {emptyCopy}
          </MoneyEmpty>
        ) : null
      ) : (
        <>
          <div className="account-grid">
            {lead}
            {pageSlice(accounts, currentPage, ACCOUNT_PAGE_SIZE).map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
            {accounts.length === 0 && emptyPrompt && emptyAction && emptyCopy && (
              <MoneyEmpty prompt={emptyPrompt} action={emptyAction}>{emptyCopy}</MoneyEmpty>
            )}
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
  const cash = data.cash_quality;
  const visibleAccountCount = cards.length + loans.length + banking.length + holdings.length;
  const mismatch = cards.find((account) => cardDisagreement(account));

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
      {mismatch && (
        <div className="agent-note">
          <Orb size={30} />
          <p>
            <strong>{mismatch.name}</strong>: {cardDisagreement(mismatch)}
          </p>
          <MoneyAsk prompt={`Help me reconcile the ledger and statement balances for ${mismatch.name}.`} label="Look into it" />
        </div>
      )}

      {visibleAccountCount === 0 ? (
        <MoneyEmpty prompt="Add a bank account: " action="Ask to add one">
          No bank, cash, card, loan, or holding accounts yet. Start with the account you use every
          day.
        </MoneyEmpty>
      ) : (
        <>
          <AccountGroup
            title="Bank and cash"
            accounts={banking}
            noun="account"
            lead={
              <article className="account-tile cash-tile">
                <strong>Cash on hand</strong>
                <div className="account-balance">
                  <strong>{cash.status === "known" ? formatPaise(data.liquid_total_paise) : cash.status === "no_accounts" ? "No accounts" : "Unknown"}</strong>
                </div>
                <p>Liquid balances before card dues and bills. Not money free to spend.</p>
                {cash.status === "unknown" && <p>{countLabel(cash.unknown_count, "liquid account")} without a known balance.</p>}
              </article>
            }
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
