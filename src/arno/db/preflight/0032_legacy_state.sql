-- 0032 preflight: bring a database the app wrote before 0032 into the shape 0032 requires.
--
-- 0032's constraints and indexes encode invariants the application already assumed but the
-- pre-0032 schema left to code. Two of the states a pre-0032 install can legitimately hold have
-- exactly one correct repair, so they are repaired here. The rest carry a real value the app never
-- writes in a partial form, so the upgrade stops with a message naming the rows rather than
-- inventing terms or dropping identities.
--
-- This file runs only while 0032 is still pending: `run_migrations` applies it in 0032's
-- transaction, and 0032's recorded bytes and checksum stay untouched.

-- A. Reversal polarity ---------------------------------------------------------------------------
-- `reverses_id` is the authority for reversal state; `kind` is its label. A pre-0032 row with an
-- edge but another kind was written by hand or by an older path, and relabeling it is lossless.
UPDATE movements
SET kind = 'reversal'
WHERE kind <> 'reversal' AND reverses_id IS NOT NULL;

-- B. Statement balance pairing -------------------------------------------------------------------
-- A statement date with no balance describes nothing the app can read, so it is cleared rather
-- than paired with an invented balance.
UPDATE accounts
SET statement_balance_as_of = NULL
WHERE statement_balance_paise IS NULL AND statement_balance_as_of IS NOT NULL;

-- C. Case-insensitive categories ------------------------------------------------------------------
-- One transaction-local map for every category: case variants share a canonical row, and the
-- active row wins before the deterministic oldest-created_at/id tie-break. Every reference below
-- reads this one map, so no group is ranked from two different snapshots of the categories.
CREATE TEMP TABLE _arno_0032_category_map ON COMMIT DROP AS
WITH ranked AS (
    SELECT
        id,
        first_value(id) OVER (
            PARTITION BY lower(name)
            ORDER BY active DESC, created_at, id
        ) AS keep_id
    FROM categories
)
SELECT id AS category_id, keep_id FROM ranked;

-- Repoint every kind of reference through the map before the duplicate categories disappear.
UPDATE payees p
SET default_category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE p.default_category_id = m.category_id AND m.category_id <> m.keep_id;

UPDATE transfer_labels t
SET category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE t.category_id = m.category_id AND m.category_id <> m.keep_id;

UPDATE schedules s
SET category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE s.category_id = m.category_id AND m.category_id <> m.keep_id;

UPDATE category_rules r
SET category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE r.category_id = m.category_id AND m.category_id <> m.keep_id;

UPDATE schedule_occurrences o
SET category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE o.category_id = m.category_id AND m.category_id <> m.keep_id;

-- One baseline per surviving category. Join through the map so each baseline is ranked exactly
-- once: the baseline already on the canonical category wins, else the oldest/id. A baseline is
-- never its own competitor when a group has three or more case variants.
WITH ranked_baselines AS (
    SELECT
        b.id,
        row_number() OVER (
            PARTITION BY m.keep_id
            ORDER BY (b.category_id = m.keep_id) DESC, b.created_at, b.id
        ) AS position
    FROM baselines b
    JOIN _arno_0032_category_map m ON m.category_id = b.category_id
)
DELETE FROM baselines b
USING ranked_baselines r
WHERE b.id = r.id AND r.position > 1;

UPDATE baselines b
SET category_id = m.keep_id
FROM _arno_0032_category_map m
WHERE b.category_id = m.category_id AND m.category_id <> m.keep_id;

DELETE FROM categories c
USING _arno_0032_category_map m
WHERE c.id = m.category_id AND m.category_id <> m.keep_id;

-- D. Case-insensitive payee UPIs -----------------------------------------------------------------
-- Two payees whose UPI IDs differ only by case are one identity as far as every lookup is
-- concerned. Both payees and their ledgers stay; only the duplicate identifier is cleared. The
-- active payee keeps it first, then the oldest/id.
WITH ranked AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY lower(upi_id)
            ORDER BY active DESC, created_at, id
        ) AS position
    FROM payees
    WHERE upi_id IS NOT NULL
)
UPDATE payees p
SET upi_id = NULL, updated_at = now()
FROM ranked r
WHERE p.id = r.id AND r.position > 1;

-- E. States with no truthful repair ---------------------------------------------------------------
-- These names are the app's own fields, so a partial or duplicated value means hand-written SQL.
-- Naming the rows is the whole repair: an operator decides, and the transaction rolls back.
DO $$
DECLARE
    offenders BIGINT;
    first_rows TEXT;
BEGIN
    SELECT count(*) INTO offenders
    FROM accounts
    WHERE kind = 'card'
      AND (credit_limit_paise IS NULL OR statement_day IS NULL OR due_day IS NULL);
    IF offenders > 0 THEN
        SELECT string_agg(id, ', ' ORDER BY id) INTO first_rows
        FROM (SELECT id FROM accounts WHERE kind = 'card'
              AND (credit_limit_paise IS NULL OR statement_day IS NULL OR due_day IS NULL)
              ORDER BY id LIMIT 10) sample;
        RAISE EXCEPTION
            '0032 preflight: % card account(s) have no credit limit, statement day, or due day (%). '
            'Set the missing terms, then upgrade again.',
            offenders, first_rows;
    END IF;

    SELECT count(*) INTO offenders
    FROM (
        SELECT 1 FROM statement_layouts
        GROUP BY lower(institution), lower(kind)
        HAVING count(*) > 1
    ) duplicates;
    IF offenders > 0 THEN
        SELECT string_agg(institution || '/' || kind, ', ' ORDER BY institution, kind)
        INTO first_rows
        FROM (
            SELECT lower(institution) AS institution, lower(kind) AS kind
            FROM statement_layouts
            GROUP BY 1, 2 HAVING count(*) > 1
            ORDER BY 1, 2 LIMIT 10
        ) sample;
        RAISE EXCEPTION
            '0032 preflight: % statement layout(s) differ only by case (%). Merging them would '
            'renumber parser versions; delete or rename the duplicate layout, then upgrade again.',
            offenders, first_rows;
    END IF;

    SELECT count(*) INTO offenders
    FROM schedule_occurrences
    WHERE kind NOT IN (
            'income_fixed', 'income_variable', 'income_oneoff',
            'obligation', 'instalment', 'goal_contribution'
        )
       OR (subtype IS NOT NULL AND (
            subtype NOT IN ('rent', 'emi', 'subscription', 'insurance', 'family_transfer', 'other')
            OR kind <> 'obligation'
        ));
    IF offenders > 0 THEN
        RAISE EXCEPTION
            '0032 preflight: % schedule occurrence(s) carry a kind or subtype the app never wrote. '
            'Correct or delete those occurrence rows, then upgrade again.',
            offenders;
    END IF;
END
$$;
