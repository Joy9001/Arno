import { formatPaise } from "../format";
import type { Account, AccountSummary } from "../types";
import { useApi } from "../useApi";

function ageText(account: Account): string {
  if (account.statement_age_days === null) return "No stated balance yet";
  return `Stated ${account.statement_age_days} day${account.statement_age_days === 1 ? "" : "s"} ago`;
}

function AccountRow({ account }: { account: Account }) {
  return (
    <article className="account">
      <div className="account-head">
        <div>
          <h3>{account.name}</h3>
          <p className="fine">
            {[account.institution, account.last4 ? `ending ${account.last4}` : null]
              .filter(Boolean)
              .join(" · ") || account.kind}
          </p>
        </div>
        <span className={`badge ${account.liquid ? "good" : "muted"}`}>{account.kind}</span>
      </div>
      <p className={`amount ${account.balance_paise < 0 ? "negative" : ""}`}>
        {formatPaise(account.balance_paise)}
      </p>
      {account.kind === "card" && (
        <dl className="mini">
          <div>
            <dt>Owed</dt>
            <dd>{formatPaise(account.card_outstanding_paise)}</dd>
          </div>
          <div>
            <dt>Available</dt>
            <dd>{formatPaise(account.available_credit_paise)}</dd>
          </div>
          <div>
            <dt>Cycle</dt>
            <dd>
              statement {account.statement_day}, due {account.due_day}
            </dd>
          </div>
        </dl>
      )}
      {account.kind === "loan" && (
        <dl className="mini">
          <div>
            <dt>Outstanding</dt>
            <dd>{formatPaise(account.loan_outstanding_paise)}</dd>
          </div>
          <div>
            <dt>Rate</dt>
            <dd>{account.rate_bps === null ? "unknown" : `${account.rate_bps / 100}%`}</dd>
          </div>
          <div>
            <dt>Tenure</dt>
            <dd>{account.tenure_months ? `${account.tenure_months} months` : "unknown"}</dd>
          </div>
        </dl>
      )}
      <p className="fine">{ageText(account)}</p>
    </article>
  );
}

export function AccountsPanel() {
  const { data, error, loading } = useApi<AccountSummary>("/api/accounts");

  if (loading && !data) return <p className="muted">Loading accounts…</p>;
  if (error) return <p className="error">{error}</p>;
  if (!data) return null;

  const cards = data.accounts.filter((account) => account.kind === "card");
  const loans = data.accounts.filter((account) => account.kind === "loan");
  const others = data.accounts.filter(
    (account) => account.kind !== "card" && account.kind !== "loan",
  );

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Accounts and cards</p>
          <h2>Where the money sits</h2>
          <p className="muted">Read-only. Balances are the ledger's, with the last stated balance beside them.</p>
        </div>
        <p className="fine">as of {data.as_of}</p>
      </header>

      <div className="tiles">
        <div className="tile">
          <span>Spendable</span>
          <strong>{formatPaise(data.liquid_total_paise)}</strong>
        </div>
        <div className="tile">
          <span>Card owed</span>
          <strong>{formatPaise(data.card_owed_paise)}</strong>
        </div>
        <div className="tile">
          <span>Loan outstanding</span>
          <strong>{formatPaise(data.loan_outstanding_paise)}</strong>
        </div>
      </div>

      {cards.length > 0 && (
        <>
          <h3 className="section-title">Cards</h3>
          <div className="account-grid">
            {cards.map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </div>
        </>
      )}

      {loans.length > 0 && (
        <>
          <h3 className="section-title">Loans</h3>
          <div className="account-grid">
            {loans.map((account) => (
              <AccountRow key={account.id} account={account} />
            ))}
          </div>
        </>
      )}

      <h3 className="section-title">Accounts and assets</h3>
      {others.length === 0 ? (
        <p className="muted">No accounts yet.</p>
      ) : (
        <div className="account-grid">
          {others.map((account) => (
            <AccountRow key={account.id} account={account} />
          ))}
        </div>
      )}
    </section>
  );
}
