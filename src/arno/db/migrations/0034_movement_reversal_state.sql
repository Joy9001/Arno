-- 0034: single canonical definition of movement reversal state.
-- Moves reversal-chain evaluation from Python into PostgreSQL so every caller and SQL query
-- shares one definition of "effectively reversed", including undo-of-undo and as_of cutoffs.

CREATE FUNCTION movement_reversal_state(as_of date DEFAULT NULL)
RETURNS TABLE (movement_id text, is_reversed boolean)
LANGUAGE sql
STABLE
AS $$
WITH RECURSIVE active_edges AS MATERIALIZED (
    SELECT m.id, m.reverses_id
    FROM movements m
    WHERE m.reverses_id IS NOT NULL
      AND ($1::date IS NULL OR m.date <= $1)
),
parents AS MATERIALIZED (
    SELECT DISTINCT e.reverses_id AS id
    FROM active_edges e
    WHERE $1::date IS NULL
       OR (SELECT m.date FROM movements m WHERE m.id = e.reverses_id) <= $1
),
descendants AS (
    SELECT p.id AS root, p.id AS node, 0 AS depth, ARRAY[p.id] AS path
    FROM parents p
    UNION ALL
    SELECT d.root, e.id, d.depth + 1, d.path || e.id
    FROM descendants d
    JOIN active_edges e ON e.reverses_id = d.node
    WHERE NOT (e.id = ANY(d.path))
),
parent_states AS (
    SELECT
        d.root AS movement_id,
        sum(CASE WHEN d.depth % 2 = 0 THEN 1 ELSE -1 END) <= 0 AS is_reversed
    FROM descendants d
    GROUP BY d.root
)
SELECT ps.movement_id, ps.is_reversed
FROM parent_states ps
UNION ALL
SELECT e.id AS movement_id, false AS is_reversed
FROM active_edges e
WHERE NOT EXISTS (SELECT 1 FROM parents p WHERE p.id = e.id);
$$;

CREATE VIEW v_movement_state AS
SELECT
    m.id AS movement_id,
    (m.kind = 'reversal') AS is_reversal,
    coalesce(s.is_reversed, false) AS is_reversed,
    (m.kind <> 'reversal' AND NOT coalesce(s.is_reversed, false)) AS is_effective
FROM movements m
LEFT JOIN movement_reversal_state() s ON s.movement_id = m.id;
