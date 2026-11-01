CREATE TABLE movements(id int primary key, amount_paise bigint);
INSERT INTO movements VALUES (1, 20000);
CREATE TABLE auth_sessions(token text);
CREATE ROLE agent_ro LOGIN;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';
GRANT SELECT ON movements TO agent_ro;
REVOKE TEMP ON DATABASE postgres FROM PUBLIC;
