"""The in-app inbox and contentless web push.

Every proactive item lands in `notifications`. Push is only a ping: the payload carries a generic
sentence and a URL, never a merchant, an amount, or a balance, because it passes through the
browser vendor's push service. The full item is read from the inbox after the app opens.

`dedupe_key` makes a handler safe to rerun after a crash: a reminder, a risk warning, a pattern
proposal, the weekly briefing, and a scheduled answer each key on their own period.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

from pywebpush import WebPushException, webpush

from arno.db.conn import Connection

logger = logging.getLogger("arno.notifications")

# Contentless pings: the browser vendor sees only this, and never a financial number.
PUSH_BODIES = {
    "weekly_briefing": "Your weekly briefing is ready",
    "reminder": "A payment reminder is ready",
    "risk_warning": "A projection warning is ready",
    "new_subscription": "A recurring charge is ready to review",
    "missed_recurring": "A missed recurring charge is ready to review",
    "scheduled_question": "Your scheduled question has an answer",
    "backup_stale": "Arno needs a backup check",
    "job_failed": "A background job needs attention",
    "recap": "Your monthly recap is ready",
    "categorize_done": "Your statement is categorized",
}
DEFAULT_PUSH_BODY = "You have a new notification"
PUSH_TITLE = "Arno"


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_vapid_keys(
    conn: Connection, *, subject: str = "mailto:arno@localhost"
) -> dict[str, Any]:
    """Generate the instance's VAPID key pair once, on first run."""
    row = await conn.fetchrow("SELECT * FROM vapid_keys WHERE id = 1")
    if row is not None:
        return row

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid02

    # RFC 8292 fixes the curve at P-256. The key is generated here rather than by
    # `Vapid02.generate_keys`, whose property the untyped library leaves unreadable to the
    # checker, and handed to it so it still signs.
    private_key = ec.generate_private_key(ec.SECP256R1())
    vault = Vapid02(private_key)
    public_point = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(public_point).decode("ascii").rstrip("=")
    private_key = vault.private_pem().decode("ascii")
    await conn.execute(
        """
        INSERT INTO vapid_keys (id, public_key, private_key, subject)
        VALUES (1, $1, $2, $3)
        ON CONFLICT (id) DO NOTHING
        """,
        public_key,
        private_key,
        subject,
    )
    return await conn.fetchrow_required("SELECT * FROM vapid_keys WHERE id = 1")


async def public_push_key(conn: Connection) -> dict[str, Any]:
    row = await ensure_vapid_keys(conn)
    return {"public_key": row["public_key"], "subject": row["subject"]}


async def subscribe(
    conn: Connection,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> dict[str, Any]:
    if not endpoint.startswith("https://"):
        raise ValueError("A push endpoint must be HTTPS")
    if not p256dh or not auth:
        raise ValueError("A push subscription needs p256dh and auth keys")
    row = await conn.fetchrow_required(
        """
        INSERT INTO push_subscriptions (endpoint, p256dh, auth)
        VALUES ($1, $2, $3)
        ON CONFLICT (endpoint) DO UPDATE
        SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, last_used_at = now()
        RETURNING id, endpoint, created_at, last_used_at
        """,
        endpoint,
        p256dh,
        auth,
    )
    return row


async def unsubscribe(conn: Connection, *, endpoint: str) -> bool:
    row = await conn.fetchrow(
        "DELETE FROM push_subscriptions WHERE endpoint = $1 RETURNING id", endpoint
    )
    return row is not None


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
    if dedupe_key:
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
    else:
        row = await conn.fetchrow(
            """
            INSERT INTO notifications (kind, title, body, data)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            kind,
            title,
            body,
            data or {},
        )
    return row


async def list_notifications(conn: Connection, *, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    rows = await conn.fetch(
        "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT $1",
        limit,
    )
    unread = (
        await conn.fetchrow_required(
            "SELECT count(*) AS n FROM notifications WHERE read_at IS NULL"
        )
    )["n"]
    return {
        "notifications": [_payload(row) for row in rows],
        "unread_count": int(unread),
        "as_of": _now().isoformat(),
    }


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


def push_body(kind: str) -> str:
    return PUSH_BODIES.get(kind, DEFAULT_PUSH_BODY)


async def _push(
    conn: Connection,
    *,
    kind: str,
    url: str = "/",
) -> int:
    """Send one generic ping to every subscription, dropping endpoints that are gone."""
    # TODO(L6): live browser-vendor push delivery is untested on this host; tests mock webpush.
    keys = await ensure_vapid_keys(conn)
    subscriptions = await conn.fetch("SELECT * FROM push_subscriptions")
    if not subscriptions:
        return 0
    payload = json.dumps(
        {"title": PUSH_TITLE, "body": push_body(kind), "url": url},
        separators=(",", ":"),
    )
    sent = 0
    for subscription in subscriptions:
        try:
            # pywebpush is blocking and can wait up to its timeout, and the job loop shares this
            # event loop with requests, so one slow push service must not stall the API.
            await asyncio.to_thread(
                webpush,
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
                },
                data=payload,
                vapid_private_key=keys["private_key"],
                vapid_claims={"sub": keys["subject"]},
                timeout=10,
            )
            await conn.execute(
                "UPDATE push_subscriptions SET last_used_at = now() WHERE id = $1",
                subscription["id"],
            )
            sent += 1
        except WebPushException as exc:
            # `WebPushException.status_code` reads both a requests response and an aiohttp one.
            if exc.status_code in {404, 410}:
                await conn.execute(
                    "DELETE FROM push_subscriptions WHERE id = $1", subscription["id"]
                )
            else:
                logger.warning("push to %s failed (%s)", subscription["id"], type(exc).__name__)
        except Exception:  # noqa: BLE001 - a push failure must never fail the job or write
            logger.exception("push to %s failed", subscription["id"])
    return sent


async def send_push_for_kind(conn: Connection, kind: str, *, url: str = "/") -> int:
    """Public entry point for a handler after it commits its notification."""
    try:
        return await _push(conn, kind=kind, url=url)
    except Exception:  # noqa: BLE001 - push is best effort
        logger.exception("push delivery for %s failed", kind)
        return 0


def seed_chat_text(notification: dict[str, Any]) -> tuple[str, bool]:
    """Build the first chat message from an inbox item, preserving outside-text markers.

    Returns `(text, outside)`; `outside` tells the caller to set
    `conversations.outside_text_at`, so a proposal carrying a statement merchant still asks for a
    confirming tap before a lasting write.
    """
    data = notification.get("data") or {}
    title = str(notification.get("title") or "Notification")
    body = str(notification.get("body") or "").strip()
    # A pattern proposal names a merchant; a reminder names a schedule. Both can carry a payee
    # name that came from a statement, so both are marked when the item is outside text.
    label = data.get("merchant") or data.get("name")
    outside = bool(data.get("outside_text"))
    if outside and label:
        from arno.tools.mark import mark

        marked = mark(str(label)) or str(label)
        title = title.replace(str(label), marked)
        body = body.replace(str(label), marked)
    text = f"From my inbox: {title}"
    if body:
        text += f"\n\n{body}"
    if data.get("suggested_rule"):
        text += f"\n\nSuggested schedule rule: {data['suggested_rule']}"
    if data.get("expected_date"):
        text += f"\n\nExpected date: {data['expected_date']}"
    text += "\n\nHelp me with this."
    return text, outside


__all__ = [
    "PUSH_BODIES",
    "create_notification",
    "ensure_vapid_keys",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "public_push_key",
    "push_body",
    "seed_chat_text",
    "send_push_for_kind",
    "subscribe",
    "unsubscribe",
]
