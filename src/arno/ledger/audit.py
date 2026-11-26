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

import asyncpg

from arno.db.conn import Connection, quote_ident
from arno.india.formatting import format_paise
from arno.ledger.errors import (
    CommitOutcomeUnknownError,
    ConflictLedgerError,
    InvalidLedgerError,
    NotFoundLedgerError,
)

Actor = str

_COMMIT_LOSS_ERRORS = (
    asyncpg.PostgresConnectionError,
    asyncpg.AdminShutdownError,
    asyncpg.CrashShutdownError,
    asyncpg.CannotConnectNowError,
    asyncpg.InterfaceError,
    OSError,
    TimeoutError,
)

# Columns a generic restore may touch. Identity and provenance stay put.
_IMMUTABLE_COLUMNS = {"id", "created_at"}


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


async def _commit_transaction(tx: Any) -> None:
    """Commit the group's transaction (a narrow seam for testing commit-time loss)."""
    await tx.commit()


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
    nested = conn.is_in_transaction()
    tx = conn.transaction()
    await tx.start()
    try:
        group = WriteGroup(
            conn=conn,
            group_id=group_id or await new_id(conn, "und"),
            actor=actor,
            message_id=message_id,
        )
        yield group
        if conn.is_closed():
            raise asyncpg.ConnectionDoesNotExistError(
                "The connection closed before COMMIT could be sent."
            )
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
    except BaseException:
        if not conn.is_closed():
            try:
                await tx.rollback()
            except _COMMIT_LOSS_ERRORS:
                pass
        raise

    if nested:
        await tx.commit()
        return
    if conn.is_closed():
        raise asyncpg.ConnectionDoesNotExistError(
            "The connection closed before COMMIT could be sent."
        )
    try:
        await _commit_transaction(tx)
    except _COMMIT_LOSS_ERRORS as exc:
        raise CommitOutcomeUnknownError() from exc


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


@dataclass(frozen=True)
class UndoCheck:
    """What `undo_group` would do with a group right now, without doing it."""

    entries: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]

    @property
    def undone(self) -> bool:
        return bool(self.entries) and all(entry["undone_at"] is not None for entry in self.entries)

    @property
    def reason(self) -> str | None:
        """Why undo would refuse, in undo's own words; None when it would go ahead."""
        if self.undone:
            return "That write was already undone."
        return _describe_conflicts(self.conflicts) if self.conflicts else None


async def check_undo(
    conn: Connection, group_id: str, *, ignore: frozenset[str] = frozenset()
) -> UndoCheck:
    """Read-only. Undo and the history list share it, so the reason shown is the reason tapped."""
    entries = await _load_group(conn, group_id)
    if entries and all(entry["undone_at"] is not None for entry in entries):
        return UndoCheck(entries, [])
    return UndoCheck(entries, await _conflicts(conn, group_id, ignore=ignore))


DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _encode_cursor(created_at: datetime, group_id: str) -> str:
    return f"{created_at.isoformat()}|{group_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created_at, group_id = cursor.rsplit("|", 1)
        value = datetime.fromisoformat(created_at)
        if not group_id or value.tzinfo is None:
            raise ValueError
        return value, group_id
    except (TypeError, ValueError) as exc:
        raise InvalidLedgerError("Invalid history cursor") from exc


# A write group's one-line history summary, read from its audit rows. Most tables have a specific
# phrase; anything else falls back to that group's row counts per table.

_MOVEMENT_WORDS = {
    "expense": "Logged expense",
    "income": "Logged income",
    "transfer": "Transferred",
    "split": "Split",
    "loan": "Lent",
    "refund": "Refunded",
    "settlement": "Settled",
    "adjustment": "Recorded adjustment",
}
_GENERIC_DESCRIPTIONS = {
    "expense",
    "income",
    "transfer",
    "split",
    "loan",
    "refund",
    "settlement",
    "adjustment",
    "reversal",
}


def _rows(count: int) -> str:
    return f"{count} row{'s' if count != 1 else ''}"


