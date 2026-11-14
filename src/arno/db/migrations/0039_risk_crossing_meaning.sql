-- 0039: the projection's first floor crossing changes meaning.
-- It was the first day the median path went below the floor; it is now the first date by which
-- more than 10% of simulated futures have, which falls earlier by construction. A stored old date
-- compared with a new one would read as "the crossing moved earlier" and warn once for no reason,
-- so the stored crossing is cleared and the next risk check records a new one.

UPDATE risk_state SET first_floor_crossing = NULL;
