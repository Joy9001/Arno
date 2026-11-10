-- A memory is a preference (how the agent behaves), a fact (the user's world), or a rule (how
-- their money data is treated). Existing memories become preferences.
ALTER TABLE preferences
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'preference'
    CHECK (kind IN ('preference', 'fact', 'rule'));
