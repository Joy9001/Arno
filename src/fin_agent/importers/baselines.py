"""Baselines recomputed from real rows.

An estimate is allowed to exist from setup. Once a category has enough real spending days, a
computed baseline replaces it, and its `source` says which it is.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.ledger.common import today as ledger_today

WINDOW_DAYS = 90
MIN_ROWS = 3
MIN_DAYS = 2


async def recompute_baselines(conn: Connection, *, as_of: date | None = None) -> dict[str, Any]:
    day = as_of or ledger_today()
    start = day - timedelta(days=WINDOW_DAYS - 1)
    rows = await conn.fetch(
        """
        SELECT
            tl.category_id,
            c.name AS category_name,
            sum(v.amount_paise)::bigint AS total_paise,
            count(DISTINCT v.movement_id)::bigint AS movements,
            count(DISTINCT v.date)::bigint AS days
        FROM v_spending v
        JOIN transfer_labels tl ON tl.transfer_id = v.transfer_id
        JOIN categories c ON c.id = tl.category_id
        WHERE v.date BETWEEN $1 AND $2
          AND v.amount_paise > 0
          AND c.active
        GROUP BY tl.category_id, c.name
        """,
        start,
        day,
    )
    computed: list[str] = []
    for row in rows:
        if int(row["movements"]) < MIN_ROWS or int(row["days"]) < MIN_DAYS:
            continue
        amount_per_day = max(0, round(int(row["total_paise"]) / WINDOW_DAYS))
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
            WINDOW_DAYS,
        )
        computed.append(str(row["category_name"]))
    return {"computed": len(computed), "categories": computed, "window_days": WINDOW_DAYS}


async def handle_recompute_baselines(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    async with connect() as conn:
        return await recompute_baselines(conn)


__all__ = ["WINDOW_DAYS", "handle_recompute_baselines", "recompute_baselines"]
