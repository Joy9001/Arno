"""Baselines recomputed from real rows.

An estimate is allowed to exist from setup. Once a category has enough real spending days, a
computed baseline replaces it, and its `source` says which it is. It counts everyday spend only: a
payment that settled a schedule, and a payment marked one-off, are left out, as the projection
leaves them out of everyday spend. The divisor is the days the ledger covers in the window, not
always the nominal 90.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from arno.db.conn import Connection, connect
from arno.ledger.common import today as ledger_today

WINDOW_DAYS = 90
MIN_ROWS = 3
MIN_DAYS = 2


async def recompute_baselines(conn: Connection, *, as_of: date | None = None) -> dict[str, Any]:
    day = as_of or ledger_today()
    start = day - timedelta(days=WINDOW_DAYS - 1)
    first = await conn.fetchval("SELECT min(date) FROM movements WHERE date <= $1", day)
    # The days the ledger covers, as the projection counts them: a young ledger is not averaged
    # over the days before it began.
    covered_days = 1 if first is None else max(1, min(WINDOW_DAYS, (day - first).days))
    rows = await conn.fetch(
        """
        SELECT
            v.category_id,
            c.name AS category_name,
            sum(v.amount_paise)::bigint AS total_paise,
            count(DISTINCT v.movement_id)::bigint AS movements,
            count(DISTINCT v.date)::bigint AS days
        FROM v_spending v
        JOIN movements m ON m.id = v.movement_id
        JOIN v_movement_state ms ON ms.movement_id = v.movement_id
        JOIN categories c ON c.id = v.category_id
        WHERE v.date BETWEEN $1 AND $2
          AND v.amount_paise > 0
          AND c.active
          AND ms.is_effective
          AND NOT m.one_off
          AND NOT EXISTS (
              SELECT 1 FROM schedule_payments sp
              WHERE sp.movement_id = v.movement_id
          )
        GROUP BY v.category_id, c.name
        """,
        start,
        day,
    )
    computed: list[str] = []
    for row in rows:
        if int(row["movements"]) < MIN_ROWS or int(row["days"]) < MIN_DAYS:
            continue
        amount_per_day = max(0, round(int(row["total_paise"]) / covered_days))
        await conn.execute(
            """
            INSERT INTO baselines
                (category_id, amount_per_day_paise, source, window_days)
            VALUES ($1, $2, 'computed', $3)
            ON CONFLICT (category_id) DO UPDATE
            SET amount_per_day_paise = EXCLUDED.amount_per_day_paise,
                source = 'computed',
                window_days = EXCLUDED.window_days,
                updated_at = now()
            """,
            row["category_id"],
            amount_per_day,
            covered_days,
        )
        computed.append(str(row["category_name"]))
    return {"computed": len(computed), "categories": computed, "window_days": covered_days}


async def handle_recompute_baselines(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    async with connect() as conn:
        return await recompute_baselines(conn)


__all__ = ["WINDOW_DAYS", "handle_recompute_baselines", "recompute_baselines"]
