-- 0036: the agent's coherent read model.
-- Exposes v_legs as the primary leg-level read view (acc_* account IDs, resolved names, kinds,
-- liquidity flags, labels, positive integer paise, and reversal flags), revokes raw accounts,
-- movement_legs, and pgledger_*_view SELECT grants from agent_ro, grants SELECT on the final
-- read-model views, and verifies the 23-object agent_ro surface and backup privileges.

CREATE VIEW v_legs AS
SELECT
    m.id AS movement_id,
    t.id AS transfer_id,
    m.date,
    m.kind AS movement_kind,
    m.description,
    fa.id AS from_account_id,
    fa.name AS from_account_name,
    fa.kind AS from_account_kind,
    fa.liquid AS from_account_liquid,
    ta.id AS to_account_id,
    ta.name AS to_account_name,
    ta.kind AS to_account_kind,
    ta.liquid AS to_account_liquid,
    t.amount::bigint AS amount_paise,
    ml.category_id,
    c.name AS category_name,
    ml.payee_id,
    p.display_name AS payee_name,
    ml.note,
    ml.expected_date,
    (m.kind = 'reversal') AS is_reversal,
    coalesce(s.is_reversed, false) AS is_reversed
FROM movement_legs ml
JOIN pgledger_transfers t ON t.id = ml.transfer_id
JOIN movements m ON m.id = ml.movement_id
JOIN v_accounts fa ON fa.ledger_account_id = t.from_account_id
JOIN v_accounts ta ON ta.ledger_account_id = t.to_account_id
LEFT JOIN movement_reversal_state() s ON s.movement_id = m.id
LEFT JOIN categories c ON c.id = ml.category_id
LEFT JOIN payees p ON p.id = ml.payee_id;

REVOKE SELECT ON
    accounts,
    pgledger_accounts_view,
    pgledger_transfers_view,
    pgledger_entries_view,
    pgledger_idempotency_keys_view
FROM agent_ro;

GRANT SELECT ON
    v_accounts,
    v_balances,
    v_legs,
    v_movement_state,
    v_owed,
    v_spending
TO agent_ro;

GRANT SELECT ON
    movement_legs,
    v_accounts,
    v_balances,
    v_legs,
    v_movement_state,
    v_owed,
    v_spending
TO backup;

DO $$
DECLARE
    expected CONSTANT TEXT[] := ARRAY[
        'baselines',
        'captures',
        'categories',
        'category_rules',
        'conversations',
        'goals',
        'imports',
        'messages',
        'movements',
        'payees',
        'preferences',
        'schedule_occurrences',
        'schedule_payments',
        'scheduled_questions',
        'schedules',
        'settings',
        'statement_lines',
        'v_accounts',
        'v_balances',
        'v_legs',
        'v_movement_state',
        'v_owed',
        'v_spending'
    ];
    missing TEXT[];
    extra TEXT[];
    bad_privs BIGINT;
    bad_grant_rows BIGINT;
    obj TEXT;
BEGIN
    SELECT coalesce(array_agg(e ORDER BY e), ARRAY[]::TEXT[])
    INTO missing
    FROM unnest(expected) AS e
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = e
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND has_table_privilege('agent_ro', c.oid, 'SELECT')
    );

    SELECT coalesce(array_agg(c.relname ORDER BY c.relname), ARRAY[]::TEXT[])
    INTO extra
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND has_table_privilege('agent_ro', c.oid, 'SELECT')
      AND NOT (c.relname = ANY(expected));

    IF cardinality(missing) > 0 OR cardinality(extra) > 0 THEN
        RAISE EXCEPTION
            '0036: agent_ro SELECT set mismatch (missing=%, extra=%)',
            missing, extra;
    END IF;

    SELECT count(*) INTO bad_privs
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND (
          has_table_privilege('agent_ro', c.oid, 'INSERT')
          OR has_table_privilege('agent_ro', c.oid, 'UPDATE')
          OR has_table_privilege('agent_ro', c.oid, 'DELETE')
          OR has_table_privilege('agent_ro', c.oid, 'TRUNCATE')
          OR has_table_privilege('agent_ro', c.oid, 'REFERENCES')
          OR has_table_privilege('agent_ro', c.oid, 'TRIGGER')
      );

    SELECT count(*) INTO bad_grant_rows
    FROM information_schema.role_table_grants
    WHERE grantee = 'agent_ro'
      AND privilege_type <> 'SELECT';

    IF bad_privs > 0 OR bad_grant_rows > 0 THEN
        RAISE EXCEPTION
            '0036: agent_ro holds non-SELECT privileges (bad_privs=%, bad_grant_rows=%)',
            bad_privs, bad_grant_rows;
    END IF;

    FOR obj IN SELECT unnest(ARRAY[
        'movement_legs',
        'v_accounts',
        'v_balances',
        'v_legs',
        'v_movement_state',
        'v_owed',
        'v_spending'
    ]) LOOP
        IF NOT has_table_privilege('backup', obj, 'SELECT') THEN
            RAISE EXCEPTION '0036: backup lost SELECT on %', obj;
        END IF;
    END LOOP;
END
$$;
