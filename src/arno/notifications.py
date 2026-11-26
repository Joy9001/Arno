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
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from pywebpush import WebPushException, webpush

from arno.db.conn import Connection

if TYPE_CHECKING:
    from arno.ledger.audit import WriteGroup

logger = logging.getLogger("arno.notifications")

# Contentless pings: the browser vendor sees only this, and never a financial number.
PUSH_BODIES = {
    "weekly_briefing": "Your weekly briefing is ready",
    "reminder": "A payment reminder is ready",
    "risk_warning": "A projection warning is ready",
    "new_subscription": "A recurring charge is ready to review",
    "new_income": "A recurring income stream is ready to review",
    "missed_recurring": "A missed recurring charge is ready to review",
    "price_change": "A recurring charge changed price",
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


def payment_alert_keys(schedule_id: str, due_date: date) -> tuple[str, str]:
    """Dedupe keys for open payment alerts (`reminder` and `missed_recurring`) on an occurrence.

    A card bill is the third kind `send_reminders` announces; it has no schedule, so the job keys
    it by the card's account id and only the reminder key exists. `sync_card_due_alerts_in_group`
    matches those rows.
    """
    iso = due_date.isoformat()
    return f"reminder:{schedule_id}:{iso}", f"pattern:missed:{schedule_id}:{iso}"


async def sync_payment_alerts_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    schedule_id: str,
    due_date: date,
    movement_id: str | None = None,
) -> int:
    """Make the schedule occurrence's payment alerts match its live evidence, audited in `group`.

    A reminder stays open until the occurrence is covered; a missed-payment warning only says no
    payment was found, so any effective payment resolves it. Unlinking or reversing a payment
    reopens, or rebinds to whatever payment still covers it, so undo restores the prior warning.
    """
    from arno.ledger import audit

    occ = await conn.fetchrow(
        """
        SELECT o.amount_paise,
               coalesce(
                   sum(sp.amount_paise) FILTER (WHERE ms.is_effective), 0
               )::bigint AS paid_paise,
               coalesce(
                   array_agg(sp.movement_id ORDER BY sp.created_at, sp.id)
                   FILTER (WHERE ms.is_effective),
                   ARRAY[]::text[]
               ) AS effective_movement_ids
        FROM schedule_occurrences o
        LEFT JOIN schedule_payments sp ON sp.occurrence_id = o.id
        LEFT JOIN v_movement_state ms ON ms.movement_id = sp.movement_id
        WHERE o.schedule_id = $1 AND o.due_date = $2
        GROUP BY o.id, o.amount_paise
        """,
        schedule_id,
        due_date,
    )
    paid_paise = int(occ["paid_paise"]) if occ else 0
    expected = occ["amount_paise"] if occ else None
    covered = paid_paise > 0 if expected is None else paid_paise >= int(expected)
    effective = [str(item) for item in (occ["effective_movement_ids"] or [])] if occ else []
    resolver = movement_id if movement_id in effective else (effective[0] if effective else None)

    reminder, missed = payment_alert_keys(schedule_id, due_date)
    rows = await conn.fetch(
        """
        SELECT id, dedupe_key, resolved_at, resolved_by FROM notifications
        WHERE dedupe_key = ANY($1::text[])
        ORDER BY id
        FOR UPDATE
        """,
        [reminder, missed],
    )
    changed = 0
    for row in rows:
        key = row["dedupe_key"]
        resolved = (key == reminder and covered) or (key == missed and paid_paise > 0)
        if resolved:
            if row["resolved_at"] is None:
                values: dict[str, Any] = {"resolved_at": _now(), "resolved_by": resolver}
            elif row["resolved_by"] not in effective:
                values = {"resolved_by": resolver}
            else:
                continue
        elif row["resolved_at"] is not None:
            values = {"resolved_at": None, "resolved_by": None}
        else:
            continue
        updated = await audit._update_row(conn, group, "notifications", str(row["id"]), values)
        if updated is not None:
            changed += 1
    return changed


async def sync_card_due_alerts_in_group(
    conn: Connection,
    group: WriteGroup,
    *,
    account_id: str,
    movement_id: str,
) -> int:
    """Make a card's bill reminders match what it still owes, audited in `group`.

    A card bill has no schedule: the reminder job keys it by the card's account id. Money into the
    card that leaves nothing owed resolves the bill reminders; a reversal reopens them.
    """
    from arno.ledger import audit
    from arno.ledger.common import has_known_balance
    from arno.ledger.schedules import card_amount_due

    card = await conn.fetchrow(
        """
        SELECT kind, balance_paise, statement_balance_paise, statement_balance_as_of
        FROM v_accounts WHERE id = $1
        """,
        account_id,
    )
    if card is None or card["kind"] != "card":
        return 0
    # An unanchored balance cannot say the card is settled: the pre-ledger debt is unknown.
    covered = has_known_balance(card) and card_amount_due(card) == 0
    # A reversal reopens only the bills the reversed payment settled, not older ones.
    chain = [
        str(row["id"])
        for row in await conn.fetch(
            """
            WITH RECURSIVE chain AS (
                SELECT id, reverses_id FROM movements WHERE id = $1
                UNION ALL
                SELECT m.id, m.reverses_id FROM movements m JOIN chain c ON m.id = c.reverses_id
            )
            SELECT id FROM chain
            """,
            movement_id,
        )
    ]
    rows = await conn.fetch(
        """
        SELECT id, resolved_at, resolved_by FROM notifications
        WHERE kind = 'reminder' AND data->>'kind' = 'card_due' AND data->>'source_id' = $1
        ORDER BY id
        FOR UPDATE
        """,
        account_id,
    )
    changed = 0
    for row in rows:
        if covered:
            if row["resolved_at"] is not None:
                continue
            values: dict[str, Any] = {"resolved_at": _now(), "resolved_by": movement_id}
        elif str(row["resolved_by"] or "") in chain:
            values = {"resolved_at": None, "resolved_by": None}
        else:
            continue
        updated = await audit._update_row(conn, group, "notifications", str(row["id"]), values)
        if updated is not None:
            changed += 1
    return changed


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
    await conn.execute(
        """
        UPDATE settings
        SET push_opted_out_at = NULL,
            push_last_error = NULL,
            push_last_error_at = NULL,
            updated_at = now()
        WHERE id = 1
        """
    )
    return row


