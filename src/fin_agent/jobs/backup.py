"""The backup-staleness alert: once per outage, 48 hours after the last success."""

from __future__ import annotations

import logging
from contextlib import closing
from datetime import datetime
from typing import Any

from fin_agent.db.conn import connect
from fin_agent.ledger import settings as settings_mod
from fin_agent.notifications import create_notification, send_push_for_kind

logger = logging.getLogger("fin_agent.jobs.backup")


def backup_alert(conn, *, now: datetime | None = None) -> dict[str, Any] | None:
    status = settings_mod.backup_status(conn, now=now)
    if not status["overdue"]:
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
    return create_notification(
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


def handle_backup_check(payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    with closing(connect()) as conn:
        with conn.transaction():
            row = backup_alert(conn)
        if row is not None:
            send_push_for_kind(conn, "backup_stale")
            conn.commit()
        return {"alert_id": row["id"] if row else None}


__all__ = ["backup_alert", "handle_backup_check"]
