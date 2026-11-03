"""Write groups and the audit log.

Every ledger write goes through `write_group`. It opens one short transaction, collects one audit
row for every changed row, and returns an `undo_id` (the audit `group_id`). Undo replays the
`before` side of a group: ledger rows get an appended reversal, editable rows get their old values
back, and rows created by the group are archived or deleted. Nothing in the ledger is updated or
deleted.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError, NotFoundLedgerError

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
        "settings",
        "transfer_labels",
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


def new_id(conn: psycopg.Connection, prefix: str) -> str:
    row = conn.execute("SELECT pgledger_generate_id(%s) AS id", (prefix,)).fetchone()
    return str(row["id"])


def india_noon(day: date) -> datetime:
    """The moment a movement's date becomes in pgledger's `event_at`."""
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(12), tzinfo=ZoneInfo("Asia/Kolkata"))


def _key_column(table: str) -> str:
    # transfer_labels is keyed by its one-to-one transfer, not by a separate id.
    return "transfer_id" if table == "transfer_labels" else "id"


def _row_key(table: str, row: dict[str, Any]) -> str:
    return str(row[_key_column(table)])


@dataclass
class WriteGroup:
    conn: psycopg.Connection
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


@contextmanager
def write_group(
    conn: psycopg.Connection,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
    group_id: str | None = None,
) -> Iterator[WriteGroup]:
    """One transaction whose audit rows share a group and can be undone together.

    A psycopg connection starts an implicit transaction on the first `SELECT`, so a write group
    opened after a read would otherwise become a savepoint and never commit. The group owns its
    transaction: finish any open implicit transaction first, then begin a real one.
    """
    if actor not in {"user", "agent", "system"}:
        raise InvalidLedgerError("actor must be user, agent, or system")
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        conn.commit()
    with conn.transaction():
        group = WriteGroup(
            conn=conn,
            group_id=group_id or new_id(conn, "und"),
            actor=actor,
            message_id=message_id,
        )
        yield group
        for entry in group.entries:
            conn.execute(
                """
                INSERT INTO audit_log
                    (actor, message_id, group_id, table_name, row_id, before, after)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    group.actor,
                    group.message_id,
                    group.group_id,
                    entry["table_name"],
                    entry["row_id"],
                    Jsonb(entry["before"]) if entry["before"] is not None else None,
                    Jsonb(entry["after"]) if entry["after"] is not None else None,
                ),
            )
        if any(entry["table_name"] in RISK_TABLES for entry in group.entries):
            # Same transaction as the write, so a crash cannot lose the check or run it twice.
            from fin_agent.jobs.runner import enqueue_risk_check

            enqueue_risk_check(conn, group.group_id)


def group_for_row(conn: psycopg.Connection, table: str, row_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT group_id FROM audit_log
        WHERE table_name = %s AND row_id = %s AND undone_at IS NULL
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (table, row_id),
    ).fetchone()
    return str(row["group_id"]) if row else None


