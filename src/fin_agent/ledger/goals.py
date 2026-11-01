"""Goals and their fenced savings.

Every goal owns a `goal` account. Its balance is real ledger money and is never `liquid`, so the
projection cannot spend it. Contributions are ordinary transfers into that account.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from fin_agent.ledger.accounts import _create_account_in_group
from fin_agent.ledger.audit import write_group
from fin_agent.ledger.common import ledger_balance, today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

GOAL_STATUSES = ("active", "achieved", "paused", "abandoned")


def _goal_row(conn: psycopg.Connection, goal_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM goals WHERE id = %s", (goal_id,)).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No goal {goal_id}")
    return row


def goal_payload(conn: psycopg.Connection, row: dict[str, Any]) -> dict[str, Any]:
    account = conn.execute("SELECT * FROM accounts WHERE id = %s", (row["account_id"],)).fetchone()
    saved = ledger_balance(conn, account["ledger_account_id"]) if account else 0
    target = int(row["target_paise"])
    return {
        "id": row["id"],
        "name": row["name"],
        "target_paise": target,
        "saved_paise": saved,
        "remaining_paise": max(target - saved, 0),
        "progress": round(saved / target, 4) if target else 0,
        "deadline": row["deadline"].isoformat() if row["deadline"] else None,
        "priority": row["priority"],
        "account_id": row["account_id"],
        "contribution_schedule_id": row["contribution_schedule_id"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def create_goal(
    conn: psycopg.Connection,
    *,
    name: str,
    target_paise: int,
    deadline: date | None = None,
    priority: int = 3,
    contribution_schedule_id: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    if not name.strip():
        raise InvalidLedgerError("A goal needs a name")
    if target_paise <= 0:
        raise InvalidLedgerError("A goal target must be positive")
    if not 1 <= priority <= 5:
        raise InvalidLedgerError("Goal priority is 1 to 5")
    if contribution_schedule_id is not None:
        schedule = conn.execute(
            "SELECT * FROM schedules WHERE id = %s", (contribution_schedule_id,)
        ).fetchone()
        if schedule is None:
            raise NotFoundLedgerError(f"No schedule {contribution_schedule_id}")
        if schedule["kind"] != "goal_contribution":
            raise InvalidLedgerError("A goal contribution schedule has kind goal_contribution")

    with write_group(conn, actor=actor, message_id=message_id) as group:
        account = _create_account_in_group(
            conn,
            group,
            kind="goal",
            name=f"{name.strip()} goal",
            liquid=False,
        )
        row = conn.execute(
            """
            INSERT INTO goals
                (name, target_paise, deadline, priority, account_id, contribution_schedule_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                name.strip(),
                target_paise,
                deadline,
                priority,
                account["id"],
                contribution_schedule_id,
            ),
        ).fetchone()
        group.record_created("goals", row)
    return goal_payload(conn, row) | {"undo_id": group.group_id}


def list_goals(conn: psycopg.Connection, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE status <> 'abandoned'"
    rows = conn.execute(
        f"SELECT * FROM goals {where} ORDER BY priority, deadline NULLS LAST, name"  # noqa: S608
    ).fetchall()
    return [goal_payload(conn, row) for row in rows]


def get_goal(conn: psycopg.Connection, goal_id: str) -> dict[str, Any]:
    return goal_payload(conn, _goal_row(conn, goal_id))


def update_goal(
    conn: psycopg.Connection,
    goal_id: str,
    *,
    name: str | None = None,
    target_paise: int | None = None,
    deadline: date | None = None,
    priority: int | None = None,
    status: str | None = None,
    contribution_schedule_id: str | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = _goal_row(conn, goal_id)
    if status is not None and status not in GOAL_STATUSES:
        raise InvalidLedgerError(f"Unknown goal status {status}")
    if target_paise is not None and target_paise <= 0:
        raise InvalidLedgerError("A goal target must be positive")
    if priority is not None and not 1 <= priority <= 5:
        raise InvalidLedgerError("Goal priority is 1 to 5")
    if contribution_schedule_id is not None:
        row = conn.execute(
            "SELECT kind FROM schedules WHERE id = %s", (contribution_schedule_id,)
        ).fetchone()
        if row is None:
            raise NotFoundLedgerError(f"No schedule {contribution_schedule_id}")
        if row["kind"] != "goal_contribution":
            raise InvalidLedgerError("A goal contribution schedule has kind goal_contribution")
    updates: dict[str, Any] = {}
    for key, value in {
        "name": name,
        "target_paise": target_paise,
        "deadline": deadline,
        "priority": priority,
        "status": status,
        "contribution_schedule_id": contribution_schedule_id,
    }.items():
        if value is not None:
            updates[key] = value
    if not updates:
        return goal_payload(conn, before) | {"undo_id": None}
    columns = list(updates)
    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = conn.execute(
            f"""
            UPDATE goals SET {", ".join(f"{column} = %s" for column in columns)},
                updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (*[updates[column] for column in columns], goal_id),
        ).fetchone()
        group.record_updated("goals", goal_id, before, after)
    return goal_payload(conn, after) | {"undo_id": group.group_id}


def archive_goal(
    conn: psycopg.Connection,
    goal_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return update_goal(conn, goal_id, status="abandoned", actor=actor, message_id=message_id)


def goals_summary(conn: psycopg.Connection) -> dict[str, Any]:
    return {"goals": list_goals(conn), "as_of": today().isoformat()}
