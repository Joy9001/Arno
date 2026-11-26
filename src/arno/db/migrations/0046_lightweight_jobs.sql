-- 0046: Postgres stays the queue, but the job tables go.
--
-- A scheduled question carries its own next fire time. A categorize run keeps its state on the
-- import. Web Push, the job queue, its schedules, and the per-write risk state are gone.

ALTER TABLE scheduled_questions ADD COLUMN next_run_at TIMESTAMPTZ;

UPDATE scheduled_questions sq
SET next_run_at = js.next_run_at
FROM job_schedules js
WHERE js.scheduled_question_id = sq.id AND js.active;

ALTER TABLE imports ADD COLUMN categorize JSONB;

COMMENT ON COLUMN imports.categorize IS
    'The categorize state: queued, running, done, partial, or failed, with its counts or error.';

-- Carry each import's newest categorize job over, so a queued or running one resumes at boot.
UPDATE imports i
SET categorize = jsonb_strip_nulls(jsonb_build_object(
    'state',
    CASE
        WHEN j.status = 'failed' THEN 'failed'
        WHEN j.status = 'pending' THEN 'queued'
        WHEN j.status = 'running' THEN 'running'
        WHEN j.result ->> 'status' = 'partial' THEN 'partial'
        ELSE 'done'
    END,
    'labeled', j.result -> 'labeled',
    'uncategorized', j.result -> 'uncategorized',
    'failed_payees', j.result -> 'failed_payees',
    'error', to_jsonb(CASE WHEN j.status = 'failed' THEN j.last_error END)
))
FROM (
    SELECT DISTINCT ON (payload ->> 'import_id')
        payload ->> 'import_id' AS import_id, status, result, last_error
    FROM jobs
    WHERE kind = 'categorize_import'
    ORDER BY payload ->> 'import_id', created_at DESC, id DESC
) j
WHERE i.id = j.import_id AND i.status = 'committed';

DROP TABLE job_schedules;
DROP TABLE jobs;
DROP TABLE risk_state;
DROP TABLE push_subscriptions;
DROP TABLE vapid_keys;

ALTER TABLE settings
    DROP COLUMN push_last_error,
    DROP COLUMN push_last_error_at,
    DROP COLUMN push_last_sent_at,
    DROP COLUMN push_opted_out_at;

DROP INDEX notifications_open_idx;
ALTER TABLE notifications
    DROP COLUMN resolved_at,
    DROP COLUMN resolved_by;
