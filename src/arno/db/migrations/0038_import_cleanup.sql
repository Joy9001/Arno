-- 0038: import and operations cleanup.
-- Gives each import fact one home: the check verdict moves out of check_result JSON into
-- imports.check_status, and the review's duplicate counts key is removed. Drops the two
-- operation columns nothing writes and the indexes with no measurable query benefit.

-- A. Precondition checks before mutating schema or data ------------------------------------------

DO $$
DECLARE
    unexpected_status BIGINT;
BEGIN
    SELECT count(*) INTO unexpected_status
    FROM imports
    WHERE check_result ? 'status'
      AND check_result ->> 'status' NOT IN ('VERIFIED', 'DISCREPANCY', 'FAILED');
    IF unexpected_status > 0 THEN
        RAISE EXCEPTION
            '0038: % import(s) carry an unexpected check_result status', unexpected_status;
    END IF;
END
$$;

-- B. Import check status: one column instead of a JSON key ---------------------------------------

ALTER TABLE imports
    ADD COLUMN check_status TEXT,
    ADD CONSTRAINT imports_check_status_check CHECK (
        check_status IS NULL OR check_status IN ('VERIFIED', 'DISCREPANCY', 'FAILED')
    );

UPDATE imports
SET check_status = check_result ->> 'status',
    check_result = check_result - 'status'
WHERE check_result ? 'status';

-- C. Review counts: keep imports.counts, remove the duplicate JSON key ---------------------------

UPDATE imports
SET review = review - 'counts'
WHERE review ? 'counts';

-- D. Columns nothing writes ----------------------------------------------------------------------

ALTER TABLE jobs
    DROP COLUMN trace_context;

ALTER TABLE preferences
    DROP COLUMN deleted_at;

-- E. One pointer between an import and its attachment -------------------------------------------

DO $$
DECLARE
    lost_links BIGINT;
    duplicated BIGINT;
BEGIN
    SELECT count(*) INTO lost_links
    FROM attachments a
    LEFT JOIN imports i ON i.id = a.import_id
    WHERE a.import_id IS NOT NULL
      AND (i.id IS NULL OR i.attachment_id IS DISTINCT FROM a.id);
    IF lost_links > 0 THEN
        RAISE EXCEPTION
            '0038: % attachment(s) would lose their import when attachments.import_id is dropped',
            lost_links;
    END IF;

    SELECT count(*) INTO duplicated
    FROM (
        SELECT attachment_id
        FROM imports
        WHERE attachment_id IS NOT NULL
        GROUP BY attachment_id
        HAVING count(*) > 1
    ) d;
    IF duplicated > 0 THEN
        RAISE EXCEPTION
            '0038: % attachment(s) back more than one import', duplicated;
    END IF;
END
$$;

CREATE UNIQUE INDEX imports_attachment_id_uidx
    ON imports (attachment_id)
    WHERE attachment_id IS NOT NULL;

DROP INDEX imports_attachment_id_idx;

ALTER TABLE attachments
    DROP COLUMN import_id;

-- F. Indexes without measurable benefit ----------------------------------------------------------

DROP INDEX imports_account_id_idx;
DROP INDEX imports_file_hash_idx;
DROP INDEX imports_fingerprint_idx;
DROP INDEX imports_task_id_idx;
DROP INDEX accounts_kind_idx;
DROP INDEX schedules_active_idx;
DROP INDEX statement_layouts_variants_idx;
DROP INDEX parser_versions_layout_id_idx;
DROP INDEX statement_lines_import_state_idx;
