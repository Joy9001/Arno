-- 0031: measured indexes from ten-year scale baseline and drop exact duplicate index.
-- Plain CREATE INDEX (not CONCURRENTLY): migrations run in one transaction, tables are <100k rows,
-- and this is a single-user app migrated while idle.

-- Serves _transfer_rows (movements.py:46) and reconcile joins: 6.922 ms -> 0.101 ms; reconcile 2.434 -> 0.509 ms.
CREATE INDEX pgledger_transfers_movement_id_idx
    ON pgledger_transfers ((metadata->>'movement_id'));

-- Serves per-request writes-since-snapshot count (capabilities.py:373): 6.770 ms -> 0.257 ms (index-only scan).
CREATE INDEX audit_log_created_at_idx
    ON audit_log (created_at);

-- Exact duplicate of accounts_ledger_account_id_key (UNIQUE (ledger_account_id) on accounts).
DROP INDEX accounts_ledger_account_id_idx;
