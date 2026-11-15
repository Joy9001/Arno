-- 0040: a card's recorded EMI terms.
-- The affordability engine priced every card EMI at a default rate, so a card now records the
-- annual interest in basis points and the processing fee in paise that an offer from it carries.
-- Both are card-only and NULL when unknown; v_accounts carries them so the projection can read
-- them, and every existing column keeps its position for the views that depend on it.

ALTER TABLE accounts
    ADD COLUMN emi_rate_bps INTEGER CHECK (emi_rate_bps BETWEEN 0 AND 10000),
    ADD COLUMN emi_fee_paise BIGINT CHECK (emi_fee_paise >= 0);

ALTER TABLE accounts
    ADD CONSTRAINT accounts_emi_terms_card_check CHECK (
        kind = 'card' OR (emi_rate_bps IS NULL AND emi_fee_paise IS NULL)
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
    a.emi_fee_paise
FROM accounts a
JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
LEFT JOIN payees p ON a.kind = 'person' AND p.person_account_id = a.id
LEFT JOIN goals g ON a.kind = 'goal' AND g.account_id = a.id;

ANALYZE accounts;
