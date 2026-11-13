"""Write groups and the audit log.

Every ledger write goes through `write_group`. It opens one short transaction, collects one audit
row for every changed row, and returns an `undo_id` (the audit `group_id`). Undo replays the
`before` side of a group: ledger rows get an appended reversal, editable rows get their old values
back, and rows created by the group are archived or deleted. Nothing in the ledger is updated or
deleted.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from arno.db.conn import Connection, quote_ident
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError

Actor = str

# Columns a generic restore may touch. Identity and provenance stay put.
_IMMUTABLE_COLUMNS = {"id", "created_at"}

# A write to any of these can move the projection, so it queues a risk check. Preferences,
# notifications, and scheduled questions do not: they never change cash.
RISK_TABLES = frozenset(
    {
        "accounts",
        "baselines",
        "categories",
        "goals",
        "imports",
        "movements",
        "payees",
        "schedules",
        "schedule_occurrences",
        "schedule_payments",
        "settings",
        "movement_legs",
    }
)


def jsonable(value: Any) -> Any:
    """Turn database values into JSON-safe values for audit rows and exports."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value


async def new_id(conn: Connection, prefix: str) -> str:
    # pgledger_generate_id is a scalar function: it always returns one id.
    row = await conn.fetchrow_required("SELECT pgledger_generate_id($1) AS id", prefix)
    return str(row["id"])


def india_noon(day: date) -> datetime:
    """The moment a movement's date becomes in pgledger's `event_at`."""
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(12), tzinfo=ZoneInfo("Asia/Kolkata"))


def _key_column(table: str) -> str:
    # movement_legs is keyed by its one-to-one transfer, not by a separate id.
    return "transfer_id" if table == "movement_legs" else "id"


def _row_key(table: str, row: dict[str, Any]) -> str:
    return str(row[_key_column(table)])


