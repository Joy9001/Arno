-- Freeze an occurrence's terms when an actual ledger movement is first assigned to it.
CREATE TABLE schedule_occurrences (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('occ'),
    schedule_id TEXT NOT NULL REFERENCES schedules (id),
    due_date DATE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    subtype TEXT,
    outside_text BOOLEAN NOT NULL DEFAULT false,
    amount_paise BIGINT CHECK (amount_paise IS NULL OR amount_paise > 0),
    account_id TEXT REFERENCES accounts (id),
    category_id TEXT REFERENCES categories (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (schedule_id, due_date)
);

CREATE TABLE schedule_payments (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('pay'),
    occurrence_id TEXT NOT NULL REFERENCES schedule_occurrences (id),
    movement_id TEXT NOT NULL REFERENCES movements (id),
    amount_paise BIGINT NOT NULL CHECK (amount_paise > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (occurrence_id, movement_id)
);

CREATE INDEX schedule_payments_movement_idx ON schedule_payments (movement_id);

GRANT SELECT ON schedule_occurrences, schedule_payments TO agent_ro;
