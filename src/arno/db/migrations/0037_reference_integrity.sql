-- 0037: reference integrity for parser versions and scheduled questions.
-- Replaces the circular statement_layouts.active_version_id pointer with parser_versions.is_active
-- and a per-layout partial unique index, and replaces the JSON-carried scheduled_question_id on
-- job_schedules with a cascading foreign key.

-- A. Precondition checks before mutating schema or data ------------------------------------------

DO $$
DECLARE
    bad_active_pointers BIGINT;
    missing_question_ids BIGINT;
    unexpected_question_keys BIGINT;
    duplicate_question_schedules BIGINT;
BEGIN
    SELECT count(*) INTO bad_active_pointers
    FROM statement_layouts sl
    WHERE sl.active_version_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM parser_versions pv
          WHERE pv.id = sl.active_version_id
            AND pv.layout_id = sl.id
      );
    IF bad_active_pointers > 0 THEN
        RAISE EXCEPTION
            '0037: % statement_layouts row(s) have active_version_id not matching a parser_versions row of the same layout',
            bad_active_pointers;
    END IF;

    SELECT count(*) INTO missing_question_ids
    FROM job_schedules js
    WHERE js.kind = 'scheduled_question'
      AND (
          nullif(js.payload ->> 'scheduled_question_id', '') IS NULL
          OR NOT EXISTS (
              SELECT 1 FROM scheduled_questions sq
              WHERE sq.id = js.payload ->> 'scheduled_question_id'
          )
      );
    IF missing_question_ids > 0 THEN
        RAISE EXCEPTION
            '0037: % scheduled_question job_schedules row(s) lack a valid scheduled_question_id',
            missing_question_ids;
    END IF;

    SELECT count(*) INTO unexpected_question_keys
    FROM job_schedules js
    WHERE js.kind <> 'scheduled_question'
      AND js.payload ? 'scheduled_question_id';
    IF unexpected_question_keys > 0 THEN
        RAISE EXCEPTION
            '0037: % non-scheduled_question job_schedules row(s) carry scheduled_question_id in payload',
            unexpected_question_keys;
    END IF;

    SELECT count(*) INTO duplicate_question_schedules
    FROM (
        SELECT payload ->> 'scheduled_question_id' AS qid
        FROM job_schedules
        WHERE kind = 'scheduled_question'
        GROUP BY 1
        HAVING count(*) > 1
    ) dups;
    IF duplicate_question_schedules > 0 THEN
        RAISE EXCEPTION
            '0037: % scheduled_question_id(s) appear more than once in job_schedules',
            duplicate_question_schedules;
    END IF;
END
$$;

-- B. Parser versions: replace circular active_version_id with parser_versions.is_active ----------

ALTER TABLE parser_versions
    ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT false;

UPDATE parser_versions pv
SET is_active = true
FROM statement_layouts sl
WHERE sl.active_version_id = pv.id
  AND pv.layout_id = sl.id;

DO $$
DECLARE
    expected_active BIGINT;
    actual_active BIGINT;
BEGIN
    SELECT count(*) INTO expected_active
    FROM statement_layouts
    WHERE active_version_id IS NOT NULL;

    SELECT count(*) INTO actual_active
    FROM parser_versions
    WHERE is_active;

    IF actual_active <> expected_active THEN
        RAISE EXCEPTION
            '0037: expected % active parser_versions row(s) after backfill, found %',
            expected_active, actual_active;
    END IF;
END
$$;

CREATE UNIQUE INDEX parser_versions_layout_active_idx
    ON parser_versions (layout_id)
    WHERE is_active;

ALTER TABLE statement_layouts
    DROP COLUMN active_version_id;

-- C. Scheduled questions: real cascading foreign key on job_schedules ----------------------------

ALTER TABLE job_schedules
    ADD COLUMN scheduled_question_id TEXT REFERENCES scheduled_questions (id) ON DELETE CASCADE;

UPDATE job_schedules
SET scheduled_question_id = payload ->> 'scheduled_question_id',
    payload = payload - 'scheduled_question_id'
WHERE kind = 'scheduled_question';

ALTER TABLE job_schedules
    ADD CONSTRAINT job_schedules_scheduled_question_check CHECK (
        (kind = 'scheduled_question') = (scheduled_question_id IS NOT NULL)
    );

DROP INDEX job_schedules_kind_payload_idx;

CREATE UNIQUE INDEX job_schedules_kind_payload_idx
    ON job_schedules (kind, payload)
    WHERE kind <> 'scheduled_question';

CREATE UNIQUE INDEX job_schedules_scheduled_question_id_idx
    ON job_schedules (scheduled_question_id)
    WHERE scheduled_question_id IS NOT NULL;

ANALYZE parser_versions;
ANALYZE statement_layouts;
ANALYZE job_schedules;
