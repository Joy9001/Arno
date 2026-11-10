-- 0015: categorizing after commit. Rules get a match type and a seed source, a label records the
-- rule that fired, an uncategorized row keeps the model's guess as a hint, and each categorizer
-- run is recorded.
ALTER TABLE category_rules
    ADD COLUMN match TEXT NOT NULL DEFAULT 'equals'
        CHECK (match IN ('equals', 'contains', 'regex')),
    ADD COLUMN pattern TEXT,
    ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;

UPDATE category_rules SET pattern = merchant_pattern;
ALTER TABLE category_rules ALTER COLUMN pattern SET NOT NULL;
ALTER TABLE category_rules DROP CONSTRAINT IF EXISTS category_rules_merchant_pattern_key;
ALTER TABLE category_rules DROP COLUMN merchant_pattern;
ALTER TABLE category_rules DROP CONSTRAINT IF EXISTS category_rules_source_check;
ALTER TABLE category_rules ADD CONSTRAINT category_rules_source_check
    CHECK (source IN ('user', 'llm', 'seed'));
ALTER TABLE category_rules ADD CONSTRAINT category_rules_match_pattern_key
    UNIQUE (match, pattern);

ALTER TABLE movements
    ADD COLUMN category_rule_id TEXT REFERENCES category_rules (id) ON DELETE SET NULL,
    ADD COLUMN category_hint JSONB;

CREATE TABLE categorize_runs (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('cat'),
    import_id TEXT NOT NULL REFERENCES imports (id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('now', 'batch')),
    -- The handle the agent passes back to batch_results. Null for a run that never batches.
    batch_id TEXT,
    agreement_rate NUMERIC(5, 4)
        CHECK (agreement_rate IS NULL OR (agreement_rate >= 0 AND agreement_rate <= 1)),
    agreement_checked INTEGER NOT NULL DEFAULT 0 CHECK (agreement_checked >= 0),
    labeled INTEGER NOT NULL DEFAULT 0 CHECK (labeled >= 0),
    uncategorized INTEGER NOT NULL DEFAULT 0 CHECK (uncategorized >= 0),
    -- The suspended labeling runs, one per chunk: its keys and its saved message history.
    batch_runs JSONB,
    -- Payee keys sampled from rows a rule already covers, and the category each one has.
    batch_checks JSONB,
    batch_answers JSONB,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX categorize_runs_import_id_idx ON categorize_runs (import_id);
