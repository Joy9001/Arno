-- 0025: one row per real model dispatch, so a cost observation survives a tracing outage.
--
-- Cost and identity only. No prompt, answer, document, credential, or raw provider body is stored.
-- This is not a budget ledger and not read to admit requests: native counters stay the only
-- authority. `agent_ro` gets no grant; the backup role reads it through the existing default
-- privileges for `app`.
--
-- An `intent` row is written before the network call and finalized once its response (or failure)
-- is known. Finalized cost fields are immutable: finalization only fires from `intent`, and no
-- correction API exists. `exported_at` records transport acceptance for the cost-only replay.

CREATE TABLE llm_calls (
    attempt_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    span_id TEXT NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    billing_mode TEXT NOT NULL,
    workload TEXT,
    status TEXT NOT NULL DEFAULT 'intent' CHECK (status IN ('intent', 'finalized')),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    usage JSONB,
    usd NUMERIC,
    source TEXT,
    complete BOOLEAN,
    price_revision TEXT,
    provider_charge NUMERIC,
    allowance NUMERIC,
    billing_source TEXT,
    rates JSONB,
    exported_at TIMESTAMPTZ,
    CHECK (status = 'intent' OR finished_at IS NOT NULL),
    CHECK (
        status = 'intent'
        OR (source IS NOT NULL AND complete IS NOT NULL AND price_revision IS NOT NULL)
    ),
    CHECK (usd IS NULL OR usd >= 0),
    CHECK (provider_charge IS NULL OR provider_charge >= 0),
    CHECK (allowance IS NULL OR allowance >= 0)
);

-- Replay looks only for finalized rows the exporter has not accepted, oldest first.
CREATE INDEX llm_calls_recovery_idx ON llm_calls (finished_at)
    WHERE status = 'finalized' AND exported_at IS NULL;

COMMENT ON TABLE llm_calls IS
    'Per-dispatch cost observations for tracing recovery. Cost/identity only; no prompt content.';
