"""Instance settings and the backup status the Settings panel shows."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from fin_agent.ledger.audit import write_group
from fin_agent.ledger.errors import InvalidLedgerError

BACKUP_DUE_AFTER = timedelta(hours=24)
BACKUP_ALERT_AFTER = timedelta(hours=48)


def read_settings(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    if row is None:  # pragma: no cover - migration 0003 inserts it
        raise InvalidLedgerError("The settings row is missing")
    user = conn.execute("SELECT id, display_name FROM users ORDER BY created_at LIMIT 1").fetchone()
    return {
        "base_currency": row["base_currency"],
        "time_zone": row["time_zone"],
        "floor_paise": row["floor_paise"],
        "default_horizon_days": row["default_horizon_days"],
        "notification_time": row["notification_time"].isoformat()
        if hasattr(row["notification_time"], "isoformat")
        else str(row["notification_time"]),
        "weekly_briefing_dow": row["weekly_briefing_dow"],
        "profile": {
            "user_id": user["id"] if user else None,
            "display_name": user["display_name"] if user else None,
        },
        "last_model_error_type": row["last_model_error_type"],
        "last_model_error_at": row["last_model_error_at"].isoformat()
        if row["last_model_error_at"]
        else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def update_settings(
    conn: psycopg.Connection,
    *,
    floor_paise: int | None = None,
    base_currency: str | None = None,
    time_zone: str | None = None,
    default_horizon_days: int | None = None,
    notification_time: str | None = None,
    weekly_briefing_dow: int | None = None,
    display_name: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if floor_paise is not None and floor_paise < 0:
        raise InvalidLedgerError("The floor cannot be negative")
    if default_horizon_days is not None and not 1 <= default_horizon_days <= 3650:
        raise InvalidLedgerError("The default horizon is 1 to 3650 days")
    if weekly_briefing_dow is not None and not 0 <= weekly_briefing_dow <= 6:
        raise InvalidLedgerError("The briefing day is 0 (Sunday) to 6 (Saturday)")
    if base_currency is not None and (len(base_currency) != 3 or not base_currency.isalpha()):
        raise InvalidLedgerError("The currency is a three-letter code")
    if display_name is not None and not display_name.strip():
        raise InvalidLedgerError("The display name cannot be blank")
    if time_zone is not None:
        try:
            ZoneInfo(time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise InvalidLedgerError(f"Unknown time zone: {time_zone}") from exc
    if notification_time is not None:
        try:
            notification_time = time.fromisoformat(notification_time).isoformat()
        except ValueError as exc:
            raise InvalidLedgerError("The notification time is HH:MM") from exc

    before = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    user_before = conn.execute("SELECT * FROM users ORDER BY created_at LIMIT 1").fetchone()
    updates: dict[str, Any] = {}
    for key, value in {
        "floor_paise": floor_paise,
        "base_currency": base_currency.upper() if base_currency else None,
        "time_zone": time_zone,
        "default_horizon_days": default_horizon_days,
        "notification_time": notification_time,
        "weekly_briefing_dow": weekly_briefing_dow,
    }.items():
        if value is not None:
            updates[key] = value

    if not updates and display_name is None:
        return read_settings(conn) | {"undo_id": None}

    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = before
        if updates:
            columns = list(updates)
            after = conn.execute(
                f"""
                UPDATE settings SET {", ".join(f"{column} = %s" for column in columns)},
                    updated_at = now()
                WHERE id = 1 RETURNING *
                """,
                tuple(updates[column] for column in columns),
            ).fetchone()
            group.record_updated("settings", "1", before, after)
        if display_name is not None:
            if user_before is None:
                raise InvalidLedgerError("There is no user profile yet")
            user_after = conn.execute(
                "UPDATE users SET display_name = %s WHERE id = %s RETURNING *",
                (display_name.strip(), user_before["id"]),
            ).fetchone()
            group.record_updated("users", user_before["id"], user_before, user_after)
    return read_settings(conn) | {"undo_id": group.group_id}


def backup_status(conn: psycopg.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    latest = conn.execute(
        "SELECT * FROM backup_runs ORDER BY created_at DESC, id DESC LIMIT 1"
    ).fetchone()
    success = conn.execute(
        """
        SELECT * FROM backup_runs WHERE status = 'success'
        ORDER BY finished_at DESC, id DESC LIMIT 1
        """
    ).fetchone()
    last_success_at = success["finished_at"] if success else None
    age_seconds = (
        int((now - last_success_at).total_seconds()) if last_success_at is not None else None
    )
    due_at = last_success_at + BACKUP_DUE_AFTER if last_success_at is not None else None
    alert_at = last_success_at + BACKUP_ALERT_AFTER if last_success_at is not None else None
    return {
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "last_success_age_seconds": age_seconds,
        "last_attempt": (
            {
                "id": latest["id"],
                "status": latest["status"],
                "started_at": latest["started_at"].isoformat(),
                "finished_at": latest["finished_at"].isoformat() if latest["finished_at"] else None,
                "dump_bytes": latest["dump_bytes"],
                "duration_ms": latest["duration_ms"],
                "restic_snapshot": latest["restic_snapshot"],
                "error": latest["error"],
            }
            if latest
            else None
        ),
        "due_at": due_at.isoformat() if due_at else None,
        "due": last_success_at is None or now >= due_at,
        "alert_at": alert_at.isoformat() if alert_at else None,
        "overdue": last_success_at is None or now >= alert_at,
        "checked_at": now.isoformat(),
    }
