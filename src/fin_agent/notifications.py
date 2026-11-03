"""The in-app inbox and contentless web push.

Every proactive item lands in `notifications`. Push is only a ping: the payload carries a generic
sentence and a URL, never a merchant, an amount, or a balance, because it passes through the
browser vendor's push service. The full item is read from the inbox after the app opens.

`dedupe_key` makes a handler safe to rerun after a crash: a reminder, a risk warning, a pattern
proposal, the weekly briefing, and a scheduled answer each key on their own period.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pywebpush import WebPushException, webpush

logger = logging.getLogger("fin_agent.notifications")

# Contentless pings: the browser vendor sees only this, and never a financial number.
PUSH_BODIES = {
    "weekly_briefing": "Your weekly briefing is ready",
    "reminder": "A payment reminder is ready",
    "risk_warning": "A projection warning is ready",
    "new_subscription": "A recurring charge is ready to review",
    "missed_recurring": "A missed recurring charge is ready to review",
    "scheduled_question": "Your scheduled question has an answer",
    "backup_stale": "Fin-Agent needs a backup check",
    "job_failed": "A background job needs attention",
}
DEFAULT_PUSH_BODY = "You have a new notification"
PUSH_TITLE = "Fin-Agent"


def _now() -> datetime:
    return datetime.now(UTC)


def ensure_vapid_keys(
    conn: psycopg.Connection, *, subject: str = "mailto:fin-agent@localhost"
) -> dict[str, Any]:
    """Generate the instance's VAPID key pair once, on first run."""
    row = conn.execute("SELECT * FROM vapid_keys WHERE id = 1").fetchone()
    if row is not None:
        return dict(row)

    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid02

    vault = Vapid02()
    vault.generate_keys()
    public_point = vault.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_key = base64.urlsafe_b64encode(public_point).decode("ascii").rstrip("=")
    private_key = vault.private_pem().decode("ascii")
    conn.execute(
        """
        INSERT INTO vapid_keys (id, public_key, private_key, subject)
        VALUES (1, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (public_key, private_key, subject),
    )
    row = conn.execute("SELECT * FROM vapid_keys WHERE id = 1").fetchone()
    return dict(row)


def public_push_key(conn: psycopg.Connection) -> dict[str, Any]:
    row = ensure_vapid_keys(conn)
    return {"public_key": row["public_key"], "subject": row["subject"]}


def subscribe(
    conn: psycopg.Connection,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
) -> dict[str, Any]:
    if not endpoint.startswith("https://"):
        raise ValueError("A push endpoint must be HTTPS")
    if not p256dh or not auth:
        raise ValueError("A push subscription needs p256dh and auth keys")
    row = conn.execute(
        """
        INSERT INTO push_subscriptions (endpoint, p256dh, auth)
        VALUES (%s, %s, %s)
        ON CONFLICT (endpoint) DO UPDATE
        SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth, last_used_at = now()
        RETURNING id, endpoint, created_at, last_used_at
        """,
        (endpoint, p256dh, auth),
    ).fetchone()
    return dict(row)


def unsubscribe(conn: psycopg.Connection, *, endpoint: str) -> bool:
    row = conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = %s RETURNING id", (endpoint,)
    ).fetchone()
    return row is not None


def create_notification(
    conn: psycopg.Connection,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any] | None:
    """Insert one inbox item. A duplicate key returns `None` instead of a second item."""
    if dedupe_key:
        row = conn.execute(
            """
            INSERT INTO notifications (kind, title, body, data, dedupe_key)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (dedupe_key) DO NOTHING
            RETURNING *
            """,
            (kind, title, body, Jsonb(data or {}), dedupe_key),
        ).fetchone()
    else:
        row = conn.execute(
            """
            INSERT INTO notifications (kind, title, body, data)
            VALUES (%s, %s, %s, %s)
            RETURNING *
            """,
            (kind, title, body, Jsonb(data or {})),
        ).fetchone()
    return dict(row) if row is not None else None


def list_notifications(conn: psycopg.Connection, *, limit: int = 50) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    rows = list(
        conn.execute(
            "SELECT * FROM notifications ORDER BY created_at DESC, id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    )
    unread = conn.execute(
        "SELECT count(*) AS n FROM notifications WHERE read_at IS NULL"
    ).fetchone()["n"]
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


def mark_read(conn: psycopg.Connection, notification_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        UPDATE notifications
        SET read_at = coalesce(read_at, now())
        WHERE id = %s
        RETURNING *
        """,
        (notification_id,),
    ).fetchone()
    return _payload(row) if row is not None else None


def mark_all_read(conn: psycopg.Connection) -> int:
    row = conn.execute(
        "UPDATE notifications SET read_at = now() WHERE read_at IS NULL RETURNING id"
    ).fetchall()
    return len(row)


def push_body(kind: str) -> str:
    return PUSH_BODIES.get(kind, DEFAULT_PUSH_BODY)


def _push(
    conn: psycopg.Connection,
    *,
    kind: str,
    url: str = "/",
) -> int:
    """Send one generic ping to every subscription, dropping endpoints that are gone."""
    keys = ensure_vapid_keys(conn)
    subscriptions = list(conn.execute("SELECT * FROM push_subscriptions").fetchall())
    if not subscriptions:
        return 0
    payload = json.dumps(
        {"title": PUSH_TITLE, "body": push_body(kind), "url": url},
        separators=(",", ":"),
    )
    sent = 0
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription["endpoint"],
                    "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
                },
                data=payload,
                vapid_private_key=keys["private_key"],
                vapid_claims={"sub": keys["subject"]},
                timeout=10,
            )
            conn.execute(
                "UPDATE push_subscriptions SET last_used_at = now() WHERE id = %s",
                (subscription["id"],),
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                conn.execute("DELETE FROM push_subscriptions WHERE id = %s", (subscription["id"],))
            else:
                logger.warning("push to %s failed (%s)", subscription["id"], type(exc).__name__)
        except Exception:  # noqa: BLE001 - a push failure must never fail the job or write
            logger.exception("push to %s failed", subscription["id"])
    return sent


def send_push_for_kind(conn: psycopg.Connection, kind: str, *, url: str = "/") -> int:
    """Public entry point for a handler after it commits its notification."""
    try:
        return _push(conn, kind=kind, url=url)
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
    merchant = data.get("merchant")
    outside = bool(data.get("outside_text"))
    if outside and merchant:
        from fin_agent.tools.mark import mark

        marked = mark(str(merchant)) or str(merchant)
        title = title.replace(str(merchant), marked)
        body = body.replace(str(merchant), marked)
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
