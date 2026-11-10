-- 0024: an import's categorization is attributable to it. The commit queues the categorizer, so
-- the rules engine and the categorizer label this import's rows, and write `llm` rules, in their
-- own write groups. `commit_groups` holds the commit's own groups; this holds those later groups,
-- oldest first, so undoing the import reverts the labels and rules it gave rise to.

ALTER TABLE imports
    ADD COLUMN categorize_groups JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN imports.categorize_groups IS
    'Write groups the rules engine and categorizer made from this import, oldest first.';

-- Backfill: an agent write group whose ledger rows all sit on one import's statement lines was
-- that import's categorization. Groups that span imports, or hold no movement or label, are left
-- out. Ordering by the group's first audit row keeps the undo order right.
WITH agent_writes AS (
    SELECT a.group_id, a.created_at, sl.import_id
    FROM audit_log a
    JOIN movements m ON a.table_name = 'movements' AND m.id = a.row_id
    JOIN statement_lines sl ON sl.movement_id = m.id
    WHERE a.actor = 'agent' AND a.undone_at IS NULL
    UNION ALL
    SELECT a.group_id, a.created_at, sl.import_id
    FROM audit_log a
    JOIN transfer_labels tl ON a.table_name = 'transfer_labels' AND tl.transfer_id = a.row_id
    JOIN statement_lines sl ON sl.movement_id = tl.movement_id
    WHERE a.actor = 'agent' AND a.undone_at IS NULL
),
per_group AS (
    SELECT group_id, min(import_id) AS import_id, count(DISTINCT import_id) AS imports,
           min(created_at) AS first_at
    FROM agent_writes
    GROUP BY group_id
),
per_import AS (
    SELECT import_id, jsonb_agg(group_id ORDER BY first_at, group_id) AS groups
    FROM per_group
    WHERE imports = 1
    GROUP BY import_id
)
UPDATE imports i
SET categorize_groups = p.groups
FROM per_import p
WHERE p.import_id = i.id;
