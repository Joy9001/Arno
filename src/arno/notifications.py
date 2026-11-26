"""The in-app inbox, and an optional webhook ping for each new item.

Every proactive item lands in `notifications`. `dedupe_key` makes a task safe to rerun after a
restart: a reminder, a risk warning, a pattern proposal, and a scheduled answer each key on their
own period. With `ARNO_NOTIFY_WEBHOOK_URL` set, a new (not deduplicated) item is also posted there.

The inbox can grow without limit, so Home asks for a small unread-first window and the full list
walks backwards with a keyset cursor. Ordering is `created_at DESC, id DESC`; the id is the
deterministic tie-break, so paging never repeats or drops a row.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from arno.config import get_settings
from arno.db.conn import Connection
from arno.jobs.background import spawn

logger = logging.getLogger("arno.notifications")

MAX_LIMIT = 200
WEBHOOK_TIMEOUT_SECONDS = 10


def _now() -> datetime:
    return datetime.now(UTC)


async def post_webhook(
    url: str, title: str, body: str | None, *, transport: httpx.AsyncBaseTransport | None = None
) -> None:
    """Send one item to a Telegram bot URL (form field `text`) or an ntfy-style topic URL."""
    try:
        async with httpx.AsyncClient(
            timeout=WEBHOOK_TIMEOUT_SECONDS, transport=transport
        ) as client:
            if urlsplit(url).hostname == "api.telegram.org":
                response = await client.post(url, data={"text": f"{title}\n{body or ''}".strip()})
            else:
                response = await client.post(
                    url,
                    content=(body or title).encode(),
                    headers={"Title": title.encode("ascii", "ignore").decode()},
                )
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - a webhook failure never fails the write
        # The URL can carry a bot token, so only the error type is logged.
        logger.warning("notification webhook failed (%s)", type(exc).__name__)


async def create_notification(
    conn: Connection,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any] | None:
    """Insert one inbox item. A duplicate key returns `None` instead of a second item."""
    row = await conn.fetchrow(
        """
        INSERT INTO notifications (kind, title, body, data, dedupe_key)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING *
        """,
        kind,
        title,
        body,
        data or {},
        dedupe_key,
    )
    url = get_settings().notify_webhook_url
    if row is not None and url:
        spawn(post_webhook(url, title, body), name=f"webhook-{row['id']}")
    return row


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "data": row["data"] or {},
        "read_at": row["read_at"].isoformat() if row["read_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


async def mark_read(conn: Connection, notification_id: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        UPDATE notifications
        SET read_at = coalesce(read_at, now())
        WHERE id = $1
        RETURNING *
        """,
        notification_id,
    )
    return _payload(row) if row is not None else None


async def mark_all_read(conn: Connection) -> int:
    status = await conn.execute("UPDATE notifications SET read_at = now() WHERE read_at IS NULL")
    return int(status.split()[-1])


def _clamp(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


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
    """The newest `limit` open items with unread first, however old the unread ones are."""
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
    conn: Connection,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One page of the inbox, newest first, with a cursor for the next page."""
    limit = _clamp(limit)
    clauses: list[str] = []
    params: list[Any] = []
    if cursor:
        created_at, notification_id = _decode_cursor(cursor)
        clauses.append("(created_at, id) < ($1, $2)")
        params.extend([created_at, notification_id])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = await conn.fetch(
        f"""
        SELECT * FROM notifications
        {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `where` is built from constant fragments only, never user text
        *params,
        limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}" if has_more and rows else None
    )
    return {
        "notifications": [_payload(row) for row in rows],
        "unread_count": await count_unread(conn),
        "as_of": _now().isoformat(),
        "next_cursor": next_cursor,
    }


def seed_chat_text(notification: dict[str, Any]) -> str:
    """Build the first chat message from an inbox item."""
    data = notification.get("data") or {}
    title = str(notification.get("title") or "Notification")
    body = str(notification.get("body") or "").strip()
    text = f"From my inbox: {title}"
    if body:
        text += f"\n\n{body}"
    if data.get("suggested_rule"):
        text += f"\n\nSuggested schedule rule: {data['suggested_rule']}"
    if data.get("expected_date"):
        text += f"\n\nExpected date: {data['expected_date']}"
    movement_ids = data.get("movement_ids")
    if isinstance(movement_ids, list) and movement_ids:
        text += "\n\nPayments behind it: " + ", ".join(str(item) for item in movement_ids)
    text += "\n\nHelp me with this."
    return text


__all__ = [
    "count_unread",
    "create_notification",
    "list_page",
    "mark_all_read",
    "mark_read",
    "post_webhook",
    "seed_chat_text",
    "unread_first",
]