@dataclass
class WriteGroup:
    conn: Connection
    group_id: str
    actor: Actor
    message_id: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        table: str,
        row_id: str | None,
        *,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.entries.append(
            {
                "table_name": table,
                "row_id": row_id,
                "before": jsonable(before) if before is not None else None,
                "after": jsonable(after) if after is not None else None,
            }
        )

    def record_created(self, table: str, row: dict[str, Any]) -> None:
        self.record(table, _row_key(table, row), before=None, after=row)

    def record_updated(
        self, table: str, row_id: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        self.record(table, row_id, before=before, after=after)

    def record_deleted(self, table: str, row: dict[str, Any]) -> None:
        self.record(table, _row_key(table, row), before=row, after=None)


@asynccontextmanager
async def write_group(
    conn: Connection,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
    group_id: str | None = None,
) -> AsyncGenerator[WriteGroup]:
    """One transaction whose audit rows share a group and can be undone together.

    The group owns its transaction. `asyncpg` turns a transaction opened inside another one into
    a savepoint, so a nested group still commits or rolls back with its caller.
    """
    if actor not in {"user", "agent", "system"}:
        raise InvalidLedgerError("actor must be user, agent, or system")
    async with conn.transaction():
        group = WriteGroup(
            conn=conn,
            group_id=group_id or await new_id(conn, "und"),
            actor=actor,
            message_id=message_id,
        )
        yield group
        for entry in group.entries:
            await conn.execute(
                """
                INSERT INTO audit_log
                    (actor, message_id, group_id, table_name, row_id, before, after)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                group.actor,
                group.message_id,
                group.group_id,
                entry["table_name"],
                entry["row_id"],
                entry["before"],
                entry["after"],
            )
        if any(entry["table_name"] in RISK_TABLES for entry in group.entries):
            # Same transaction as the write, so a crash cannot lose the check or run it twice.
            from arno.jobs.runner import enqueue_risk_check

            await enqueue_risk_check(conn, group.group_id)


async def groups_for_rows(
    conn: Connection, table: str, row_ids: list[str]
) -> dict[str, str | None]:
    if not row_ids:
        return {}
    # Same `unnest(...)` generic-plan idiom as `movements._transfer_rows_for_movements`.
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (row_id) row_id, group_id
        FROM audit_log
        WHERE table_name = $1
          AND row_id = ANY(ARRAY(SELECT unnest($2::text[])))
          AND undone_at IS NULL
        ORDER BY row_id, created_at DESC, id DESC
        """,
        table,
        row_ids,
    )
    result: dict[str, str | None] = {row_id: None for row_id in row_ids}
    for row in rows:
        result[str(row["row_id"])] = str(row["group_id"])
    return result


async def group_for_row(conn: Connection, table: str, row_id: str) -> str | None:
    return (await groups_for_rows(conn, table, [row_id]))[row_id]


async def _load_group(conn: Connection, group_id: str) -> list[dict[str, Any]]:
    return await conn.fetch(
        """
        SELECT id, table_name, row_id, before, after, undone_at, created_at
        FROM audit_log WHERE group_id = $1
        ORDER BY created_at, id
        """,
        group_id,
    )


async def _conflicts(
    conn: Connection, group_id: str, *, ignore: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    return await conn.fetch(
        """
        SELECT a.table_name, a.row_id, a.group_id
        FROM audit_log a
        JOIN (
            SELECT DISTINCT table_name, row_id
            FROM audit_log WHERE group_id = $1 AND row_id IS NOT NULL
        ) touched USING (table_name, row_id)
        WHERE a.group_id <> $2
          AND a.undone_at IS NULL
          AND NOT (a.group_id = ANY($4::text[]))
          AND a.created_at >= (
              SELECT min(created_at) FROM audit_log WHERE group_id = $3
          )
        ORDER BY a.created_at, a.id
        """,
        group_id,
        group_id,
        group_id,
        list(ignore),
    )


# How a conflict reads to the user: what changed, and the one way out.
_CONFLICT_WORDS = {
    "movements": "movement",
    "movement_legs": "label",
    "category_rules": "rule",
    "payees": "payee",
    "categories": "category",
    "accounts": "account",
}


def _describe_conflicts(conflicts: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in conflicts:
        counts[str(row["table_name"])] = counts.get(str(row["table_name"]), 0) + 1
    parts = [
        f"{count} {_CONFLICT_WORDS.get(table, table.replace('_', ' '))}{'s' if count > 1 else ''}"
        for table, count in sorted(counts.items())
    ]
    changed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return (
        f"A later write changed {changed} this undo would revert. "
        "Undo that change first, then try again."
    )


async def _update_row(
    conn: Connection,
    group: WriteGroup,
    table: str,
    row_id: str,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    key = _key_column(table)
    before = await conn.fetchrow(
        f"SELECT * FROM {quote_ident(table)} WHERE {quote_ident(key)}::text = $1",
        row_id,
    )
    if before is None:
        return None
    columns = [column for column in values if column not in _IMMUTABLE_COLUMNS]
    if not columns:
        return before
    assignments = [f"{quote_ident(column)} = ${index}" for index, column in enumerate(columns, 1)]
    params = [values[column] for column in columns]
    after = await conn.fetchrow(
        f"UPDATE {quote_ident(table)} SET {', '.join(assignments)} "
        f"WHERE {quote_ident(key)}::text = ${len(params) + 1} RETURNING *",
        *params,
        row_id,
    )
    if after is None:
        # A concurrent change deleted the row between the read and the write. Nothing left to
        # update, and the callers already treat a missing row as nothing to restore.
        return None
    group.record_updated(table, row_id, before, after)
    return after


async def _insert_row(
    conn: Connection,
    group: WriteGroup,
    table: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    columns = list(values)
    names = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join(f"${index}" for index in range(1, len(columns) + 1))
    after = await conn.fetchrow_required(
        f"INSERT INTO {quote_ident(table)} ({names}) VALUES ({placeholders}) RETURNING *",
        *[values[column] for column in columns],
    )
    group.record_created(table, after)
    return after


async def _restore_before(
    conn: Connection,
    group: WriteGroup,
    table: str,
    row_id: str,
    before: dict[str, Any] | None,
) -> None:
    key = _key_column(table)
    current = await conn.fetchrow(
        f"SELECT * FROM {quote_ident(table)} WHERE {quote_ident(key)}::text = $1",
        row_id,
    )

    if before is not None:
        # Audit rows are JSON, so dates and timestamps come back as strings. Rebuild a typed row
        # before the column placeholders bind it.
        before = await conn.fetchrow(
            f"SELECT * FROM jsonb_populate_record(NULL::{quote_ident(table)}, $1)",
            before,
        )

    if before is None:
        if table == "schedule_occurrences" and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schedule_payments WHERE occurrence_id = $1)", row_id
        ):
            # A later match can share the frozen terms. Undo only this group's allocation.
            return
        if table == "accounts":
            await _update_row(conn, group, table, row_id, {"active": False})
        elif table in {"payees", "categories", "schedules", "preferences", "scheduled_questions"}:
            await _update_row(conn, group, table, row_id, {"active": False})
        elif table == "goals":
            await _update_row(conn, group, table, row_id, {"status": "abandoned"})
        elif table in {
            "category_rules",
            "captures",
            "users",
            "schedule_occurrences",
            "schedule_payments",
            "baselines",
        }:
            if current is not None:
                await conn.execute(
                    f"DELETE FROM {quote_ident(table)} WHERE {quote_ident(key)}::text = $1",
                    row_id,
                )
                group.record_deleted(table, current)
        else:
            raise InvalidLedgerError(f"Cannot undo a created row in {table}")
        return

    if current is None:
        if table == "schedule_payments":
            from arno.ledger.schedule_payments import validate_restored_payment

            await validate_restored_payment(conn, before)
        await _insert_row(conn, group, table, before)
        return
    await _update_row(conn, group, table, row_id, before)


async def _restore_movement(
    conn: Connection,
    group: WriteGroup,
    movement_id: str,
    before: dict[str, Any] | None,
) -> None:
    from arno.ledger.movements import reverse_movement

    if before is None:
        # The group created this movement. A later correction or delete already reversed it, and
        # a second reversal would move the money a third time; reverse_movement locks FOR UPDATE
        # and refuses if an active reversal already cancels it.
        await reverse_movement(group, movement_id, note="Undo")
        return
    values: dict[str, Any] = await conn.fetchrow_required(
        """
        SELECT date, description, kind, reverses_id
        FROM jsonb_populate_record(NULL::movements, $1)
        """,
        {
            "date": before.get("date"),
            "description": before.get("description"),
            "kind": before.get("kind"),
            "reverses_id": before.get("reverses_id"),
        },
    )
    # A categorizer changes these too, so restoring them is part of the same undo. An audit row
    # written before they existed has no value to put back.
    for column in ("category_rule_id", "category_hint"):
        if column in before:
            values[column] = before[column]
    await _update_row(conn, group, "movements", movement_id, values)


async def undo_group(
    conn: Connection,
    undo_id: str,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
    ignore_groups: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Revert one write group. Ledger movements get reversals; editable rows get old values.

    `ignore_groups` names undo groups this same operation just wrote. Their audit rows are not
    later writes; they are the previous step of one undo, such as an import's categorization
    groups before its commit groups.
    """
    entries = await _load_group(conn, undo_id)
    if not entries:
        raise NotFoundLedgerError(f"No write group {undo_id}")
    if all(entry["undone_at"] is not None for entry in entries):
        return {
            "status": "already_undone",
            "undo_id": undo_id,
            "summary": "That write was already undone.",
        }

    conflicts = await _conflicts(conn, undo_id, ignore=ignore_groups)
    if conflicts:
        names = sorted({f"{row['table_name']}:{row['row_id']}" for row in conflicts})
        raise ConflictLedgerError(
            _describe_conflicts(conflicts),
            details={"conflicts": names},
        )

    touched = {entry["table_name"] for entry in entries}
    # A movement and the legs written with it share one group. Undo reverses the movement and
    # keeps its legs, because the reversal copied their labels: the pair then nets to zero inside
    # its category. Only a leg label update added later (for a movement from an earlier group)
    # reverts to its previous values.
    created_movements = {
        str(entry["row_id"])
        for entry in entries
        if entry["table_name"] == "movements" and entry["before"] is None
    }
    # Movements first: a reversal copies the labels, so reverting the original labels before the
    # reversal would lose the category that makes the net spending zero.
    ordered = [entry for entry in entries if entry["table_name"] == "movements"]
    ordered += [entry for entry in reversed(entries) if entry["table_name"] != "movements"]
    async with write_group(conn, actor=actor, message_id=message_id) as undo:
        if touched & {"schedule_occurrences", "schedule_payments"}:
            from arno.ledger.schedule_payments import lock_payment_undo

            await lock_payment_undo(conn, entries)
        for entry in ordered:
            table = entry["table_name"]
            row_id = entry["row_id"]
            if row_id is None:
                continue
            if table == "movements":
                await _restore_movement(conn, undo, row_id, entry["before"])
            elif (
                table == "movement_legs"
                and (entry["after"] or {}).get("movement_id") in created_movements
            ):
                continue
            else:
                await _restore_before(conn, undo, table, row_id, entry["before"])
        if "scheduled_questions" in touched:
            # Undoing a scheduled question must also stop its next run, as archiving it does.
            from arno.jobs.schedules import sync_scheduled_questions

            await sync_scheduled_questions(conn)
        await conn.execute(
            "UPDATE audit_log SET undone_at = now() WHERE group_id = $1 AND undone_at IS NULL",
            undo_id,
        )

    return {
        "status": "undone",
        "undo_id": undo_id,
        "undo_undo_id": undo.group_id,
        "tables": sorted(touched),
        "summary": f"Reverted {len(entries)} changes.",
    }


async def undo_latest(
    conn: Connection, *, actor: Actor = "user", message_id: str | None = None
) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT group_id FROM audit_log
        WHERE undone_at IS NULL AND actor <> 'system'
        GROUP BY group_id
        ORDER BY max(created_at) DESC, max(id) DESC
        LIMIT 1
        """
    )
    if row is None:
        raise NotFoundLedgerError("There is no write to undo.")
    return await undo_group(conn, str(row["group_id"]), actor=actor, message_id=message_id)
