"""Tell the owner a recap is ready: the latest complete week and month each get one inbox item.

The job runs daily, so a machine that slept through a Monday or the 1st still catches up; the
period ID dedupes the item. A month also sends a push; a week does not. A period with no spending
gets nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.ledger.common import today as ledger_today
from fin_agent.notifications import create_notification, send_push_for_kind
from fin_agent.tools import recaps

PUSH_KINDS = {"month"}


async def announce_recaps(conn: Connection, *, today: date | None = None) -> list[dict[str, Any]]:
    """Create the missing ready items. Returns the new ones."""
    day = today or ledger_today()
    latest = recaps.latest_complete(await recaps.list_periods(conn, today=day))
    created: list[dict[str, Any]] = []
    for kind in ("week", "month"):
        period = latest.get(kind)
        if period is None:
            continue
        story = await recaps.recap(conn, period.id, today=day)
        total = story["stories"][0]["figure"]
        if not total or total["paise"] <= 0:
            continue
        name = period.title if kind == "month" else period.title.lower()
        row = await create_notification(
            conn,
            kind="recap",
            title=f"Your {name} recap is ready",
            body=f"You spent {total['text']}. {story['stories'][0]['line']}",
            data={"period_id": period.id, "period_kind": kind},
            dedupe_key=f"recap:{period.id}",
        )
        if row is not None:
            created.append({**row, "period_kind": kind})
    return created


async def handle_recaps(payload: dict[str, Any]) -> dict[str, Any]:
    async with connect() as conn:
        created = await announce_recaps(conn)
        if any(row["period_kind"] in PUSH_KINDS for row in created):
            await send_push_for_kind(conn, "recap", url="/#recaps")
        return {"created": [row["id"] for row in created]}


__all__ = ["announce_recaps", "handle_recaps"]
