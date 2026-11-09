"""The recurring half of the scheduler: `job_schedules` -> one `jobs` row per period.

Rules are the same iCalendar RRULEs the ledger uses, anchored in India time. A due schedule is
enqueued with a key for its kind and period, then moves on from *now*: a laptop that slept through
six Sundays gets one briefing, and a restart can't send the same one twice. The unique
`(kind, payload)` index keeps the built-in rows and the user's scheduled questions in sync.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fin_agent.db.conn import Connection
from fin_agent.jobs.runner import MAX_ATTEMPTS, enqueue_job
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
    "recaps",
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


async def _settings(conn: Connection) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM settings WHERE id = 1")
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
        # Daily, so a machine that slept through a Monday or the 1st still announces the recap.
        "recaps": (f"FREQ=DAILY;BYHOUR={notification.hour};BYMINUTE={notification.minute}"),
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


def _following_occurrence(
    rule: str, occurrence: datetime, now: datetime, default_time: time
) -> datetime | None:
    """Advance from the last scheduled occurrence, not from now.

    Anchoring an INTERVAL=2 rule at today moves its phase (a late biweekly run would fire a week
    early), so walk the rule's own occurrences until one is in the future.
    """
    following = next_occurrence(rule, after=occurrence, default_time=default_time)
    steps = 0
    while following is not None and following <= now:
        following = next_occurrence(rule, after=following, default_time=default_time)
        steps += 1
        if steps >= 10_000:  # pragma: no cover - only a pathological rule gets here
            return next_occurrence(rule, after=now, default_time=default_time)
    return following


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


async def ensure_builtin_schedules(
    conn: Connection, *, now: datetime | None = None
) -> dict[str, str]:
    """Insert missing built-ins and follow the notification-time setting when it changes."""
    now = now or datetime.now(INDIA)
    rules = builtin_rules(await _settings(conn))
    changed: dict[str, str] = {}
    for kind in BUILTIN_KINDS:
        rule = rules[kind]
        row = await conn.fetchrow(
            "SELECT * FROM job_schedules WHERE kind = $1 AND payload = '{}'::jsonb",
            kind,
        )
        if row is None:
            occurrence = next_occurrence(rule, after=now)
            if occurrence is None:  # pragma: no cover - every built-in rule recurs
                continue
            await conn.execute(
                """
                INSERT INTO job_schedules (kind, payload, rule, next_run_at)
                VALUES ($1, '{}'::jsonb, $2, $3)
                ON CONFLICT (kind, payload) DO NOTHING
                """,
                kind,
                rule,
                occurrence,
            )
            changed[kind] = rule
        elif row["rule"] != rule or not row["active"]:
            occurrence = next_occurrence(rule, after=now)
            if occurrence is None:  # pragma: no cover
                continue
            await conn.execute(
                """
                UPDATE job_schedules
                SET rule = $1, next_run_at = $2, active = true, updated_at = now()
                WHERE id = $3
                """,
                rule,
                occurrence,
                row["id"],
            )
            changed[kind] = rule
    return changed


async def sync_scheduled_questions(conn: Connection, *, now: datetime | None = None) -> int:
    """Mirror the user's active scheduled questions into `job_schedules`."""
    now = now or datetime.now(INDIA)
    settings = await _settings(conn)
    default_time = _time_value(settings.get("notification_time"), time(8, 0))
    questions = await conn.fetch(
        "SELECT * FROM scheduled_questions WHERE active ORDER BY created_at"
    )
    active_ids: set[str] = set()
    changed = 0
    for question in questions:
        question_id = str(question["id"])
        active_ids.add(question_id)
        payload = {"scheduled_question_id": question_id}
        row = await conn.fetchrow(
            """
            SELECT * FROM job_schedules
            WHERE kind = 'scheduled_question' AND payload = $1
            """,
            payload,
        )
        try:
            occurrence = next_occurrence(question["rule"], after=now, default_time=default_time)
        except ValueError:
            logger.warning("scheduled question %s has an invalid rule", question_id)
            continue
        if row is None:
            if occurrence is None:
                continue
            await conn.execute(
                """
                INSERT INTO job_schedules (kind, payload, rule, next_run_at)
                VALUES ('scheduled_question', $1, $2, $3)
                ON CONFLICT (kind, payload) DO NOTHING
                """,
                payload,
                question["rule"],
                occurrence,
            )
            changed += 1
        elif row["rule"] != question["rule"] or not row["active"]:
            if occurrence is None:
                await conn.execute(
                    "UPDATE job_schedules SET active = false, updated_at = now() WHERE id = $1",
                    row["id"],
                )
            else:
                await conn.execute(
                    """
                    UPDATE job_schedules
                    SET rule = $1, next_run_at = $2, active = true, updated_at = now()
                    WHERE id = $3
                    """,
                    question["rule"],
                    occurrence,
                    row["id"],
                )
            changed += 1
    for row in await conn.fetch(
        "SELECT * FROM job_schedules WHERE kind = 'scheduled_question' AND active"
    ):
        question_id = (row["payload"] or {}).get("scheduled_question_id")
        if question_id not in active_ids:
            await conn.execute(
                "UPDATE job_schedules SET active = false, updated_at = now() WHERE id = $1",
                row["id"],
            )
            changed += 1
    return changed


