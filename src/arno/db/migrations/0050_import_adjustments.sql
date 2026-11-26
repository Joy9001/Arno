-- 0050: an import's balance adjustments name their import.
--
-- Opening and stated-balance re-anchor adjustments were found by the shape of their description,
-- which broke when the label changed. `movements.import_id` names the import instead: the
-- statement whose first day an opening sits on, or the import whose commit booked a re-anchor.
-- A closing adjustment carries none, since it is never looked up and shares its day with the
-- opening of a one-day statement. `agent_ro` already reads `movements` table-wide, so the new
-- column needs no grant.
ALTER TABLE movements
    ADD COLUMN import_id TEXT REFERENCES imports (id) ON DELETE SET NULL;

CREATE INDEX movements_import_id_idx ON movements (import_id) WHERE import_id IS NOT NULL;

COMMENT ON COLUMN movements.import_id IS
    'The import a balance adjustment belongs to: its opening day or the commit that re-anchored.';

-- Backfill: the audit write group that created each adjustment is one of an import's commit
-- groups. The description shapes below are read once, here, and never again.
UPDATE movements m
SET import_id = traced.import_id
FROM (
    SELECT DISTINCT ON (a.row_id) a.row_id AS movement_id, i.id AS import_id
    FROM audit_log a
    JOIN imports i ON i.commit_groups @> jsonb_build_array(a.group_id)
    WHERE a.table_name = 'movements' AND a.before IS NULL
    ORDER BY a.row_id, i.committed_at, i.id
) traced
WHERE m.id = traced.movement_id
  AND m.kind = 'adjustment'
  AND (m.description LIKE 'Opening balance for % statement'
       OR m.description LIKE 'Stated balance re-anchor for % statement');

-- A later import's commit re-places an older one's opening, so that group traces to the wrong
-- import. An opening belongs to the import of that account whose period starts on its day.
UPDATE movements m
SET import_id = owner.import_id
FROM (
    SELECT DISTINCT ON (m2.id) m2.id AS movement_id, i.id AS import_id
    FROM movements m2
    JOIN movement_legs ml ON ml.movement_id = m2.id
    JOIN pgledger_transfers t ON t.id = ml.transfer_id
    JOIN accounts ac ON ac.ledger_account_id IN (t.from_account_id, t.to_account_id)
    JOIN imports i ON i.account_id = ac.id AND i.period_start = m2.date
    WHERE m2.kind = 'adjustment' AND m2.import_id IS NOT NULL
      AND m2.description LIKE 'Opening balance for % statement'
    ORDER BY m2.id, (i.id = m2.import_id) DESC,
             (i.status = 'committed' AND i.undone_at IS NULL) DESC, i.committed_at, i.id
) owner
WHERE m.id = owner.movement_id;

-- An opening no import starts on has no owner; left on the booking import it would read as that
-- import's re-anchor.
UPDATE movements m
SET import_id = NULL
WHERE m.kind = 'adjustment' AND m.import_id IS NOT NULL
  AND m.description LIKE 'Opening balance for % statement'
  AND NOT EXISTS (SELECT 1 FROM imports i WHERE i.id = m.import_id AND i.period_start = m.date);
