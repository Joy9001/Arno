-- 0035: single home for account display names and single-user guard.
-- Moves person and goal account display names to their owning payees/goals rows, stores account
-- IDs in pgledger_accounts.name, exposes resolved names and balance_paise via v_accounts, updates
-- v_balances to read v_accounts, and guarantees at most one row in users.

-- A. Precondition checks before mutating schema or data ------------------------------------------

DO $$
DECLARE
    unlinked_person_accounts BIGINT;
    bad_payee_targets BIGINT;
    unlinked_goal_accounts BIGINT;
    bad_goal_targets BIGINT;
    missing_names BIGINT;
    user_count BIGINT;
    non_integer_balances BIGINT;
BEGIN
    SELECT count(*) INTO unlinked_person_accounts
    FROM accounts a
    LEFT JOIN payees p ON p.person_account_id = a.id
    WHERE a.kind = 'person'
      AND p.id IS NULL;
    IF unlinked_person_accounts > 0 THEN
        RAISE EXCEPTION
            '0035: % person account(s) are not linked to a payee',
            unlinked_person_accounts;
    END IF;

    SELECT count(*) INTO bad_payee_targets
    FROM payees p
    JOIN accounts a ON a.id = p.person_account_id
    WHERE a.kind IS DISTINCT FROM 'person';
    IF bad_payee_targets > 0 THEN
        RAISE EXCEPTION
            '0035: % payee(s) reference a non-person account via person_account_id',
            bad_payee_targets;
    END IF;

    SELECT count(*) INTO unlinked_goal_accounts
    FROM accounts a
    LEFT JOIN goals g ON g.account_id = a.id
    WHERE a.kind = 'goal'
      AND g.id IS NULL;
    IF unlinked_goal_accounts > 0 THEN
        RAISE EXCEPTION
            '0035: % goal account(s) are not linked to a goal',
            unlinked_goal_accounts;
    END IF;

    SELECT count(*) INTO bad_goal_targets
    FROM goals g
    JOIN accounts a ON a.id = g.account_id
    WHERE a.kind IS DISTINCT FROM 'goal';
    IF bad_goal_targets > 0 THEN
        RAISE EXCEPTION
            '0035: % goal(s) reference a non-goal account via account_id',
            bad_goal_targets;
    END IF;

    SELECT count(*) INTO missing_names
    FROM accounts
    WHERE kind NOT IN ('person', 'goal')
      AND name IS NULL;
    IF missing_names > 0 THEN
        RAISE EXCEPTION
            '0035: % non-person/goal account(s) have NULL name',
            missing_names;
    END IF;

    SELECT count(*) INTO user_count FROM users;
    IF user_count > 1 THEN
        RAISE EXCEPTION
            '0035: expected at most 1 row in users, found %',
            user_count;
    END IF;

    SELECT count(*) INTO non_integer_balances
    FROM accounts a
    JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
    WHERE pg.balance <> trunc(pg.balance);
    IF non_integer_balances > 0 THEN
        RAISE EXCEPTION
            '0035: % account(s) have non-integer pgledger_accounts.balance',
            non_integer_balances;
    END IF;
END
$$;

-- B. One home for account display names ----------------------------------------------------------

ALTER TABLE accounts ALTER COLUMN name DROP NOT NULL;

UPDATE accounts
SET name = NULL
WHERE kind IN ('person', 'goal')
  AND name IS NOT NULL;

ALTER TABLE accounts
    ADD CONSTRAINT accounts_name_kind_check CHECK (
        (kind IN ('person', 'goal')) = (name IS NULL)
    );

-- Store stable account IDs in pgledger_accounts.name instead of mutable display names.
UPDATE pgledger_accounts pa
SET name = a.id,
    updated_at = now()
FROM accounts a
WHERE a.ledger_account_id = pa.id
  AND pa.name IS DISTINCT FROM a.id;

DO $$
DECLARE
    mismatched_pgledger BIGINT;
BEGIN
    SELECT count(*) INTO mismatched_pgledger
    FROM accounts a
    JOIN pgledger_accounts pa ON pa.id = a.ledger_account_id
    WHERE pa.name IS DISTINCT FROM a.id;
    IF mismatched_pgledger > 0 THEN
        RAISE EXCEPTION
            '0035: % pgledger_accounts row(s) do not hold their accounts.id',
            mismatched_pgledger;
    END IF;
END
$$;

-- C. Single-user guard ---------------------------------------------------------------------------

CREATE UNIQUE INDEX users_one_row_idx ON users ((true));

-- D. Resolved account view and final v_balances --------------------------------------------------

CREATE VIEW v_accounts AS
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
    coalesce(pg.balance, 0)::bigint AS balance_paise
FROM accounts a
JOIN pgledger_accounts pg ON pg.id = a.ledger_account_id
LEFT JOIN payees p ON a.kind = 'person' AND p.person_account_id = a.id
LEFT JOIN goals g ON a.kind = 'goal' AND g.account_id = a.id;

CREATE OR REPLACE VIEW v_balances AS
WITH dated AS (
    SELECT
        e.account_id,
        m.date AS as_of,
        e.amount
    FROM pgledger_entries e
    JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
    JOIN movements m ON m.id = ml.movement_id
),
daily AS (
    SELECT account_id, as_of, sum(amount) AS delta
    FROM dated
    GROUP BY account_id, as_of
)
SELECT
    a.id AS account_id,
    a.name,
    a.kind,
    a.liquid,
    d.as_of,
    sum(d.delta) OVER (
        PARTITION BY d.account_id
        ORDER BY d.as_of
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )::bigint AS balance_paise
FROM daily d
JOIN v_accounts a ON a.ledger_account_id = d.account_id;

-- E. Migrate historical audit_log images for person/goal accounts --------------------------------

UPDATE audit_log
SET
    before = CASE
        WHEN before IS NOT NULL THEN jsonb_set(before, '{name}', 'null'::jsonb)
        ELSE NULL
    END,
    after = CASE
        WHEN after IS NOT NULL THEN jsonb_set(after, '{name}', 'null'::jsonb)
        ELSE NULL
    END
WHERE table_name = 'accounts'
  AND coalesce(before ->> 'kind', after ->> 'kind') IN ('person', 'goal');

ANALYZE accounts;
ANALYZE payees;
ANALYZE goals;
ANALYZE pgledger_accounts;
