-- Recaps are computed fresh from ledger rows; only "this recap was opened" is stored, so the
-- latest complete recap of each kind can be marked new until then.
CREATE TABLE recap_views (
    period_id TEXT PRIMARY KEY,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