async def enqueue_due_schedules(conn: Connection, *, now: datetime | None = None) -> int:
    """Turn every due schedule into at most one job per period, then move past now."""
    now = now or datetime.now(INDIA)
    enqueued = 0
    async with conn.transaction():
        rows = await conn.fetch(
            """
            SELECT * FROM job_schedules
            WHERE active AND next_run_at <= $1
            ORDER BY next_run_at
            FOR UPDATE SKIP LOCKED
            """,
            now,
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
            await enqueue_job(
                conn,
                key=job_key(kind, period, payload),
                kind=kind,
                payload=job_payload,
                run_at=now,
            )
            following = _following_occurrence(
                row["rule"],
                occurrence,
                now,
                occurrence.time().replace(tzinfo=None),
            )
            await conn.execute(
                """
                UPDATE job_schedules
                SET last_run_at = $1, next_run_at = $2, active = $3, updated_at = now()
                WHERE id = $4
                """,
                row["next_run_at"],
                following or occurrence,
                following is not None,
                row["id"],
            )
            enqueued += 1
    return enqueued


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _period_text(start: date | None, end: date | None) -> str | None:
    if end is None:
        return start.strftime("%b %Y") if start is not None else None
    if start is None or (start.year, start.month) == (end.year, end.month):
        return end.strftime("%b %Y")
    if start.year == end.year:
        return f"{start.strftime('%b')}–{end.strftime('%b %Y')}"
    return f"{start.strftime('%b %Y')}–{end.strftime('%b %Y')}"


def _import_subject(row: Mapping[str, Any]) -> str:
    institution = row["institution"] or "Statement"
    period = _period_text(row["period_start"], row["period_end"])
    return f"{institution} statement, {period}" if period else institution


async def _subjects(
    conn: Connection, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    """Batch-load the import and question rows the subjects are built from."""
    import_ids: set[str] = set()
    question_ids: set[str] = set()
    for row in rows:
        payload = row["payload"] or {}
        if row["kind"] == "scheduled_question":
            if payload.get("scheduled_question_id"):
                question_ids.add(str(payload["scheduled_question_id"]))
        elif payload.get("import_id"):
            import_ids.add(str(payload["import_id"]))
    if not import_ids and not question_ids:
        return {}
    found = await conn.fetch(
        """
        SELECT 'import' AS source, id, institution, period_start, period_end, NULL AS prompt
        FROM imports WHERE id = ANY($1::text[])
        UNION ALL
        SELECT 'question', id, NULL, NULL, NULL, prompt
        FROM scheduled_questions WHERE id = ANY($2::text[])
        """,
        list(import_ids),
        list(question_ids),
    )
    return {f"{row['source']}:{row['id']}": row for row in found}


def _subject(row: Mapping[str, Any], subjects: Mapping[str, Mapping[str, Any]]) -> str | None:
    payload = row["payload"] or {}
    if row["kind"] == "scheduled_question":
        question = subjects.get(f"question:{payload.get('scheduled_question_id')}")
        prompt = str(question["prompt"]) if question is not None else ""
        return prompt[:60] or None
    import_id = payload.get("import_id")
    if import_id:
        statement = subjects.get(f"import:{import_id}")
        if statement is not None:
            return _import_subject(statement)
    return None


def job_view(row: Mapping[str, Any], subjects: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """One job as the app shows it: what it did, where it is, and a human subject."""
    status = str(row["status"])
    attempts = int(row["attempts"] or 0)
    payload = row["payload"] or {}
    return {
        "id": row["id"],
        "key": row["key"],
        "kind": row["kind"],
        "status": status,
        "attempts": attempts,
        "attempt": attempts if status == "failed" else attempts + 1,
        "max_attempts": MAX_ATTEMPTS,
        "run_at": row["run_at"].isoformat(),
        "created_at": row["created_at"].isoformat(),
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
        "last_error": row["last_error"],
        "result": row["result"],
        "subject": _subject(row, subjects),
        "import_id": payload.get("import_id"),
    }


async def job_views(conn: Connection, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Serialize any number of jobs with one batch query for their subjects."""
    subjects = await _subjects(conn, rows)
    return [job_view(row, subjects) for row in rows]


async def job_status(conn: Connection, *, limit: int = 20) -> dict[str, Any]:
    schedules = await conn.fetch(
        """
        SELECT js.*, sq.prompt AS question_prompt
        FROM job_schedules js
        LEFT JOIN scheduled_questions sq
            ON sq.id = js.payload ->> 'scheduled_question_id'
        ORDER BY js.active DESC, js.next_run_at
        """
    )
    recent_rows = await conn.fetch(
        "SELECT * FROM jobs ORDER BY created_at DESC, id DESC LIMIT $1", limit
    )
    recent = await job_views(conn, recent_rows)
    counts = {
        str(row["status"]): int(row["n"])
        for row in await conn.fetch("SELECT status, count(*) AS n FROM jobs GROUP BY status")
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
        "recent": recent,
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
    "job_view",
    "job_views",
    "next_occurrence",
    "period_for",
    "sync_scheduled_questions",
]