def _load_group(conn: psycopg.Connection, group_id: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT id, table_name, row_id, before, after, undone_at, created_at
            FROM audit_log WHERE group_id = %s
            ORDER BY created_at, id
            """,
            (group_id,),
        ).fetchall()
    )


def _conflicts(conn: psycopg.Connection, group_id: str) -> list[dict[str, Any]]:
    return list(
        conn.execute(
            """
            SELECT a.table_name, a.row_id, a.group_id
            FROM audit_log a
            JOIN (
                SELECT DISTINCT table_name, row_id
                FROM audit_log WHERE group_id = %s AND row_id IS NOT NULL
            ) touched USING (table_name, row_id)
            WHERE a.group_id <> %s
              AND a.undone_at IS NULL
              AND a.created_at >= (
                  SELECT min(created_at) FROM audit_log WHERE group_id = %s
              )
            ORDER BY a.created_at, a.id
            """,
            (group_id, group_id, group_id),
        ).fetchall()
    )


def _update_row(
    conn: psycopg.Connection,
    group: WriteGroup,
    table: str,
    row_id: str,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    key = _key_column(table)
    before = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE {} = %s").format(
            sql.Identifier(table), sql.Identifier(key)
        ),
        (row_id,),
    ).fetchone()
    if before is None:
        return None
    assignments = [
        sql.SQL("{} = %s").format(sql.Identifier(column))
        for column in values
        if column not in _IMMUTABLE_COLUMNS
    ]
    if not assignments:
        return before
    params = [value for column, value in values.items() if column not in _IMMUTABLE_COLUMNS]
    after = conn.execute(
        sql.SQL("UPDATE {} SET {} WHERE {} = %s RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(assignments),
            sql.Identifier(key),
        ),
        (*params, row_id),
    ).fetchone()
    group.record_updated(table, row_id, before, after)
    return after


def _insert_row(
    conn: psycopg.Connection,
    group: WriteGroup,
    table: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    columns = list(values)
    after = conn.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(table),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        ),
        tuple(values[column] for column in columns),
    ).fetchone()
    group.record_created(table, after)
    return after


def _restore_before(
    conn: psycopg.Connection,
    group: WriteGroup,
    table: str,
    row_id: str,
    before: dict[str, Any] | None,
) -> None:
    key = _key_column(table)
    current = conn.execute(
        sql.SQL("SELECT * FROM {} WHERE {} = %s").format(
            sql.Identifier(table), sql.Identifier(key)
        ),
        (row_id,),
    ).fetchone()

    if before is None:
        if table == "accounts":
            _update_row(conn, group, table, row_id, {"active": False})
        elif table in {"payees", "categories", "schedules"}:
            _update_row(conn, group, table, row_id, {"active": False})
        elif table == "goals":
            _update_row(conn, group, table, row_id, {"status": "abandoned"})
        elif table in {"category_rules", "transfer_labels", "captures", "users"}:
            if current is not None:
                conn.execute(
                    sql.SQL("DELETE FROM {} WHERE {} = %s").format(
                        sql.Identifier(table), sql.Identifier(key)
                    ),
                    (row_id,),
                )
                group.record_deleted(table, current)
        else:
            raise InvalidLedgerError(f"Cannot undo a created row in {table}")
        return

    if current is None:
        _insert_row(conn, group, table, before)
        return
    _update_row(conn, group, table, row_id, before)


def _restore_movement(
    conn: psycopg.Connection,
    group: WriteGroup,
    movement_id: str,
    before: dict[str, Any] | None,
) -> None:
    from fin_agent.ledger.movements import reverse_movement

    if before is None:
        reverse_movement(group, movement_id, note="Undo")
        return
    values = {
        "date": before.get("date"),
        "description": before.get("description"),
        "kind": before.get("kind"),
        "reverses_id": before.get("reverses_id"),
    }
    _update_row(conn, group, "movements", movement_id, values)


def undo_group(
    conn: psycopg.Connection,
    undo_id: str,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    """Revert one write group. Ledger movements get reversals; editable rows get old values."""
    entries = _load_group(conn, undo_id)
    if not entries:
        raise NotFoundLedgerError(f"No write group {undo_id}")
    if all(entry["undone_at"] is not None for entry in entries):
        return {
            "status": "already_undone",
            "undo_id": undo_id,
            "summary": "That write was already undone.",
        }

    conflicts = _conflicts(conn, undo_id)
    if conflicts:
        names = sorted({f"{row['table_name']}:{row['row_id']}" for row in conflicts})
        raise ConflictLedgerError(
            "A later write changed rows this group touched.",
            details={"conflicts": names},
        )

    touched = {entry["table_name"] for entry in entries}
    # Movements first: a reversal copies the labels, so deleting the original labels before the
    # reversal would lose the category that makes the net spending zero.
    ordered = [entry for entry in entries if entry["table_name"] == "movements"]
    ordered += [entry for entry in reversed(entries) if entry["table_name"] != "movements"]
    with write_group(conn, actor=actor, message_id=message_id) as undo:
        for entry in ordered:
            table = entry["table_name"]
            row_id = entry["row_id"]
            if row_id is None:
                continue
            if table == "movements":
                _restore_movement(conn, undo, row_id, entry["before"])
            else:
                _restore_before(conn, undo, table, row_id, entry["before"])
        conn.execute(
            "UPDATE audit_log SET undone_at = now() WHERE group_id = %s AND undone_at IS NULL",
            (undo_id,),
        )

    return {
        "status": "undone",
        "undo_id": undo_id,
        "undo_undo_id": undo.group_id,
        "tables": sorted(touched),
        "summary": f"Reverted {len(entries)} changes.",
    }


def undo_latest(
    conn: psycopg.Connection, *, actor: Actor = "user", message_id: str | None = None
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT group_id FROM audit_log
        WHERE undone_at IS NULL
        GROUP BY group_id
        ORDER BY max(created_at) DESC, max(id) DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise NotFoundLedgerError("There is no write to undo.")
    return undo_group(conn, str(row["group_id"]), actor=actor, message_id=message_id)
