-- 0042: a card's recorded finance charge and minimum-due terms.
-- The engine pays every card bill in full, so a card never recorded what carrying a balance
-- costs. A card now records the annual finance charge, the minimum due as a percentage of the
-- statement balance, and that minimum's floor in paise. All three are card-only and NULL when
-- unknown; v_accounts carries them so a later read can use them, and every existing column keeps
-- its position for the views that depend on it.

ALTER TABLE accounts
    ADD COLUMN finance_rate_bps INTEGER CHECK (finance_rate_bps BETWEEN 0 AND 10000),
    ADD COLUMN min_due_bps INTEGER CHECK (min_due_bps BETWEEN 0 AND 10000),
    ADD COLUMN min_due_floor_paise BIGINT CHECK (min_due_floor_paise >= 0);

ALTER TABLE accounts
    ADD CONSTRAINT accounts_finance_terms_card_check CHECK (
        kind = 'card' OR (
            finance_rate_bps IS NULL
            AND min_due_bps IS NULL
            AND min_due_floor_paise IS NULL
        )
    );

CREATE OR REPLACE VIEW v_accounts AS
SELECT
    a.id,
    a.kind,
    a.ledger_account_id,
    CASE
        WHEN a.kind = 'person' THEN p.display_name
        WHEN a.kind = 'goal' THEN g.name || ' goal'
        ELSE a.name
    END AS name,
    a.institution,
    a.last4,
    a.statement_balance_paise,
    a.statement_balance_as_of,
    a.liquid,
    a.credit_limit_paise,
    a.statement_day,
    a.due_day,
    a.rate_bps,
    a.tenure_months,
    a.emi_schedule_id,
    a.active,
    a.created_at,
    a.updated_at,
    coalesce(pg.balance, 0)::bigint AS balance_paise,
    a.emi_rate_bps,
    a.emi_fee_paise,
    a.finance_rate_bps,
    a.min_due_bps,
    a.min_due_floor_paise
FROM accounts a
JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
LEFT JOIN payees p ON a.kind = 'person' AND p.person_account_id = a.id
LEFT JOIN goals g ON a.kind = 'goal' AND g.account_id = a.id;

ANALYZE accounts;
