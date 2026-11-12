-- 0027: a batch's spend accounting, so submission and collection can agree without a second
-- budget ledger.
--
-- Batch submission spends before collection: the estimate is posted to the native counters
-- before the first request can auto-submit, and collection posts `actual - estimate` once.
-- `accounting` holds only what recovery needs: the estimate and its USD value, the counter keys
-- it was posted to, the stable estimate/settlement tokens, the item count and model identity,
-- and whether each posting was applied. Cost observations themselves live in `llm_calls`; this
-- is not a budget authority and is never read to admit a request.

ALTER TABLE categorize_runs
    ADD COLUMN accounting JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN categorize_runs.accounting IS
    'Batch estimate/settlement intent: counter keys, USD estimate, replay tokens, applied phases.';
