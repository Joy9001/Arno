-- Step 7: the fix for agent_ro's timeout. Synthetic tables only.
\set ON_ERROR_STOP off
CREATE TABLE movements(id int primary key, amount_paise bigint);
INSERT INTO movements VALUES (1, 20000);
CREATE ROLE agent_ro LOGIN;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
GRANT SELECT ON movements TO agent_ro;
REVOKE TEMP ON DATABASE postgres FROM PUBLIC;
\connect - agent_ro
\echo == 1 temp table once TEMP is revoked, in a read-only session
CREATE TEMP TABLE t2(x int);
\echo == 2 two statements through the extended protocol, as a driver with parameters sends them
SELECT 1; SELECT 2 \bind \g
\echo == 3 a setting changed inside a read-only transaction, then rolled back
BEGIN READ ONLY;
SET LOCAL statement_timeout = '2s';
SELECT set_config('statement_timeout', '0', false);
ROLLBACK;
SHOW statement_timeout;
\echo == 4 can the transaction's own read-only be turned off from a SELECT?
BEGIN READ ONLY;
SELECT set_config('transaction_read_only', 'off', true);
INSERT INTO movements VALUES (9, 9);
ROLLBACK;
\echo == 5 a heavy read inside the timeout
SELECT count(*) FROM generate_series(1, 3000) a, generate_series(1, 3000) b;
