"""The user's scheduled questions: CRUD, validation, and the next run shown in Settings.

They run through the agent read-only (see `arno.jobs.questions`). The row here is only the
schedule; the answer lands in the inbox.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from arno.db.conn import Connection
from arno.jobs.schedules import INDIA, next_occurrence, sync_scheduled_questions
from arno.ledger.audit import write_group
from arno.ledger.errors import InvalidLedgerError, NotFoundLedgerError


async def _notification_time(conn: Connection) -> time:
    row = await conn.fetchrow("SELECT notification_time FROM settings WHERE id = 1")
    value = row["notification_time"] if row else time(8, 0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    return time.fromisoformat(str(value)).replace(second=0, microsecond=0, tzinfo=None)


async def _check_rule(conn: Connection, rule: str, now: datetime | None = None) -> None:
    now = now or datetime.now(INDIA)
    try:
        following = next_occurrence(rule, after=now, default_time=await _notification_time(conn))
    except ValueError as exc:
        raise InvalidLedgerError(str(exc)) from exc
    if following is None:
        raise InvalidLedgerError("That rule never runs again")


def _payload(row: dict[str, Any], next_run_at: Any = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "rule": row["rule"],
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "next_run_at": (next_run_at.isoformat() if next_run_at and row["active"] else None),
        "active": row["active"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


async def list_questions(
    conn: Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE sq.active"
    rows = await conn.fetch(
        f"""
        SELECT sq.*, js.next_run_at
        FROM scheduled_questions sq
        LEFT JOIN job_schedules js
            ON js.scheduled_question_id = sq.id
        {where}
        ORDER BY sq.active DESC, sq.created_at
        """  # noqa: S608
    )
    return [_payload(row, row.get("next_run_at")) for row in rows]


async def get_question(conn: Connection, question_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT sq.*, js.next_run_at
        FROM scheduled_questions sq
        LEFT JOIN job_schedules js
            ON js.scheduled_question_id = sq.id
        WHERE sq.id = $1
        """,
        question_id,
    )
    if row is None:
        raise NotFoundLedgerError(f"No scheduled question {question_id}")
    return _payload(row, row.get("next_run_at"))


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
    await _check_rule(conn, rule)
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow_required(
            """
            INSERT INTO scheduled_questions (prompt, rule)
            VALUES ($1, $2) RETURNING *
            """,
            text,
            rule.strip(),
        )
        group.record_created("scheduled_questions", row)
    await sync_scheduled_questions(conn)
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
        await _check_rule(conn, rule)
        updates["rule"] = rule.strip()
    if active is not None:
        updates["active"] = active
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
    await sync_scheduled_questions(conn)
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
    "archive_question",
    "create_question",
    "get_question",
    "list_questions",
    "update_question",
]
