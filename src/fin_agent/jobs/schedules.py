"""The recurring half of the scheduler: `job_schedules` -> one `jobs` row per period.

Rules are the same iCalendar RRULEs the ledger uses, anchored in India time. A due schedule is
enqueued with a key for its kind and period, then moves on from *now*: a laptop that slept through
six Sundays gets one briefing, and a restart can't send the same one twice. The unique
`(kind, payload)` index keeps the built-in rows and the user's scheduled questions in sync.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from fin_agent.jobs.runner import enqueue_job
from fin_agent.ledger.common import today as ledger_today

logger = logging.getLogger("fin_agent.jobs.schedules")

INDIA = ZoneInfo("Asia/Kolkata")
BUILTIN_KINDS = (
    "weekly_briefing",
    "reminders",
    "risk_check",
    "pattern_detection",
    "recompute_baselines",
    "backup_check",
)
WEEKDAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")
NIGHTLY = {
    "risk_check": time(2, 0),
    "pattern_detection": time(2, 15),
    "recompute_baselines": time(2, 30),
    "backup_check": time(3, 0),
}


def _rule_text(rule: str) -> str:
    text = rule.strip()
    return text if text.upper().startswith("RRULE:") else f"RRULE:{text}"


def _settings(conn: psycopg.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    return dict(row)


def _time_value(value: Any, fallback: time) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    if value is None:
        return fallback
    try:
        return time.fromisoformat(str(value)).replace(second=0, microsecond=0, tzinfo=None)
    except ValueError:
        return fallback


def builtin_rules(settings: dict[str, Any]) -> dict[str, str]:
    """The rules the instance should always have. Times are India time."""
    notification = _time_value(settings.get("notification_time"), time(8, 0))
    briefing_day = int(settings.get("weekly_briefing_dow") or 0)
    rules = {
        "weekly_briefing": (
            f"FREQ=WEEKLY;BYDAY={WEEKDAYS[briefing_day]};"
            f"BYHOUR={notification.hour};BYMINUTE={notification.minute}"
        ),
        "reminders": (f"FREQ=DAILY;BYHOUR={notification.hour};BYMINUTE={notification.minute}"),
    }
    for kind, time_of_day in NIGHTLY.items():
        rules[kind] = f"FREQ=DAILY;BYHOUR={time_of_day.hour};BYMINUTE={time_of_day.minute}"
    return rules


def next_occurrence(
    rule: str, *, after: datetime, default_time: time | None = None
) -> datetime | None:
    """The first occurrence strictly after `after`, in India time."""
    from dateutil.rrule import rrulestr

    local_after = after.astimezone(INDIA)
    at = default_time or local_after.time().replace(second=0, microsecond=0)
    dtstart = datetime.combine(local_after.date(), at, tzinfo=INDIA)
    try:
        parsed = rrulestr(_rule_text(rule), dtstart=dtstart)
        found = parsed.after(local_after, inc=False)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid job RRULE: {exc}") from exc
    return found.astimezone(INDIA) if found is not None else None


def period_for(kind: str, occurrence: datetime, payload: dict[str, Any] | None = None) -> str:
    local = occurrence.astimezone(INDIA)
    if kind == "weekly_briefing":
        return local.strftime("%G-W%V")
    if kind == "scheduled_question":
        return local.strftime("%Y-%m-%dT%H:%M")
    return local.strftime("%Y-%m-%d")


def job_key(kind: str, period: str, payload: dict[str, Any] | None = None) -> str:
    if kind == "scheduled_question":
        question_id = (payload or {}).get("scheduled_question_id", "unknown")
        return f"scheduled_question:{question_id}:{period}"
    return f"{kind}:{period}"


def ensure_builtin_schedules(
    conn: psycopg.Connection, *, now: datetime | None = None
) -> dict[str, str]:
    """Insert missing built-ins and follow the notification-time setting when it changes."""
    now = now or datetime.now(INDIA)
    rules = builtin_rules(_settings(conn))
    changed: dict[str, str] = {}
    for kind in BUILTIN_KINDS:
        rule = rules[kind]
        row = conn.execute(
            "SELECT * FROM job_schedules WHERE kind = %s AND payload = '{}'::jsonb",
            (kind,),
        ).fetchone()
        if row is None:
            occurrence = next_occurrence(rule, after=now)
            if occurrence is None:  # pragma: no cover - every built-in rule recurs
                continue
            conn.execute(
                """
                INSERT INTO job_schedules (kind, payload, rule, next_run_at)
                VALUES (%s, '{}'::jsonb, %s, %s)
                ON CONFLICT (kind, payload) DO NOTHING
                """,
                (kind, rule, occurrence),
            )
            changed[kind] = rule
        elif row["rule"] != rule or not row["active"]:
            occurrence = next_occurrence(rule, after=now)
            if occurrence is None:  # pragma: no cover
                continue
            conn.execute(
                """
                UPDATE job_schedules
                SET rule = %s, next_run_at = %s, active = true, updated_at = now()
                WHERE id = %s
                """,
                (rule, occurrence, row["id"]),
            )
            changed[kind] = rule
    return changed


def sync_scheduled_questions(conn: psycopg.Connection, *, now: datetime | None = None) -> int:
    """Mirror the user's active scheduled questions into `job_schedules`."""
    now = now or datetime.now(INDIA)
    settings = _settings(conn)
    default_time = _time_value(settings.get("notification_time"), time(8, 0))
    questions = list(
        conn.execute(
            "SELECT * FROM scheduled_questions WHERE active ORDER BY created_at"
        ).fetchall()
    )
    active_ids: set[str] = set()
    changed = 0
    for question in questions:
        question_id = str(question["id"])
        active_ids.add(question_id)
        payload = {"scheduled_question_id": question_id}
        row = conn.execute(
            """
            SELECT * FROM job_schedules
            WHERE kind = 'scheduled_question' AND payload = %s
            """,
            (Jsonb(payload),),
        ).fetchone()
        try:
            occurrence = next_occurrence(question["rule"], after=now, default_time=default_time)
        except ValueError:
            logger.warning("scheduled question %s has an invalid rule", question_id)
            continue
        if row is None:
            if occurrence is None:
                continue
            conn.execute(
                """
                INSERT INTO job_schedules (kind, payload, rule, next_run_at)
                VALUES ('scheduled_question', %s, %s, %s)
                ON CONFLICT (kind, payload) DO NOTHING
                """,
                (Jsonb(payload), question["rule"], occurrence),
            )
            changed += 1
        elif row["rule"] != question["rule"] or not row["active"]:
            if occurrence is None:
                conn.execute(
                    "UPDATE job_schedules SET active = false, updated_at = now() WHERE id = %s",
                    (row["id"],),
                )
            else:
                conn.execute(
                    """
                    UPDATE job_schedules
                    SET rule = %s, next_run_at = %s, active = true, updated_at = now()
                    WHERE id = %s
                    """,
                    (question["rule"], occurrence, row["id"]),
                )
            changed += 1
    for row in conn.execute(
        "SELECT * FROM job_schedules WHERE kind = 'scheduled_question' AND active"
    ).fetchall():
        question_id = (row["payload"] or {}).get("scheduled_question_id")
        if question_id not in active_ids:
            conn.execute(
                "UPDATE job_schedules SET active = false, updated_at = now() WHERE id = %s",
                (row["id"],),
            )
            changed += 1
    return changed


