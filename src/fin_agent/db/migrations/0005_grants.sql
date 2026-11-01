-- 0005: agent_ro's grants. This list is the boundary for model-written SQL: SELECT only, on the
-- ledger, plan, people, categorization, settings, and conversation tables and views. No auth
-- tables, no audit log, no jobs, no attachments, no statement layouts.
--
-- Future migrations must grant new readable objects explicitly. There are no ALTER DEFAULT
-- PRIVILEGES here on purpose.
GRANT USAGE ON SCHEMA public TO agent_ro;

GRANT SELECT ON
    -- pgledger's read views (functions run with the caller's rights, so agent_ro can't write).
    pgledger_accounts_view,
    pgledger_transfers_view,
    pgledger_entries_view,
    pgledger_idempotency_keys_view,
    -- The ledger.
    accounts,
    movements,
    transfer_labels,
    captures,
    imports,
    statement_lines,
    -- The plan.
    schedules,
    goals,
    scheduled_questions,
    -- People.
    payees,
    -- Categorization.
    categories,
    category_rules,
    baselines,
    -- Memory and conversations.
    preferences,
    conversations,
    messages,
    -- Instance settings.
    settings
TO agent_ro;

GRANT SELECT ON
    v_spending,
    v_cashflow,
    v_balances,
    v_owed
TO agent_ro;
