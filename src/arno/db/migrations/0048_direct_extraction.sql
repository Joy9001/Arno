-- 0048: statement extraction no longer saves parsers. A file is read directly each time, so the
-- layout and parser-version tables go, with the import columns only the parser used. `paused`
-- (a parser that ran out of steps) cannot happen any more; a leftover one is a failed import
-- that Retry can read again.

UPDATE imports
SET status = 'failed', reason = coalesce(reason, 'This import was paused by an older version.')
WHERE status = 'paused';

ALTER TABLE imports DROP CONSTRAINT imports_status_check;
ALTER TABLE imports
    ADD CONSTRAINT imports_status_check CHECK (
        status IN (
            'parsing', 'needs_password', 'needs_input', 'review', 'not_a_statement',
            'failed', 'committed', 'rejected'
        )
    );

ALTER TABLE imports
    DROP COLUMN layout_id,
    DROP COLUMN parser,
    DROP COLUMN helper_version,
    DROP COLUMN fingerprint,
    DROP COLUMN task_id;

DROP TABLE parser_versions;
DROP TABLE statement_layouts;
