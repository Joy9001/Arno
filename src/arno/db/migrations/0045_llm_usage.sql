-- 0045: the model-usage log.
--
-- One row per model response. The daily spend cap sums `usd` since midnight India time, so
-- `created_at` is indexed. `agent_ro` gets no grant: spend is operational data, not ledger data.
CREATE TABLE llm_usage (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    usd NUMERIC(10,6) NOT NULL
);

CREATE INDEX llm_usage_created_at_idx ON llm_usage (created_at);

-- The `cost_replay` job kind is gone. A stale schedule would keep enqueuing jobs that fail with
-- "no handler", so remove the schedule and any queued or past jobs.
DELETE FROM job_schedules WHERE kind = 'cost_replay';
DELETE FROM jobs WHERE kind = 'cost_replay';
