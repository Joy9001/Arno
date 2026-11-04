import { MessageCircle } from "lucide-react";
import { askAgent } from "../ask";
import { formatPaise } from "../format";
import { Loading } from "../Loading";
import type { Account, AccountSummary } from "../types";
import { useApi } from "../useApi";

function ageText(account: Account): string {
  if (account.statement_age_days === null) return "No stated balance yet";
  const age = `${account.statement_age_days} day${account.statement_age_days === 1 ? "" : "s"} ago`;
  if (account.statement_balance_paise === null) return `Stated ${age}`;
  return `Stated ${formatPaise(account.statement_balance_paise)} · ${age}`;
}

function nextDueLabel(day: number | null): string {
  if (!day) return "—";
  const now = new Date();
  const due = new Date(now.getFullYear(), now.getMonth(), day);
  if (due < now) due.setMonth(due.getMonth() + 1);
  return due.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function AccountRow({ account }: { account: Account }) {
  const meta =
    [account.institution, account.last4 ? `ending ${account.last4}` : null]
      .filter(Boolean)
      .join(" · ") || account.kind;

  return (
    <article className="money-row">
      <div className="money-row-main">
        <strong>{account.name}</strong>
        <span className="fine">{meta}</span>
        <span className="fine">{ageText(account)}</span>
      </div>
      <div className="money-row-figures">
        {account.kind === "card" ? (
          <strong>
            {account.balance_paise < 0 ? "Owed " : "Credit "}
            {formatPaise(Math.abs(account.balance_paise))}
          </strong>
        ) : (
          <strong className={account.balance_paise < 0 ? "negative" : undefined}>
            {formatPaise(account.balance_paise)}
          </strong>
        )}
        {account.kind === "card" && (
          <span className="fine">
            Available {formatPaise(account.available_credit_paise ?? null)} · next due{" "}
            {nextDueLabel(account.due_day)}
          </span>
        )}
        {account.kind === "card" &&
          account.balance_paise > 0 &&
          (account.statement_balance_paise ?? 0) < 0 && (
            <span className="fine warn-text">
              Ledger and statement disagree — some card spends may be missing.
            </span>
          )}
        {account.kind === "loan" && (
          <span className="fine">
            {account.rate_bps === null ? "rate unknown" : `${account.rate_bps / 100}%`} ·{" "}
            {account.tenure_months ? `${account.tenure_months} months` : "tenure unknown"}
          </span>
        )}
      </div>
      <button
        type="button"
        className="ask-link"
        onClick={() => askAgent(`Tell me about ${account.name}.`)}
      >
        <MessageCircle size={13} aria-hidden="true" />
        Ask
      </button>
    </article>
  );
}

export function AccountsPanel() {
  const { data, error, loading, reload } = useApi<AccountSummary>("/api/accounts");

  if (loading && !data) return <Loading label="Loading accounts…" />;
  if (error) {
    return (
      <div className="panel-error">
        <p className="error">{error}</p>
        <button type="button" className="ghost small" onClick={reload}>
          Retry
        </button>
      </div>
    );
  }
  if (!data) return null;

  const cards = data.accounts.filter((account) => account.kind === "card");
  const loans = data.accounts.filter((account) => account.kind === "loan");
  const others = data.accounts.filter(
    (account) => !["card", "loan", "person", "goal"].includes(account.kind),
  );
  const banking = others.filter((account) =>
    ["bank", "cash", "wallet"].includes(account.kind),
  );
  const holdings = others.filter((account) => account.kind === "asset");
  const assets = holdings.reduce((total, account) => total + account.balance_paise, 0);

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>Where the money sits</h2>
          <p className="muted">
            Read-only. Balances are the ledger's, with the last stated balance beside them.
          </p>
        </div>
        <p className="fine">as of {data.as_of}</p>
      </header>

      <div className="summary-strip">
        <div className="summary-cell">
          <span>Spendable</span>
          <strong>{formatPaise(data.liquid_total_paise)}</strong>
          <p>Bank, cash, wallet</p>
        </div>
        <div className="summary-cell">
          <span>Card owed</span>
          <strong>{formatPaise(data.card_owed_paise)}</strong>
          <p>Outstanding on the cards</p>
        </div>
        <div className="summary-cell">
          <span>Loan outstanding</span>
          <strong>{formatPaise(data.loan_outstanding_paise)}</strong>
          <p>Principal, from the ledger</p>
        </div>
        <div className="summary-cell">
          <span>Holdings</span>
          <strong>{formatPaise(assets)}</strong>
          <p>Mutual funds, gold, EPF</p>
        </div>
      </div>

      {cards.length > 0 && (
        <>
          <div className="section-head">
            <h3>Cards</h3>
          </div>
          <div className="money-list">
            {cards.map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </div>
        </>
      )}

      {loans.length > 0 && (
        <>
          <div className="section-head">
            <h3>Loans</h3>
          </div>
          <div className="money-list">
            {loans.map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </div>
        </>
      )}

      <div className="section-head">
        <h3>Bank and cash</h3>
      </div>
      {banking.length === 0 ? (
        <div className="empty-state">
          <p>No bank, cash, or wallet accounts yet.</p>
          <button
            type="button"
            className="ask-link"
            onClick={() => askAgent("Add a bank account: ")}
          >
            <MessageCircle size={13} aria-hidden="true" />
            Ask to add one
          </button>
        </div>
      ) : (
        <div className="money-list">
          {banking.map((account) => (
            <AccountRow key={account.id} account={account} />
          ))}
        </div>
      )}

      <div className="section-head">
        <h3>Holdings</h3>
      </div>
      {holdings.length === 0 ? (
        <div className="empty-state">
          <p>No hand-updated holdings yet.</p>
          <button
            type="button"
            className="ask-link"
            onClick={() => askAgent("Add an asset: ")}
          >
            <MessageCircle size={13} aria-hidden="true" />
            Ask to add one
          </button>
        </div>
      ) : (
        <div className="money-list">
          {holdings.map((account) => (
            <AccountRow key={account.id} account={account} />
          ))}
        </div>
      )}
    </section>
  );
}
