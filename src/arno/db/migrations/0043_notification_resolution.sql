-- 0043: Payment-evidence resolution for inbox alerts and push subscription/delivery recovery state.

ALTER TABLE notifications
    ADD COLUMN resolved_at TIMESTAMPTZ,
    ADD COLUMN resolved_by TEXT;

COMMENT ON COLUMN notifications.resolved_at IS
    'When payment evidence cleared this alert; NULL while it is still open.';
COMMENT ON COLUMN notifications.resolved_by IS
    'The movement id that cleared it, or a short reason such as job_retry.';

CREATE INDEX notifications_open_idx
    ON notifications (created_at DESC, id DESC)
    WHERE resolved_at IS NULL;

ALTER TABLE settings
    ADD COLUMN push_last_error TEXT,
    ADD COLUMN push_last_error_at TIMESTAMPTZ,
    ADD COLUMN push_last_sent_at TIMESTAMPTZ,
    ADD COLUMN push_opted_out_at TIMESTAMPTZ;
