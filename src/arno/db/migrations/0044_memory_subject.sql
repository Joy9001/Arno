-- 0044: a memory's subject.
--
-- A newly stated fact of the same kind and subject supersedes the active memory instead of leaving
-- two rows for one topic. The old value, when it was saved, and when it was replaced stay in
-- `audit_log`, where every memory write is already recorded, so no history table is added and
-- `agent_ro`'s read surface is unchanged. Delete-for-good blanks those audit rows.
ALTER TABLE preferences ADD COLUMN subject TEXT;

COMMENT ON COLUMN preferences.subject IS
    'Normalized topic key; a new active memory with the same kind and subject replaces the old one.';
