-- 0006: L1 ledger.
--
-- Schedules gain the one field an RRULE cannot express: which way an occurrence moves when it
-- lands on a weekend or an Indian holiday. "previous" gives salary paid on the last working day;
-- "next" gives a bill paid on the next working day.
--
-- The backup sidecar gets its own role: it can read the whole database for `pg_dump`, and it can
-- INSERT one row per attempt and nothing else. `backup_runs` is the status the Settings panel and
-- the later "backups stopped" alert read.

ALTER TABLE schedules
    ADD COLUMN holiday_adjust TEXT NOT NULL DEFAULT 'none'
    CHECK (holiday_adjust IN ('none', 'next', 'previous'));

CREATE TABLE backup_runs (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('bkr'),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    dump_bytes BIGINT CHECK (dump_bytes IS NULL OR dump_bytes >= 0),
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    restic_snapshot TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX backup_runs_success_idx
    ON backup_runs (finished_at DESC) WHERE status = 'success';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup') THEN
        CREATE ROLE backup LOGIN;
    END IF;
END
$$;

ALTER ROLE backup NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO backup', current_database());
    EXECUTE format('REVOKE TEMP ON DATABASE %I FROM PUBLIC', current_database());
END
$$;

GRANT USAGE ON SCHEMA public TO backup;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO backup;

-- Tables added by later migrations are readable by the dumper without another grant.
ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public
    GRANT SELECT ON TABLES TO backup;
ALTER DEFAULT PRIVILEGES FOR ROLE app IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO backup;

-- The dumper's only write. No UPDATE and no DELETE.
GRANT INSERT ON backup_runs TO backup;
