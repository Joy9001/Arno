-- Step 7: what can model-written SQL do as agent_ro? Synthetic tables only.
\set ON_ERROR_STOP off
CREATE TABLE movements(id int primary key, amount_paise bigint);
INSERT INTO movements VALUES (1, 20000);
CREATE TABLE auth_sessions(token text);
INSERT INTO auth_sessions VALUES ('secret-token');
CREATE ROLE agent_ro LOGIN;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
GRANT SELECT ON movements TO agent_ro;
\connect - agent_ro
\echo == 1 read allowed
SELECT count(*) FROM movements;
\echo == 2 write refused
INSERT INTO movements VALUES (2, 1);
\echo == 3 turn read-only off, then write: still no grant
SET default_transaction_read_only = off;
INSERT INTO movements VALUES (3, 1);
\echo == 4 auth table refused
SELECT * FROM auth_sessions;
\echo == 5 catalog: can it see the auth table exists, and read password hashes?
SELECT count(*) AS tables_visible FROM pg_tables WHERE tablename = 'auth_sessions';
SELECT rolpassword FROM pg_authid LIMIT 1;
\echo == 6 server files and programs
SELECT pg_read_file('postgresql.conf');
COPY movements TO PROGRAM 'whoami';
COPY movements TO 'movements.csv';
\echo == 7 create objects
CREATE TABLE t(x int);
CREATE TEMP TABLE t2(x int);
\echo == 8 timeout: plain sleep past 2s
SELECT pg_sleep(4);
\echo == 9 timeout: turned off in the same session by SET, then sleep
SET statement_timeout = 0;
SHOW statement_timeout;
SELECT pg_sleep(3) AS slept_past_the_timeout;
RESET statement_timeout;
\echo == 10 timeout: turned off from inside one SELECT
SELECT set_config('statement_timeout', '0', false), pg_sleep(3);
SHOW statement_timeout;
RESET statement_timeout;
\echo == 11 two statements in one string
SELECT 1; SELECT 2;
\echo == 12 environment and settings
SELECT current_setting('data_directory');
SHOW config_file;
