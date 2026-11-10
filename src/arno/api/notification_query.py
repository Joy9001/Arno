"""Bounded reads over the notification inbox.

The inbox can grow without limit, so Home asks for a small unread-first window and the full
list walks backwards with a keyset cursor. Ordering is `created_at DESC, id DESC`; the id is
the deterministic tie-break, so paging never repeats or drops a row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from arno.db.conn import Connection
from arno.notifications import _payload

MAX_LIMIT = 200


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _encode_cursor(created_at: datetime, notification_id: str) -> str:
    return f"{created_at.isoformat()}|{notification_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created_at, notification_id = cursor.rsplit("|", 1)
        if not notification_id:
            raise ValueError
        return datetime.fromisoformat(created_at), notification_id
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid notification cursor") from exc


async def count_unread(conn: Connection) -> int:
    row = await conn.fetchrow_required(
        "SELECT count(*) AS n FROM notifications WHERE read_at IS NULL"
    )
    return int(row["n"])


async def unread_first(conn: Connection, *, limit: int = 4) -> list[dict[str, Any]]:
    """The newest `limit` items with unread first, however old the unread ones are."""
    rows = await conn.fetch(
        """
        SELECT * FROM notifications
        ORDER BY (read_at IS NULL) DESC, created_at DESC, id DESC
        LIMIT $1
        """,
        _clamp(limit),
    )
    return [_payload(row) for row in rows]


async def list_page(
    conn: Connection, *, limit: int = 50, cursor: str | None = None
) -> dict[str, Any]:
    """One page of the full inbox, newest first, with a cursor for the next page."""
    limit = _clamp(limit)
    where = ""
    params: list[Any] = []
    if cursor:
        created_at, notification_id = _decode_cursor(cursor)
        where = "WHERE (created_at, id) < ($1, $2)"
        params.extend([created_at, notification_id])

    rows = await conn.fetch(
        f"""
        SELECT * FROM notifications
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `where` is a constant fragment, never user text
        *params,
        limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        _encode_cursor(rows[-1]["created_at"], rows[-1]["id"]) if has_more and rows else None
    )
    return {
        "notifications": [_payload(row) for row in rows],
        "unread_count": await count_unread(conn),
        "as_of": datetime.now(UTC).isoformat(),
        "next_cursor": next_cursor,
    }


__all__ = ["count_unread", "list_page", "unread_first"]
