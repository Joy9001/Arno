-- 0041: detected recurring series and the one-off movement flag.
-- Adds `movements.one_off` so non-recurring big expenses can be excluded from spend history, and
-- `series_detections` to store Bayesian-detected spend and income series computed by the pattern
-- job for proposals, attribution, and unconfirmed-series projection.

ALTER TABLE movements
    ADD COLUMN one_off BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN movements.one_off IS
    'A one-off payment will not recur, so the projection leaves it out of spend history.';

CREATE TABLE series_detections (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('det'),
    payee_id TEXT NOT NULL REFERENCES payees (id),
    kind TEXT NOT NULL CHECK (kind IN ('spend', 'income')),
    family TEXT NOT NULL,
    rrule TEXT NOT NULL,
    starts DATE NOT NULL,
    holiday_adjust TEXT NOT NULL,
    description TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    alive DOUBLE PRECISION NOT NULL CHECK (alive BETWEEN 0 AND 1),
    skip DOUBLE PRECISION NOT NULL CHECK (skip BETWEEN 0 AND 1),
    jitter JSONB NOT NULL,
    amount_kind TEXT NOT NULL CHECK (amount_kind IN ('fixed', 'stepped', 'variable')),
    level_paise BIGINT NOT NULL,
    low_paise BIGINT NOT NULL,
    high_paise BIGINT NOT NULL,
    samples BIGINT[] NOT NULL,
    matched_movement_ids TEXT[] NOT NULL,
    card_id TEXT REFERENCES accounts (id),
    category_id TEXT REFERENCES categories (id),
    as_of DATE NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (payee_id, kind)
);

CREATE INDEX series_detections_as_of_idx ON series_detections (as_of);

GRANT SELECT ON series_detections TO agent_ro;
