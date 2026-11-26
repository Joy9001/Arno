"""The user's scheduled questions: CRUD, validation, and the next run shown in Settings.

They run through the agent read-only (see `arno.jobs.questions`). The row holds the schedule and
its next fire time, which the minute loop advances when it claims the question; the answer lands
in the inbox.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from arno.db.conn import Connection
from arno.ledger.audit import write_group
from arno.ledger.errors import InvalidLedgerError, NotFoundLedgerError

INDIA = ZoneInfo("Asia/Kolkata")
BRIEFING_ID = "sq_weekly_briefing"
BRIEFING_PROMPT = (
    "Write my weekly briefing: what I spent this week against last week, what is due in the "
    "next week, and anything in my projection I should know about. Use only numbers from your "
    "tools."
)
WEEKDAYS = ("SU", "MO", "TU", "WE", "TH", "FR", "SA")


def next_occurrence(
    rule: str, *, after: datetime, created_at: datetime, at: time
) -> datetime | None:
    """The first run strictly after `after`, in India time.

    The rule starts on the day the question was created, at `at` (the notification time), so
    `COUNT` and `INTERVAL` count from a fixed first run.
    """
    text = rule.strip()
    text = text if text.upper().startswith("RRULE:") else f"RRULE:{text}"
    start = datetime.combine(created_at.astimezone(INDIA).date(), at, tzinfo=INDIA)
    try:
        return rrulestr(text, dtstart=start).after(after.astimezone(INDIA), inc=False)
    except (ValueError, TypeError) as exc:
        raise InvalidLedgerError(f"Invalid job RRULE: {exc}") from exc


async def notification_time(conn: Connection) -> time:
    row = await conn.fetchrow("SELECT notification_time FROM settings WHERE id = 1")
    value = row["notification_time"] if row else time(8, 0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    return time.fromisoformat(str(value)).replace(second=0, microsecond=0, tzinfo=None)


async def _first_run(conn: Connection, rule: str, created_at: datetime) -> datetime:
    following = next_occurrence(
        rule,
        after=datetime.now(INDIA),
        created_at=created_at,
        at=await notification_time(conn),
    )
    if following is None:
        raise InvalidLedgerError("That rule never runs again")
    return following


async def ensure_default_question(conn: Connection) -> None:
    """Seed the weekly briefing once. A later edit or archive is never undone."""
    row = await conn.fetchrow_required("SELECT weekly_briefing_dow FROM settings WHERE id = 1")
    at = await notification_time(conn)
    rule = (
        f"FREQ=WEEKLY;BYDAY={WEEKDAYS[int(row['weekly_briefing_dow'] or 0)]};"
        f"BYHOUR={at.hour};BYMINUTE={at.minute}"
    )
    await conn.execute(
        """
        INSERT INTO scheduled_questions (id, prompt, rule, next_run_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO NOTHING
        """,
        BRIEFING_ID,
        BRIEFING_PROMPT,
        rule,
        await _first_run(conn, rule, datetime.now(INDIA)),
    )


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "rule": row["rule"],
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "next_run_at": (
            row["next_run_at"].isoformat() if row["next_run_at"] and row["active"] else None
        ),
        "active": row["active"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def list_questions(
    conn: Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE sq.active"
    rows = await conn.fetch(
        f"""
        SELECT sq.* FROM scheduled_questions sq
        {where}
        ORDER BY sq.active DESC, sq.created_at
        """  # noqa: S608
    )
    return [_payload(row) for row in rows]


async def get_question(conn: Connection, question_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM scheduled_questions WHERE id = $1", question_id)
    if row is None:
        raise NotFoundLedgerError(f"No scheduled question {question_id}")
    return _payload(row)


async def create_question(
    conn: Connection,
    *,
    prompt: str,
    rule: str,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    text = prompt.strip()
    if not text:
        raise InvalidLedgerError("A scheduled question needs a prompt")
    first_run = await _first_run(conn, rule, datetime.now(INDIA))
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow_required(
            """
            INSERT INTO scheduled_questions (prompt, rule, next_run_at)
            VALUES ($1, $2, $3) RETURNING *
            """,
            text,
            rule.strip(),
            first_run,
        )
        group.record_created("scheduled_questions", row)
    return (await get_question(conn, row["id"])) | {"undo_id": group.group_id}


async def update_question(
    conn: Connection,
    question_id: str,
    *,
    prompt: str | None = None,
    rule: str | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = await conn.fetchrow("SELECT * FROM scheduled_questions WHERE id = $1", question_id)
    if before is None:
        raise NotFoundLedgerError(f"No scheduled question {question_id}")
    updates: dict[str, Any] = {}
    if prompt is not None:
        if not prompt.strip():
            raise InvalidLedgerError("A scheduled question needs a prompt")
        updates["prompt"] = prompt.strip()
    if rule is not None:
        updates["rule"] = rule.strip()
    if active is not None:
        updates["active"] = active
    if (rule is not None or active) and updates.get("active", before["active"]):
        updates["next_run_at"] = await _first_run(
            conn, updates.get("rule", before["rule"]), before["created_at"]
        )
    if not updates:
        return (await get_question(conn, question_id)) | {"undo_id": None}
    columns = list(updates)
    assignments = ", ".join(f"{column} = ${index}" for index, column in enumerate(columns, start=1))
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        after = await conn.fetchrow_required(
            f"""
            UPDATE scheduled_questions
            SET {assignments}, updated_at = now()
            WHERE id = ${len(columns) + 1} RETURNING *
            """,  # noqa: S608
            *[updates[column] for column in columns],
            question_id,
        )
        group.record_updated("scheduled_questions", question_id, before, after)
    return (await get_question(conn, question_id)) | {"undo_id": group.group_id}


async def archive_question(
    conn: Connection,
    question_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_question(
        conn, question_id, active=False, actor=actor, message_id=message_id
    )


__all__ = [
    "BRIEFING_ID",
    "INDIA",
    "archive_question",
    "create_question",
    "ensure_default_question",
    "get_question",
    "list_questions",
    "next_occurrence",
    "notification_time",
    "update_question",
]