async def unsubscribe(conn: Connection, *, endpoint: str) -> bool:
    row = await conn.fetchrow(
        "DELETE FROM push_subscriptions WHERE endpoint = $1 RETURNING id", endpoint
    )
    if row is None:
        return False
    remaining = int(await conn.fetchval("SELECT count(*) FROM push_subscriptions") or 0)
    if remaining == 0:
        await conn.execute(
            """
            UPDATE settings
            SET push_opted_out_at = coalesce(push_opted_out_at, now()),
                push_last_error = NULL,
                push_last_error_at = NULL,
                updated_at = now()
            WHERE id = 1
            """
        )
    return True


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
        "SELECT * FROM notifications WHERE resolved_at IS NULL "
        "ORDER BY created_at DESC, id DESC LIMIT $1",
        limit,
    )
    unread = (
        await conn.fetchrow_required(
            "SELECT count(*) AS n FROM notifications WHERE read_at IS NULL AND resolved_at IS NULL"
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
        "resolved_at": row["resolved_at"].isoformat() if row.get("resolved_at") else None,
        "resolved_by": row.get("resolved_by"),
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
    status = await conn.execute(
        "UPDATE notifications SET read_at = now() WHERE read_at IS NULL AND resolved_at IS NULL"
    )
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
    batch_errors: list[str] = []
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
                batch_errors.append(f"permission_revoked: HTTP {exc.status_code}")
            else:
                code_part = f" (HTTP {exc.status_code})" if exc.status_code is not None else ""
                batch_errors.append(f"delivery_failed: {type(exc).__name__}{code_part}")
                logger.warning("push to %s failed (%s)", subscription["id"], type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 - a push failure must never fail the job or write
            batch_errors.append(f"delivery_failed: {type(exc).__name__}")
            logger.exception("push to %s failed", subscription["id"])
    if batch_errors:
        await conn.execute(
            """
            UPDATE settings
            SET push_last_error = $1,
                push_last_error_at = now(),
                push_last_sent_at = CASE WHEN $2 THEN now() ELSE push_last_sent_at END,
                updated_at = now()
            WHERE id = 1
            """,
            batch_errors[-1][:200],
            sent > 0,
        )
    elif sent > 0:
        await conn.execute(
            """
            UPDATE settings
            SET push_last_error = NULL,
                push_last_error_at = NULL,
                push_last_sent_at = now(),
                push_opted_out_at = NULL,
                updated_at = now()
            WHERE id = 1
            """
        )
    return sent


async def push_status(conn: Connection) -> dict[str, Any]:
    """Visible push subscription, opt-out, and delivery-failure state for UI recovery."""
    subscription_count = int(await conn.fetchval("SELECT count(*) FROM push_subscriptions") or 0)
    settings_row = await conn.fetchrow(
        """
        SELECT push_last_error, push_last_error_at, push_last_sent_at, push_opted_out_at
        FROM settings WHERE id = 1
        """
    )
    last_error = settings_row["push_last_error"] if settings_row else None
    last_error_at = (
        settings_row["push_last_error_at"].isoformat()
        if settings_row and settings_row["push_last_error_at"]
        else None
    )
    last_sent_at = (
        settings_row["push_last_sent_at"].isoformat()
        if settings_row and settings_row["push_last_sent_at"]
        else None
    )
    opted_out_at = (
        settings_row["push_opted_out_at"].isoformat()
        if settings_row and settings_row["push_opted_out_at"]
        else None
    )
    subscribed = subscription_count > 0
    opted_out = not subscribed and opted_out_at is not None
    permission_revoked = bool(last_error and last_error.startswith("permission_revoked"))
    if subscribed:
        status = "degraded" if last_error else "active"
    elif permission_revoked:
        status = "permission_revoked"
    elif opted_out:
        status = "opted_out"
    else:
        status = "unsubscribed"
    return {
        "status": status,
        "subscribed": subscribed,
        "subscription_count": subscription_count,
        "opted_out": opted_out,
        "opted_out_at": opted_out_at,
        "permission_revoked": permission_revoked,
        "last_sent_at": last_sent_at,
        "last_error": last_error,
        "last_error_at": last_error_at,
        "inbox_fallback_active": True,
    }


async def send_push_for_kind(conn: Connection, kind: str, *, url: str = "/") -> int:
    """Public entry point for a handler after it commits its notification."""
    try:
        return await _push(conn, kind=kind, url=url)
    except Exception:  # noqa: BLE001 - push is best effort
        logger.exception("push delivery for %s failed", kind)
        return 0


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
    "PUSH_BODIES",
    "create_notification",
    "ensure_vapid_keys",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "payment_alert_keys",
    "public_push_key",
    "push_body",
    "push_status",
    "seed_chat_text",
    "send_push_for_kind",
    "subscribe",
    "sync_card_due_alerts_in_group",
    "sync_payment_alerts_in_group",
    "unsubscribe",
]
