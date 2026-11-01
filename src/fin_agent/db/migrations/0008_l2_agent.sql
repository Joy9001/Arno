-- 0008: the agent layer.
--
-- `messages.run_id` tags every message with the turn that produced it, so an ending can build the
-- transcript the same way for a finished run, a cancel, and a failure (system-design step 8).
-- `conversations.active_run_id` is the one running turn per thread; it is cleared in the same
-- transaction that saves both records, so a value left behind at boot means the process crashed.
--
-- `preferences.deleted_at` separates turning a preference off (active = false, reversible) from
-- deleting it for good in Settings (body blanked, not reversible).

ALTER TABLE conversations
    ADD COLUMN active_run_id TEXT;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_active_run_id_check
        CHECK (active_run_id IS NULL OR length(active_run_id) <= 100);

ALTER TABLE messages
    ADD COLUMN run_id TEXT;

ALTER TABLE messages
    ADD CONSTRAINT messages_run_id_check CHECK (run_id IS NULL OR length(run_id) <= 100);

CREATE INDEX messages_run_id_idx ON messages (run_id) WHERE run_id IS NOT NULL;

ALTER TABLE preferences
    ADD COLUMN deleted_at TIMESTAMPTZ;

-- The spend store creates its own tables (`spend_counters`, `spend_dedup`) as `app`; the backup
-- role's default privileges already make them readable by pg_dump. Nothing else changes for
-- `agent_ro`: it already reads conversations, messages, and preferences.

COMMENT ON COLUMN conversations.active_run_id IS
    'The running turn; cleared with run_started_at when both records are saved.';
COMMENT ON COLUMN messages.run_id IS
    'The turn that produced this message, used to rebuild the transcript on every ending.';
