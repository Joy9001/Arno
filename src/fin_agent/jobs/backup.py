"""The backup-staleness alert: once per outage, 48 hours after the last success."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fin_agent.db.conn import Connection, connect
from fin_agent.ledger import settings as settings_mod
from fin_agent.ledger.settings import BACKUP_ALERT_AFTER
from fin_agent.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("fin_agent.jobs.backup")


async def _no_success_clock(conn: Connection) -> datetime | None:
    """The earliest signal that a backup was expected: the install, or its first attempt.

    With no success to age, "48 hours with no success" is measured from when there was first
    something to succeed at, so a fresh install does not alert before its sidecar has run.
    """
    first_attempt = await conn.fetchval("SELECT min(started_at) FROM backup_runs")
    installed = await conn.fetchval("SELECT created_at FROM settings WHERE id = 1")
    candidates = [value for value in (first_attempt, installed) if value is not None]
    return min(candidates) if candidates else None


async def backup_alert(conn: Connection, *, now: datetime | None = None) -> dict[str, Any] | None:
    status = await settings_mod.backup_status(conn, now=now)
    if not status["overdue"]:
        return None
    if status["last_success_at"] is None:
        clock = await _no_success_clock(conn)
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        if clock is None or current < clock + BACKUP_ALERT_AFTER:
            return None
    last = status["last_success_at"] or "never"
    key = f"backup_stale:{last}"
    if status["last_success_at"] is None:
        body = "No successful backup is recorded yet. The sidecar has not shipped a dump."
    else:
        hours = round((status["last_success_age_seconds"] or 0) / 3600)
        body = (
            f"The last successful backup finished {hours} hours ago, past the 48-hour alert "
            "line. The sidecar keeps trying whenever it is due."
        )
    return await create_notification(
        conn,
        kind="backup_stale",
        title="Backups have stopped",
        body=body,
        data={
            "last_success_at": status["last_success_at"],
            "last_success_age_seconds": status["last_success_age_seconds"],
            "due_at": status["due_at"],
            "alert_at": status["alert_at"],
        },
        dedupe_key=key,
    )


async def handle_backup_check(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    async with connect() as conn:
        row = await backup_alert(conn)
        if row is not None:
            await send_push_for_kind(conn, "backup_stale")
        return {"alert_id": row["id"] if row else None}


__all__ = ["backup_alert", "handle_backup_check"]
