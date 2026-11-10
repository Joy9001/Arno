"""Goals and their fenced savings.

Every goal owns a `goal` account. Its balance is real ledger money and is never `liquid`, so the
projection cannot spend it. Contributions are ordinary transfers into that account.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fin_agent.db.conn import Connection
from fin_agent.ledger.accounts import _create_account_in_group
from fin_agent.ledger.audit import write_group
from fin_agent.ledger.common import ledger_balance, today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

GOAL_STATUSES = ("active", "achieved", "paused", "abandoned")


async def _goal_row(conn: Connection, goal_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM goals WHERE id = $1", goal_id)
    if row is None:
        raise NotFoundLedgerError(f"No goal {goal_id}")
    return row


async def goal_payload(conn: Connection, row: dict[str, Any]) -> dict[str, Any]:
    account = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1", row["account_id"])
    saved = await ledger_balance(conn, account["ledger_account_id"]) if account else 0
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


async def create_goal(
    conn: Connection,
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
        schedule = await conn.fetchrow(
            "SELECT * FROM schedules WHERE id = $1", contribution_schedule_id
        )
        if schedule is None:
            raise NotFoundLedgerError(f"No schedule {contribution_schedule_id}")
        if schedule["kind"] != "goal_contribution":
            raise InvalidLedgerError("A goal contribution schedule has kind goal_contribution")

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        account = await _create_account_in_group(
            conn,
            group,
            kind="goal",
            name=f"{name.strip()} goal",
            liquid=False,
        )
        row = await conn.fetchrow_required(
            """
            INSERT INTO goals
                (name, target_paise, deadline, priority, account_id, contribution_schedule_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            name.strip(),
            target_paise,
            deadline,
            priority,
            account["id"],
            contribution_schedule_id,
        )
        group.record_created("goals", row)
    payload = await goal_payload(conn, row)
    return payload | {"undo_id": group.group_id}


async def list_goals(conn: Connection, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE status <> 'abandoned'"
    rows = await conn.fetch(
        f"SELECT * FROM goals {where} ORDER BY priority, deadline NULLS LAST, name"  # noqa: S608
    )
    return [await goal_payload(conn, row) for row in rows]


async def get_goal(conn: Connection, goal_id: str) -> dict[str, Any]:
    return await goal_payload(conn, await _goal_row(conn, goal_id))


async def update_goal(
    conn: Connection,
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
    before = await _goal_row(conn, goal_id)
    if status is not None and status not in GOAL_STATUSES:
        raise InvalidLedgerError(f"Unknown goal status {status}")
    if target_paise is not None and target_paise <= 0:
        raise InvalidLedgerError("A goal target must be positive")
    if priority is not None and not 1 <= priority <= 5:
        raise InvalidLedgerError("Goal priority is 1 to 5")
    if contribution_schedule_id is not None:
        row = await conn.fetchrow(
            "SELECT kind FROM schedules WHERE id = $1", contribution_schedule_id
        )
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
        payload = await goal_payload(conn, before)
        return payload | {"undo_id": None}
    columns = list(updates)
    assignments = ", ".join(f"{column} = ${index}" for index, column in enumerate(columns, start=1))
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        after = await conn.fetchrow_required(
            f"""
            UPDATE goals SET {assignments},
                updated_at = now()
            WHERE id = ${len(columns) + 1} RETURNING *
            """,
            *[updates[column] for column in columns],
            goal_id,
        )
        group.record_updated("goals", goal_id, before, after)
    payload = await goal_payload(conn, after)
    return payload | {"undo_id": group.group_id}


async def archive_goal(
    conn: Connection,
    goal_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_goal(conn, goal_id, status="abandoned", actor=actor, message_id=message_id)


async def goals_summary(conn: Connection) -> dict[str, Any]:
    return {"goals": await list_goals(conn), "as_of": today().isoformat()}
