-- 0021: the activity feed polls for jobs that finished after a timestamp, so index that column
-- only for rows that have a finish time.
CREATE INDEX jobs_finished_idx ON jobs (finished_at) WHERE status IN ('done', 'failed');
