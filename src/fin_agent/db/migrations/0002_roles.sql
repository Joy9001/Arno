-- 0002: the two database roles. The API connects as `app` and owns the schema.
-- `agent_ro` is the boundary for model-written SQL: SELECT grants only (see 0005), no TEMP,
-- read-only by default, and a two-second statement timeout. Its password is set from
-- AGENT_RO_PASSWORD at boot; the role has no password until then.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro LOGIN;
    END IF;
END
$$;

ALTER ROLE agent_ro NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '2s';

DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO agent_ro', current_database());
    EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC', current_database());
END
$$;
