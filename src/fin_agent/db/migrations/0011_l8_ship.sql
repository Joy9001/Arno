-- 0011: L8 ship. Fresh passkey re-verification, and one content manifest for the restore drill.
--
-- `sessions.reverified_at` records the last time this session re-authenticated with a passkey.
-- Export and passkey changes require it to be recent, so a borrowed unlocked browser can't walk
-- out with the ledger or add a device.
--
-- `fin_agent_backup_manifest()` is the backup sidecar's proof of what it shipped. It walks every
-- base table and records a row count and a content hash, so a restore can prove the database came
-- back rather than only that some dump restored.

ALTER TABLE sessions
    ADD COLUMN reverified_at TIMESTAMPTZ;

ALTER TABLE webauthn_challenges
    DROP CONSTRAINT webauthn_challenges_kind_check;

ALTER TABLE webauthn_challenges
    ADD CONSTRAINT webauthn_challenges_kind_check
    CHECK (kind IN ('setup', 'login', 'reverify'));

CREATE OR REPLACE FUNCTION fin_agent_backup_manifest()
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
    table_row RECORD;
    row_count BIGINT;
    content_hash TEXT;
    tables JSONB := '{}'::jsonb;
BEGIN
    FOR table_row IN
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    LOOP
        EXECUTE format(
            'SELECT count(*), md5(coalesce(string_agg(md5(t::text), %L ORDER BY md5(t::text)), %L)) FROM %I t',
            '', '', table_row.table_name
        )
        INTO row_count, content_hash;

        tables := tables || jsonb_build_object(
            table_row.table_name,
            jsonb_build_object('rows', row_count, 'md5', content_hash)
        );
    END LOOP;

    RETURN jsonb_build_object(
        'format', 'fin-agent-backup-manifest',
        'version', 1,
        'created_at', now(),
        'tables', tables
    );
END
$$;

REVOKE ALL ON FUNCTION fin_agent_backup_manifest() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fin_agent_backup_manifest() TO backup;
