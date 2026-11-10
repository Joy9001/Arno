-- 0022: a job rule's start, so a finite rule can exhaust.
--
-- An RRULE counts `COUNT` and phases `INTERVAL` from its start (dtstart). The scheduler built
-- dtstart from "now" on every run, so `FREQ=DAILY;COUNT=3` on a scheduled question fired forever.
-- Store the first occurrence the schedule was created for and pass it back on every advance.

ALTER TABLE job_schedules
    ADD COLUMN dtstart TIMESTAMPTZ;

-- Existing rows keep running: their next run is a real occurrence of their rule, so anchoring
-- there preserves the phase and (for a finite rule) restarts the count from it.
UPDATE job_schedules SET dtstart = next_run_at WHERE dtstart IS NULL;

COMMENT ON COLUMN job_schedules.dtstart IS
    'The rule''s first occurrence, the dtstart COUNT and INTERVAL are counted from.';