def enqueue_due_schedules(conn: psycopg.Connection, *, now: datetime | None = None) -> int:
    """Turn every due schedule into at most one job per period, then move it from now."""
    now = now or datetime.now(INDIA)
    enqueued = 0
    with conn.transaction():
        rows = list(
            conn.execute(
                """
                SELECT * FROM job_schedules
                WHERE active AND next_run_at <= %s
                ORDER BY next_run_at
                FOR UPDATE SKIP LOCKED
                """,
                (now,),
            ).fetchall()
        )
        for row in rows:
            kind = str(row["kind"])
            payload = dict(row["payload"] or {})
            occurrence = row["next_run_at"].astimezone(INDIA)
            period = period_for(kind, occurrence, payload)
            job_payload = {
                **payload,
                "period": period,
                "scheduled_for": occurrence.isoformat(),
            }
            enqueue_job(
                conn,
                key=job_key(kind, period, payload),
                kind=kind,
                payload=job_payload,
                run_at=now,
            )
            following = next_occurrence(
                row["rule"],
                after=now,
                default_time=occurrence.time().replace(tzinfo=None),
            )
            conn.execute(
                """
                UPDATE job_schedules
                SET last_run_at = %s, next_run_at = %s, active = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    row["next_run_at"],
                    following or occurrence,
                    following is not None,
                    row["id"],
                ),
            )
            enqueued += 1
    return enqueued


def job_status(conn: psycopg.Connection, *, limit: int = 20) -> dict[str, Any]:
    schedules = list(
        conn.execute(
            """
            SELECT js.*, sq.prompt AS question_prompt
            FROM job_schedules js
            LEFT JOIN scheduled_questions sq
                ON sq.id = js.payload ->> 'scheduled_question_id'
            ORDER BY js.active DESC, js.next_run_at
            """
        ).fetchall()
    )
    recent = list(
        conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT %s", (limit,)
        ).fetchall()
    )
    counts = {
        str(row["status"]): int(row["n"])
        for row in conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
    }
    return {
        "schedules": [
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": row["payload"] or {},
                "label": row["question_prompt"] or row["kind"].replace("_", " ").title(),
                "rule": row["rule"],
                "next_run_at": row["next_run_at"].isoformat(),
                "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
                "active": row["active"],
            }
            for row in schedules
        ],
        "recent": [
            {
                "id": row["id"],
                "key": row["key"],
                "kind": row["kind"],
                "status": row["status"],
                "attempts": row["attempts"],
                "run_at": row["run_at"].isoformat(),
                "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
                "last_error": row["last_error"],
            }
            for row in recent
        ],
        "counts": counts,
        "as_of": ledger_today().isoformat(),
    }


__all__ = [
    "BUILTIN_KINDS",
    "NIGHTLY",
    "builtin_rules",
    "enqueue_due_schedules",
    "ensure_builtin_schedules",
    "job_key",
    "job_status",
    "next_occurrence",
    "period_for",
    "sync_scheduled_questions",
]
