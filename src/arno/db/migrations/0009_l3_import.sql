-- 0009: L3 import. The tables existed at L0; this adds the state the pipeline needs:
-- a wider import status set, the detected layout fingerprint, the pending parser text,
-- the review payload, the write groups a committed import can undo, and signed amounts
-- on statement lines.

ALTER TABLE imports DROP CONSTRAINT IF EXISTS imports_status_check;
ALTER TABLE imports
    ADD CONSTRAINT imports_status_check CHECK (
        status IN (
            'parsing', 'needs_password', 'needs_input', 'review', 'not_a_statement',
            'failed', 'committed', 'rejected'
        )
    );

ALTER TABLE imports
    ADD COLUMN attachment_id TEXT REFERENCES attachments (id),
    ADD COLUMN institution TEXT,
    ADD COLUMN fingerprint TEXT,
    ADD COLUMN helper_version TEXT,
    ADD COLUMN reason TEXT,
    ADD COLUMN counts JSONB,
    ADD COLUMN review JSONB,
    ADD COLUMN layout_id TEXT REFERENCES statement_layouts (id),
    ADD COLUMN commit_groups JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN rejected_at TIMESTAMPTZ,
    ADD COLUMN undone_at TIMESTAMPTZ,
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX imports_attachment_id_idx ON imports (attachment_id);
CREATE INDEX imports_fingerprint_idx ON imports (fingerprint) WHERE fingerprint IS NOT NULL;

ALTER TABLE statement_lines
    ADD COLUMN debit_paise BIGINT,
    ADD COLUMN credit_paise BIGINT,
    ADD COLUMN balance_paise BIGINT,
    ADD COLUMN match_detail JSONB,
    ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX statement_lines_import_state_idx ON statement_lines (import_id, match_state);

-- Jobs carry no statement password, and a retried import gets a fresh key. The existing
-- unique key stays the dedupe rule.
ALTER TABLE jobs ADD COLUMN timed_out_at TIMESTAMPTZ;

-- Coverage and review queries read imports by status; keep both cheap.
CREATE INDEX imports_status_idx ON imports (status, created_at DESC);
