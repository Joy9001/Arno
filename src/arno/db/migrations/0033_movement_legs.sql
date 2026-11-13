-- 0033: replace transfer_labels and metadata->>'movement_id' with total movement_legs.
-- Every pgledger transfer gets exactly one movement_legs row in the same transaction, enforced
-- by a deferred foreign key from pgledger_transfers(id) to movement_legs(transfer_id). Label
-- columns live directly on the leg, so setting or clearing a label is an UPDATE, never an insert
-- or delete of a label row.

-- A. Create movement_legs matching transfer_labels' live catalog ---------------------------------

CREATE TABLE movement_legs (
    transfer_id TEXT PRIMARY KEY REFERENCES pgledger_transfers (id),
    movement_id TEXT NOT NULL REFERENCES movements (id),
    category_id TEXT REFERENCES categories (id),
    payee_id TEXT REFERENCES payees (id),
    expected_date DATE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- B. Verify source integrity and backfill one leg per transfer -----------------------------------

DO $$
DECLARE
    disagreed_count BIGINT;
BEGIN
    SELECT count(*) INTO disagreed_count
    FROM transfer_labels tl
    JOIN pgledger_transfers t ON t.id = tl.transfer_id
    WHERE (t.metadata ->> 'movement_id') IS DISTINCT FROM tl.movement_id;
    IF disagreed_count > 0 THEN
        RAISE EXCEPTION
            '0033: % transfer_labels row(s) disagree with pgledger_transfers.metadata movement_id',
            disagreed_count;
    END IF;
END
$$;

INSERT INTO movement_legs (
    transfer_id,
    movement_id,
    category_id,
    payee_id,
    expected_date,
    note,
    created_at,
    updated_at
)
SELECT
    t.id,
    coalesce(tl.movement_id, t.metadata ->> 'movement_id'),
    tl.category_id,
    tl.payee_id,
    tl.expected_date,
    tl.note,
    coalesce(tl.created_at, t.created_at),
    coalesce(tl.updated_at, t.created_at)
FROM pgledger_transfers t
LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id;

DO $$
DECLARE
    transfer_count BIGINT;
    leg_count BIGINT;
    missing_or_diff BIGINT;
BEGIN
    SELECT count(*) INTO transfer_count FROM pgledger_transfers;
    SELECT count(*) INTO leg_count FROM movement_legs;
    IF leg_count <> transfer_count THEN
        RAISE EXCEPTION
            '0033: movement_legs count (%) differs from pgledger_transfers count (%)',
            leg_count, transfer_count;
    END IF;

    SELECT count(*) INTO missing_or_diff
    FROM transfer_labels tl
    LEFT JOIN movement_legs ml ON ml.transfer_id = tl.transfer_id
    WHERE ml.transfer_id IS NULL
       OR ml.movement_id IS DISTINCT FROM tl.movement_id
       OR ml.category_id IS DISTINCT FROM tl.category_id
       OR ml.payee_id IS DISTINCT FROM tl.payee_id
       OR ml.expected_date IS DISTINCT FROM tl.expected_date
       OR ml.note IS DISTINCT FROM tl.note
       OR ml.created_at IS DISTINCT FROM tl.created_at
       OR ml.updated_at IS DISTINCT FROM tl.updated_at;
    IF missing_or_diff > 0 THEN
        RAISE EXCEPTION
            '0033: % transfer_labels row(s) missing or altered in movement_legs',
            missing_or_diff;
    END IF;
END
$$;

-- Carry over indexes backed by real application queries: movement_id for batched/single leg
-- lookups, and category_id for list_movements/query_movements category filters and v_spending.
CREATE INDEX movement_legs_movement_id_idx ON movement_legs (movement_id);
CREATE INDEX movement_legs_category_id_idx ON movement_legs (category_id);

-- C. Enforce total 1:1 leg ownership via deferred FK and remove the JSON movement_id home --------

ALTER TABLE pgledger_transfers
    ADD CONSTRAINT pgledger_transfers_leg_fkey
    FOREIGN KEY (id) REFERENCES movement_legs (transfer_id)
    DEFERRABLE INITIALLY DEFERRED;

DROP INDEX pgledger_transfers_movement_id_idx;

UPDATE pgledger_transfers
SET metadata = NULLIF(metadata - 'movement_id', '{}'::jsonb)
WHERE metadata ? 'movement_id';

-- D. Redefine semantic views over movement_legs and drop v_cashflow and transfer_labels ----------

DROP VIEW v_cashflow;

CREATE OR REPLACE VIEW v_spending AS
SELECT
    m.id AS movement_id,
    m.date,
    m.description,
    t.id AS transfer_id,
    ml.category_id,
    c.name AS category_name,
    ml.payee_id,
    p.display_name AS payee_name,
    (CASE WHEN t.to_account_id = sp.ledger_account_id THEN t.amount ELSE -t.amount END)::bigint
        AS amount_paise
FROM pgledger_transfers t
JOIN movement_legs ml ON ml.transfer_id = t.id
JOIN movements m ON m.id = ml.movement_id
JOIN accounts sp ON sp.kind = 'spending'
LEFT JOIN categories c ON c.id = ml.category_id
LEFT JOIN payees p ON p.id = ml.payee_id
WHERE t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id;

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
JOIN accounts a ON a.ledger_account_id = d.account_id;

CREATE OR REPLACE VIEW v_owed AS
SELECT
    p.id AS payee_id,
    p.display_name,
    a.id AS account_id,
    coalesce(sum(e.amount), 0)::bigint AS balance_paise,
    CASE
        WHEN coalesce(sum(e.amount), 0) > 0 THEN 'owes_you'
        WHEN coalesce(sum(e.amount), 0) < 0 THEN 'you_owe'
        ELSE 'settled'
    END AS direction,
    min(ml.expected_date) FILTER (
        WHERE ml.expected_date >= (now() AT TIME ZONE 'Asia/Kolkata')::date
    ) AS next_expected_date
FROM accounts a
JOIN payees p ON p.person_account_id = a.id
LEFT JOIN pgledger_entries e ON e.account_id = a.ledger_account_id
LEFT JOIN movement_legs ml ON ml.transfer_id = e.transfer_id
WHERE a.kind = 'person'
GROUP BY p.id, p.display_name, a.id;

DROP TABLE transfer_labels;

-- E. Migrate audit_log entries from transfer_labels to movement_legs -----------------------------
-- Standalone label creations (not written in the same group as their movement) and historical
-- label deletions become updates over a persistent leg, so undo clears or restores label columns
-- rather than trying to delete a leg row.

UPDATE audit_log a
SET
    table_name = 'movement_legs',
    before = CASE
        WHEN a.before IS NULL AND a.after IS NOT NULL AND NOT EXISTS (
            SELECT 1
            FROM audit_log m
            WHERE m.group_id = a.group_id
              AND m.table_name = 'movements'
              AND m.before IS NULL
              AND m.row_id = a.after ->> 'movement_id'
        ) THEN jsonb_build_object(
            'transfer_id', a.after ->> 'transfer_id',
            'movement_id', a.after ->> 'movement_id',
            'category_id', NULL,
            'payee_id', NULL,
            'expected_date', NULL,
            'note', NULL,
            'created_at', coalesce(a.after -> 'created_at', to_jsonb(a.created_at)),
            'updated_at', coalesce(a.after -> 'updated_at', a.after -> 'created_at', to_jsonb(a.created_at))
        )
        WHEN a.before IS NOT NULL THEN a.before || jsonb_build_object(
            'created_at', coalesce(a.before -> 'created_at', to_jsonb(a.created_at)),
            'updated_at', coalesce(a.before -> 'updated_at', a.before -> 'created_at', to_jsonb(a.created_at))
        )
        ELSE a.before
    END,
    after = CASE
        WHEN a.before IS NOT NULL AND a.after IS NULL THEN jsonb_build_object(
            'transfer_id', a.before ->> 'transfer_id',
            'movement_id', a.before ->> 'movement_id',
            'category_id', NULL,
            'payee_id', NULL,
            'expected_date', NULL,
            'note', NULL,
            'created_at', coalesce(a.before -> 'created_at', to_jsonb(a.created_at)),
            'updated_at', coalesce(a.before -> 'updated_at', a.before -> 'created_at', to_jsonb(a.created_at))
        )
        WHEN a.after IS NOT NULL THEN a.after || jsonb_build_object(
            'created_at', coalesce(a.after -> 'created_at', to_jsonb(a.created_at)),
            'updated_at', coalesce(a.after -> 'updated_at', a.after -> 'created_at', to_jsonb(a.created_at))
        )
        ELSE a.after
    END
WHERE a.table_name = 'transfer_labels';

ANALYZE movement_legs;