async def _movement_detail(conn: Connection, movement_id: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT m.kind, m.description,
               (SELECT sum(t.amount)::bigint
                  FROM movement_legs ml
                  JOIN pgledger_transfers t ON t.id = ml.transfer_id
                 WHERE ml.movement_id = m.id) AS amount_paise,
               (SELECT p.display_name
                  FROM movement_legs ml
                  JOIN payees p ON p.id = ml.payee_id
                 WHERE ml.movement_id = m.id
                 ORDER BY ml.transfer_id
                 LIMIT 1) AS payee_name
        FROM movements m
        WHERE m.id = $1
        """,
        movement_id,
    )
    return dict(row) if row is not None else {}


def _movement_sentence(
    *,
    kind: str | None,
    description: str | None,
    amount_paise: Any,
    payee_name: str | None,
) -> str:
    words = _MOVEMENT_WORDS.get(str(kind), f"Recorded {str(kind).replace('_', ' ')}")
    if amount_paise is not None:
        words = f"{words} {format_paise(int(amount_paise))}"
    label = (payee_name or description or "").strip()
    if label and label.casefold() in _GENERIC_DESCRIPTIONS:
        label = ""
    return f"{words} · {label}" if label else words


async def _movement_phrase(
    conn: Connection,
    movement_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    row = after or before or {}
    if row.get("kind") == "reversal" and row.get("reverses_id"):
        original = await _movement_detail(conn, str(row["reverses_id"]))
        reversed_line = _movement_sentence(
            kind=original.get("kind"),
            description=original.get("description"),
            amount_paise=original.get("amount_paise"),
            payee_name=original.get("payee_name"),
        )
        return f"Reversed: {reversed_line}"
    detail = await _movement_detail(conn, movement_id)
    sentence = _movement_sentence(
        kind=row.get("kind") or detail.get("kind"),
        description=row.get("description") or detail.get("description"),
        amount_paise=detail.get("amount_paise"),
        payee_name=detail.get("payee_name"),
    )
    return (
        f"Changed {sentence[0].lower()}{sentence[1:]}"
        if before is not None and after is not None
        else sentence
    )


async def _leg_phrase(conn: Connection, row: dict[str, Any]) -> str | None:
    movement_id = row.get("movement_id")
    if not movement_id:
        return None
    detail = await _movement_detail(conn, str(movement_id))
    if not detail:
        return None
    sentence = _movement_sentence(
        kind=detail.get("kind"),
        description=detail.get("description"),
        amount_paise=detail.get("amount_paise"),
        payee_name=detail.get("payee_name"),
    )
    return f"Relabeled: {sentence}"


def _account_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str | None:
    row = after or before or {}
    name = row.get("name")
    if not name:
        # Person and goal accounts carry no name of their own; their payee or goal entry speaks.
        return None
    if before is None:
        return f"Added account {name}"
    if after is None or (before.get("active") and not after.get("active")):
        return f"Removed account {name}"
    if before.get("name") != after.get("name"):
        return f"Renamed account {before.get('name') or name} to {after.get('name') or name}"
    return f"Changed account {name}"


def _memory_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    # A delete-for-good blanks every copy of the body, so an empty body never names the text.
    body = str((after or before or {}).get("body") or "").strip()
    old = str((before or {}).get("body") or "").strip()
    if before is None:
        return f"Remembered: {body}" if body else "Remembered a memory"
    if after is None:
        return "Deleted a memory"
    if before.get("active") and not after.get("active"):
        return f"Forgot: {old}" if old else "Forgot a memory"
    if not before.get("active") and after.get("active"):
        return f"Restored: {body}" if body else "Restored a memory"
    if before.get("body") != after.get("body"):
        if old and body:
            return f"Changed memory: {old} → {body}"
        return "Changed a memory"
    if before.get("kind") != after.get("kind"):
        return f"Reclassified memory: {body}" if body else "Reclassified a memory"
    return "Changed a memory"


def _settings_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    before = before or {}
    after = after or {}
    old = before.get("floor_paise")
    new = after.get("floor_paise")
    if old != new:
        if new is None:
            return "Removed the floor"
        if old is None:
            return f"Set floor {format_paise(int(new))}"
        return f"Changed floor {format_paise(int(old))} → {format_paise(int(new))}"
    if before.get("display_name") != after.get("display_name"):
        return f"Changed your name to {after.get('display_name')}"
    return "Changed settings"


def _schedule_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    row = after or before or {}
    name = row.get("name") or "a schedule"
    if before is None:
        return f"Added schedule {name}"
    if after is None or (before.get("active") and not after.get("active")):
        return f"Removed schedule {name}"
    old = before.get("amount_paise")
    new = after.get("amount_paise")
    if old != new and old is not None and new is not None:
        return f"Changed schedule {name} {format_paise(int(old))} → {format_paise(int(new))}"
    return f"Changed schedule {name}"


def _named_phrase(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    noun: str,
    key: str,
) -> str:
    row = after or before or {}
    name = row.get(key) or f"a {noun}"
    if before is None:
        return f"Added {noun} {name}"
    if after is None or (before.get("active") and not after.get("active")):
        return f"Removed {noun} {name}"
    if before.get(key) != after.get(key):
        return f"Renamed {noun} {before.get(key) or name} to {after.get(key) or name}"
    return f"Changed {noun} {name}"


def _goal_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    row = after or before or {}
    name = row.get("name") or "a goal"
    if before is None:
        return f"Added goal {name}"
    if after is None:
        return f"Removed goal {name}"
    if before.get("status") != after.get("status"):
        return f"Changed goal {name} to {after.get('status')}"
    return f"Changed goal {name}"


async def _payment_phrase(
    conn: Connection,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> str:
    row = after or before or {}
    name = None
    if row.get("occurrence_id"):
        name = await conn.fetchval(
            "SELECT name FROM schedule_occurrences WHERE id = $1", str(row["occurrence_id"])
        )
    label = name or "a scheduled payment"
    amount = row.get("amount_paise")
    money = f" {format_paise(int(amount))}" if amount is not None else ""
    return f"{'Unmarked' if after is None else 'Marked'} {label} paid{money}"


async def _entry_phrase(conn: Connection, entry: dict[str, Any]) -> str | None:
    table = str(entry["table_name"])
    before = entry["before"]
    after = entry["after"]
    row_id = entry["row_id"]
    if table == "accounts":
        return _account_phrase(before, after)
    if table == "movements" and row_id is not None:
        return await _movement_phrase(conn, str(row_id), before, after)
    if table == "movement_legs":
        return await _leg_phrase(conn, after or before or {})
    if table == "preferences":
        return _memory_phrase(before, after)
    if table == "settings":
        return _settings_phrase(before, after)
    if table == "schedules":
        return _schedule_phrase(before, after)
    if table == "schedule_occurrences" and after is not None:
        name = after.get("name") or "a scheduled payment"
        amount = after.get("amount_paise")
        money = f" {format_paise(int(amount))}" if amount is not None else ""
        return f"Marked {name} paid{money}"
    if table == "schedule_payments":
        return await _payment_phrase(conn, before, after)
    if table == "payees":
        return _named_phrase(before, after, noun="payee", key="display_name")
    if table in {"categories", "category_rules"}:
        key = "name" if table == "categories" else "pattern"
        return _named_phrase(
            before, after, noun="category rule" if key != "name" else "category", key=key
        )
    if table == "goals":
        return _goal_phrase(before, after)
    return None


async def _import_phrase(conn: Connection, group_id: str) -> str | None:
    row = await conn.fetchrow(
        """
        SELECT (SELECT a.filename FROM attachments a WHERE a.id = i.attachment_id) AS filename,
               (SELECT count(*) FROM statement_lines s
                 WHERE s.import_id = i.id AND s.match_state <> 'ignored') AS rows
        FROM imports i
        WHERE jsonb_exists(i.commit_groups, $1)
          AND i.commit_groups ->> 0 = $1
        LIMIT 1
        """,
        group_id,
    )
    if row is None:
        return None
    name = row["filename"] or "a statement"
    return f"Committed import of {name} ({_rows(int(row['rows']))})"


async def _group_phrase(conn: Connection, group_id: str, entries: list[dict[str, Any]]) -> str:
    imported = await _import_phrase(conn, group_id)
    if imported:
        return imported
    created_movements = [
        entry
        for entry in entries
        if entry["table_name"] == "movements"
        and entry["before"] is None
        and (entry.get("after") or {}).get("kind") != "reversal"
    ]
    if len(created_movements) > 1:
        labels: list[str] = []
        for entry in created_movements:
            movement_id = str(entry["row_id"])
            detail = await _movement_detail(conn, movement_id)
            after = entry.get("after") or {}
            label = str(
                detail.get("payee_name")
                or after.get("description")
                or detail.get("description")
                or ""
            ).strip()
            if label and label.casefold() not in _GENERIC_DESCRIPTIONS and label not in labels:
                labels.append(label)
                if len(labels) == 4:
                    break
        suffix = ""
        if labels:
            shown = ", ".join(labels[:3]) + ("…" if len(labels) > 3 else "")
            suffix = f" · {shown}"
        return f"Logged {len(created_movements)} movements{suffix}"
    # A capture writes its payee before the expense; the money names the group, not the payee.
    ordered = [entry for entry in entries if entry["table_name"] == "movements"]
    ordered += [entry for entry in entries if entry["table_name"] != "movements"]
    for entry in ordered:
        phrase = await _entry_phrase(conn, entry)
        if phrase:
            return phrase
    counts: dict[str, int] = {}
    for entry in entries:
        table = str(entry["table_name"])
        counts[table] = counts.get(table, 0) + 1
    return "Changed " + ", ".join(f"{_rows(count)} in {table}" for table, count in counts.items())


async def _group_summary(
    conn: Connection, group_id: str, entries: list[dict[str, Any]], created_at: datetime
) -> str:
    # `undo_group` stamps the undone rows' `undone_at` with the same transaction `now()` its own
    # audit rows carry, so an exact match names the group this one undid.
    undone = await conn.fetch(
        "SELECT DISTINCT group_id FROM audit_log WHERE undone_at = $1 AND group_id <> $2",
        created_at,
        group_id,
    )
    if undone:
        target_id = str(undone[0]["group_id"])
        return f"Undid: {await _group_phrase(conn, target_id, await _load_group(conn, target_id))}"
    return await _group_phrase(conn, group_id, entries)


async def list_history(
    conn: Connection, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> dict[str, Any]:
    """One page of write groups, newest first, each with what undo would say about it."""
    limit = max(1, min(limit, MAX_LIMIT))
    having = ""
    params: list[Any] = []
    if cursor:
        created_at, group_id = _decode_cursor(cursor)
        having = "HAVING (min(created_at), group_id) < ($1::timestamptz, $2::text)"
        params.extend([created_at, group_id])
    groups = await conn.fetch(
        f"""
        SELECT group_id, min(created_at) AS created_at, max(actor) AS actor,
               max(message_id) AS message_id
        FROM audit_log
        GROUP BY group_id
        {having}
        ORDER BY min(created_at) DESC, group_id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `having` is a fixed fragment, never user text
        *params,
        limit + 1,
    )
    has_more = len(groups) > limit
    groups = groups[:limit]
    items = []
    for group in groups:
        group_id = str(group["group_id"])
        check = await check_undo(conn, group_id)
        conversation_id = None
        if group["message_id"]:
            conversation_id = await conn.fetchval(
                "SELECT conversation_id FROM messages WHERE id = $1", group["message_id"]
            )
        summary = await _group_summary(conn, group_id, check.entries, group["created_at"])
        items.append(
            {
                "undo_id": group_id,
                "created_at": group["created_at"].isoformat(),
                "actor": group["actor"],
                "message_id": group["message_id"],
                "conversation_id": conversation_id,
                "summary": summary,
                "records": [
                    {"table": entry["table_name"], "id": entry["row_id"]} for entry in check.entries
                ],
                "undoable": check.reason is None,
                "reason": check.reason,
            }
        )
    next_cursor = (
        _encode_cursor(groups[-1]["created_at"], str(groups[-1]["group_id"]))
        if has_more and groups
        else None
    )
    return {"items": items, "next_cursor": next_cursor}


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
    # A categorizer changes the first two, and a one-off mark is an edit like them. An audit row
    # written before a column existed has no value to put back.
    for column in ("category_rule_id", "category_hint", "one_off"):
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
    check = await check_undo(conn, undo_id, ignore=ignore_groups)
    entries = check.entries
    if not entries:
        raise NotFoundLedgerError(f"No write group {undo_id}")
    if check.undone:
        return {
            "status": "already_undone",
            "undo_id": undo_id,
            "summary": "That write was already undone.",
        }
    if check.conflicts:
        names = sorted({f"{row['table_name']}:{row['row_id']}" for row in check.conflicts})
        raise ConflictLedgerError(
            _describe_conflicts(check.conflicts),
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
