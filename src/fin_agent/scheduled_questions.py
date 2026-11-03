"""The user's scheduled questions: CRUD, validation, and the next run shown in Settings.

They run through the agent read-only (see `fin_agent.jobs.questions`). The row here is only the
schedule and the per-run spend ceiling; the answer lands in the inbox.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from fin_agent.jobs.schedules import INDIA, next_occurrence, sync_scheduled_questions
from fin_agent.ledger.audit import write_group
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

DEFAULT_SPEND_LIMIT_USD = Decimal("1.00")


def _notification_time(conn: psycopg.Connection) -> time:
    row = conn.execute("SELECT notification_time FROM settings WHERE id = 1").fetchone()
    value = row["notification_time"] if row else time(8, 0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0, tzinfo=None)
    return time.fromisoformat(str(value)).replace(second=0, microsecond=0, tzinfo=None)


def _check_rule(conn: psycopg.Connection, rule: str, now: datetime | None = None) -> None:
    now = now or datetime.now(INDIA)
    try:
        following = next_occurrence(rule, after=now, default_time=_notification_time(conn))
    except ValueError as exc:
        raise InvalidLedgerError(str(exc)) from exc
    if following is None:
        raise InvalidLedgerError("That rule never runs again")


def _spend_cents(value: Decimal | int | float | str | None) -> int | None:
    if value is None:
        return None
    try:
        cents = int((Decimal(str(value)) * 100).quantize(Decimal("1")))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidLedgerError("The spend limit is in US dollars") from exc
    if cents < 0:
        raise InvalidLedgerError("A spend limit cannot be negative")
    return cents


def _payload(row: dict[str, Any], next_run_at: Any = None) -> dict[str, Any]:
    cents = row.get("spend_limit_cents")
    return {
        "id": row["id"],
        "prompt": row["prompt"],
        "rule": row["rule"],
        "spend_limit_usd": (
            str((Decimal(cents) / 100).quantize(Decimal("0.01"))) if cents is not None else None
        ),
        "default_spend_limit_usd": str(DEFAULT_SPEND_LIMIT_USD),
        "last_run_at": row["last_run_at"].isoformat() if row["last_run_at"] else None,
        "next_run_at": next_run_at.isoformat() if next_run_at else None,
        "active": row["active"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


def list_questions(
    conn: psycopg.Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE sq.active"
    rows = list(
        conn.execute(
            f"""
            SELECT sq.*, js.next_run_at
            FROM scheduled_questions sq
            LEFT JOIN job_schedules js
                ON js.kind = 'scheduled_question'
               AND js.payload ->> 'scheduled_question_id' = sq.id
            {where}
            ORDER BY sq.active DESC, sq.created_at
            """,  # noqa: S608
        ).fetchall()
    )
    return [_payload(row, row.get("next_run_at")) for row in rows]


def get_question(conn: psycopg.Connection, question_id: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT sq.*, js.next_run_at
        FROM scheduled_questions sq
        LEFT JOIN job_schedules js
            ON js.kind = 'scheduled_question'
           AND js.payload ->> 'scheduled_question_id' = sq.id
        WHERE sq.id = %s
        """,
        (question_id,),
    ).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No scheduled question {question_id}")
    return _payload(row, row.get("next_run_at"))


def create_question(
    conn: psycopg.Connection,
    *,
    prompt: str,
    rule: str,
    spend_limit_usd: Decimal | int | float | str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    text = prompt.strip()
    if not text:
        raise InvalidLedgerError("A scheduled question needs a prompt")
    _check_rule(conn, rule)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        row = conn.execute(
            """
            INSERT INTO scheduled_questions (prompt, rule, spend_limit_cents)
            VALUES (%s, %s, %s) RETURNING *
            """,
            (text, rule.strip(), _spend_cents(spend_limit_usd)),
        ).fetchone()
        group.record_created("scheduled_questions", row)
    sync_scheduled_questions(conn)
    return get_question(conn, row["id"]) | {"undo_id": group.group_id}


def update_question(
    conn: psycopg.Connection,
    question_id: str,
    *,
    prompt: str | None = None,
    rule: str | None = None,
    spend_limit_usd: Decimal | int | float | str | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = conn.execute(
        "SELECT * FROM scheduled_questions WHERE id = %s", (question_id,)
    ).fetchone()
    if before is None:
        raise NotFoundLedgerError(f"No scheduled question {question_id}")
    updates: dict[str, Any] = {}
    if prompt is not None:
        if not prompt.strip():
            raise InvalidLedgerError("A scheduled question needs a prompt")
        updates["prompt"] = prompt.strip()
    if rule is not None:
        _check_rule(conn, rule)
        updates["rule"] = rule.strip()
    if spend_limit_usd is not None:
        updates["spend_limit_cents"] = _spend_cents(spend_limit_usd)
    if active is not None:
        updates["active"] = active
    if not updates:
        return get_question(conn, question_id) | {"undo_id": None}
    columns = list(updates)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = conn.execute(
            f"""
            UPDATE scheduled_questions
            SET {", ".join(f"{column} = %s" for column in columns)}, updated_at = now()
            WHERE id = %s RETURNING *
            """,  # noqa: S608
            (*[updates[column] for column in columns], question_id),
        ).fetchone()
        group.record_updated("scheduled_questions", question_id, before, after)
    sync_scheduled_questions(conn)
    return get_question(conn, question_id) | {"undo_id": group.group_id}


def archive_question(
    conn: psycopg.Connection,
    question_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return update_question(conn, question_id, active=False, actor=actor, message_id=message_id)


__all__ = [
    "DEFAULT_SPEND_LIMIT_USD",
    "archive_question",
    "create_question",
    "get_question",
    "list_questions",
    "update_question",
]
