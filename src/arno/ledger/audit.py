"""Write groups and the undo log.

Every ledger write goes through `write_group`. It opens one short transaction, collects a
`{table, key, before, after}` entry for every changed row, and writes one `undo_log` row (its id
is the `undo_id`) with a one-line summary. Undo replays the snapshot backwards: rows the group
created are deleted (or archived when later rows may point at them), rows it deleted are put back,
and rows it edited get their old values.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid7

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

# Columns an undo never rewrites: identity and provenance stay put.
_IMMUTABLE_COLUMNS = {"id", "created_at"}
# Rows other rows may point at: undoing their creation archives them instead of deleting.
_ARCHIVED_ON_UNDO = {
    "accounts",
    "payees",
    "categories",
    "schedules",
    "preferences",
    "scheduled_questions",
}


def jsonable(value: Any) -> Any:
    """Turn database values into JSON-safe values for snapshots and exports."""
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
    return str((await conn.fetchrow_required("SELECT new_id($1) AS id", prefix))["id"])


def _key_column(table: str) -> str:
    # movement_legs is keyed by its transfer id, not by a separate id.
    return "transfer_id" if table == "movement_legs" else "id"


@dataclass
class WriteGroup:
    conn: Connection
    group_id: str
    actor: Actor
    message_id: str | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    summary: str | None = None  # set by a caller that knows better than the generic phrasing

    def record(
        self, table: str, key: str, *, before: dict[str, Any] | None, after: dict[str, Any] | None
    ) -> None:
        self.entries.append(
            {
                "table": table,
                "key": key,
                "before": jsonable(before) if before is not None else None,
                "after": jsonable(after) if after is not None else None,
            }
        )

    def record_created(self, table: str, row: dict[str, Any]) -> None:
        self.record(table, str(row[_key_column(table)]), before=None, after=row)

    def record_updated(
        self, table: str, key: str, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        self.record(table, key, before=before, after=after)

    def record_deleted(self, table: str, row: dict[str, Any]) -> None:
        self.record(table, str(row[_key_column(table)]), before=row, after=None)


def _is_closed(conn: Connection) -> bool:
    """A pooled connection whose backend was lost is detached and raises instead of saying so."""
    try:
        return conn.is_closed()
    except asyncpg.InterfaceError:
        return True


async def _commit_transaction(tx: Any) -> None:
    """Commit the group's transaction (a narrow seam for testing commit-time loss)."""
    await tx.commit()


# Groups open on a connection, so a nested `write_group` with the same id joins its outer one.
_OPEN: dict[tuple[int, str], WriteGroup] = {}


@asynccontextmanager
async def write_group(
    conn: Connection,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
    group_id: str | None = None,
) -> AsyncGenerator[WriteGroup]:
    """One transaction whose changes share an `undo_log` row and can be undone together.

    The group owns its transaction. `asyncpg` turns a transaction opened inside another one into
    a savepoint, so a nested group still commits or rolls back with its caller. A nested group
    with the id of an open one adds its entries to it; only the outermost group writes the row.
    """
    if actor not in {"user", "agent", "system"}:
        raise InvalidLedgerError("actor must be user, agent, or system")
    parent = _OPEN.get((id(conn), group_id)) if group_id else None
    nested = conn.is_in_transaction()
    tx = conn.transaction()
    await tx.start()
    group = WriteGroup(
        conn=conn,
        group_id=group_id or f"und_{uuid7().hex}",
        actor=actor,
        message_id=message_id,
    )
    key = (id(conn), group.group_id)
    if parent is None:
        _OPEN[key] = group
    try:
        yield group
        if _is_closed(conn):
            raise asyncpg.ConnectionDoesNotExistError(
                "The connection closed before COMMIT could be sent."
            )
        if parent is not None:
            parent.entries.extend(group.entries)
        elif group.entries:
            await conn.execute(
                """
                INSERT INTO undo_log (id, actor, message_id, summary, snapshot)
                VALUES ($1, $2, $3, $4, $5)
                """,
                group.group_id,
                group.actor,
                group.message_id,
                group.summary or await _summarize(conn, group.entries),
                group.entries,
            )
    except BaseException:
        if not _is_closed(conn):
            try:
                await tx.rollback()
            except _COMMIT_LOSS_ERRORS:
                pass
        raise
    finally:
        if parent is None:
            _OPEN.pop(key, None)

    if nested:
        await tx.commit()
        return
    if _is_closed(conn):
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
    """The latest group that still stands for each row: the one undo would revert."""
    if not row_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (k.key) k.key AS row_id, g.id AS group_id
        FROM unnest($2::text[]) AS k(key)
        JOIN undo_log g ON g.undone_at IS NULL
         AND g.snapshot @> jsonb_build_array(jsonb_build_object('table', $1::text, 'key', k.key))
        ORDER BY k.key, g.created_at DESC, g.id DESC
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
    undone: bool = False

    @property
    def reason(self) -> str | None:
        """Why undo would refuse, in undo's own words; None when it would go ahead."""
        if self.undone:
            return "That write was already undone."
        return _describe_conflicts(self.conflicts) if self.conflicts else None


async def _check(conn: Connection, row: dict[str, Any] | None, ignore: frozenset[str]) -> UndoCheck:
    if row is None:
        return UndoCheck([], [])
    if row["undone_at"] is not None:
        return UndoCheck(row["snapshot"], [], undone=True)
    # A later live group that touched a row this one touched (the same-transaction `>=` keeps
    # groups written together from hiding behind each other)...
    candidates = await conn.fetch(
        """
        SELECT DISTINCT h.id AS group_id, t.e ->> 'table' AS table_name, t.e ->> 'key' AS row_id
        FROM undo_log g
        CROSS JOIN LATERAL jsonb_array_elements(g.snapshot) AS t(e)
        JOIN undo_log h ON h.id <> g.id AND h.undone_at IS NULL
         AND NOT (h.id = ANY($2::text[])) AND h.created_at >= g.created_at
         AND h.snapshot @> jsonb_build_array(
                jsonb_build_object('table', t.e ->> 'table', 'key', t.e ->> 'key'))
        WHERE g.id = $1
        """,
        row["id"],
        list(ignore),
    )
    # ...is a conflict only if it left the row somewhere other than where this group left it. A
    # later undo that put everything back is no obstacle.
    end_state = {(e["table"], e["key"]): e["after"] for e in row["snapshot"]}
    differs: dict[tuple[str, str], bool] = {}
    for c in candidates:
        key = (c["table_name"], c["row_id"])
        if key not in differs:
            current = await conn.fetchrow(
                f"SELECT * FROM {quote_ident(key[0])} "  # noqa: S608
                f"WHERE {quote_ident(_key_column(key[0]))}::text = $1",
                key[1],
            )
            wanted = end_state[key]
            differs[key] = (
                current is not None
                if wanted is None
                else (current is None or jsonable(current) != wanted)
            )
    return UndoCheck(
        row["snapshot"], [c for c in candidates if differs[(c["table_name"], c["row_id"])]]
    )


async def check_undo(
    conn: Connection, group_id: str, *, ignore: frozenset[str] = frozenset()
) -> UndoCheck:
    """Read-only. Undo and the history list share it, so the reason shown is the reason tapped."""
    row = await conn.fetchrow("SELECT * FROM undo_log WHERE id = $1", group_id)
    return await _check(conn, row, ignore)


# A group's one-line summary, written with it from its entries.

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
# table -> (noun, the column that names a row)
_NAMED = {
    "accounts": ("account", "name"),
    "payees": ("payee", "display_name"),
    "categories": ("category", "name"),
    "category_rules": ("category rule", "pattern"),
    "schedules": ("schedule", "name"),
    "goals": ("goal", "name"),
}


def _rows(count: int) -> str:
    return f"{count} row{'s' if count != 1 else ''}"


def _money(amount_paise: Any) -> str:
    return f" {format_paise(int(amount_paise))}" if amount_paise is not None else ""


async def _movement_detail(
    conn: Connection, movement_id: str, entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Kind, description, amount, and payee of a movement, from the group when it made or removed
    the movement (its legs are all there) and from the database otherwise."""
    own = next((e for e in entries if e["table"] == "movements" and e["key"] == movement_id), None)
    if own is not None and (own["before"] is None or own["after"] is None):
        movement = own["after"] or own["before"]
        legs = [
            e["after"] or e["before"]
            for e in entries
            if e["table"] == "movement_legs"
            and (e["after"] or e["before"])["movement_id"] == movement_id
        ]
    else:
        movement = await conn.fetchrow(
            "SELECT kind, description FROM movements WHERE id = $1", movement_id
        )
        legs = await conn.fetch(
            "SELECT * FROM movement_legs WHERE movement_id = $1 ORDER BY transfer_id", movement_id
        )
    if movement is None:
        return {}
    payee_id = next((leg["payee_id"] for leg in legs if leg["payee_id"]), None)
    return {
        "kind": movement["kind"],
        "description": movement["description"],
        "amount_paise": sum(int(leg["amount_paise"]) for leg in legs) if legs else None,
        "payee_name": await conn.fetchval("SELECT display_name FROM payees WHERE id = $1", payee_id)
        if payee_id
        else None,
    }


def _movement_sentence(detail: dict[str, Any], verb: str | None = None) -> str:
    kind = str(detail.get("kind"))
    words = (verb or _MOVEMENT_WORDS.get(kind, f"Recorded {kind}")) + _money(
        detail.get("amount_paise")
    )
    label = (detail.get("payee_name") or detail.get("description") or "").strip()
    if label.casefold() in _MOVEMENT_WORDS:
        label = ""
    return f"{words} · {label}" if label else words


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
    if old and body and old != body:
        return f"Changed memory: {old} → {body}"
    return "Changed a memory"


def _settings_phrase(before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    old, new = (before or {}).get("floor_paise"), (after or {}).get("floor_paise")
    if old == new:
        return "Changed settings"
    if new is None:
        return "Removed the floor"
    if old is None:
        return f"Set floor {format_paise(int(new))}"
    return f"Changed floor {format_paise(int(old))} → {format_paise(int(new))}"


def _named_phrase(table: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
    noun, key = _NAMED[table]
    row = after or before or {}
    name = row.get(key) or f"a {noun}"
    if before is None:
        return f"Added {noun} {name}"
    if after is None or (before.get("active") and not after.get("active")):
        return f"Removed {noun} {name}"
    if before.get(key) != after.get(key):
        return f"Renamed {noun} {before.get(key) or name} to {after.get(key) or name}"
    return f"Changed {noun} {name}"


async def _entry_phrase(
    conn: Connection, entry: dict[str, Any], entries: list[dict[str, Any]]
) -> str | None:
    table, before, after = entry["table"], entry["before"], entry["after"]
    row = after or before or {}
    if table == "movements":
        detail = await _movement_detail(conn, entry["key"], entries)
        if after is None:
            return _movement_sentence(detail, f"Deleted {detail.get('kind')}")
        sentence = _movement_sentence(detail)
        return sentence if before is None else f"Changed {sentence[0].lower()}{sentence[1:]}"
    if table == "movement_legs":
        detail = await _movement_detail(conn, str(row["movement_id"]), entries)
        return f"Relabeled: {_movement_sentence(detail)}" if detail else None
    if table == "preferences":
        return _memory_phrase(before, after)
    if table == "settings":
        return _settings_phrase(before, after)
    if table == "schedule_payments":
        name = await conn.fetchval(
            "SELECT name FROM schedule_occurrences WHERE id = $1", str(row["occurrence_id"])
        )
        verb = "Unmarked" if after is None else "Marked"
        return f"{verb} {name or 'a scheduled payment'} paid{_money(row.get('amount_paise'))}"
    # Person and goal accounts carry no name of their own; their payee or goal entry speaks.
    if table in _NAMED and not (table == "accounts" and not row.get("name")):
        return _named_phrase(table, before, after)
    return None


async def _summarize(conn: Connection, entries: list[dict[str, Any]]) -> str:
    created = [e for e in entries if e["table"] == "movements" and e["before"] is None]
    if len(created) > 1:
        legs = [e["after"] for e in entries if e["table"] == "movement_legs" and e["after"]]
        ids = sorted({leg["payee_id"] for leg in legs if leg["payee_id"]})
        names = {
            row["id"]: row["display_name"]
            for row in await conn.fetch(
                "SELECT id, display_name FROM payees WHERE id = ANY($1::text[])", ids
            )
        }
        labels: list[str] = []
        for entry in created:
            payee_id = next(
                (
                    leg["payee_id"]
                    for leg in legs
                    if leg["movement_id"] == entry["key"] and leg["payee_id"]
                ),
                None,
            )
            label = str(names.get(payee_id) or entry["after"]["description"]).strip()
            if label.casefold() not in _MOVEMENT_WORDS and label not in labels:
                labels.append(label)
        shown = ", ".join(labels[:3]) + ("…" if len(labels) > 3 else "")
        return f"Logged {len(created)} movements" + (f" · {shown}" if labels else "")
    # A capture writes its payee before the expense; the money names the group, not the payee.
    for entry in sorted(entries, key=lambda e: bool(e["table"] != "movements")):
        if phrase := await _entry_phrase(conn, entry, entries):
            return phrase
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry["table"]] = counts.get(entry["table"], 0) + 1
    return "Changed " + ", ".join(f"{_rows(n)} in {table}" for table, n in counts.items())


async def _import_phrase(conn: Connection, group_id: str) -> str | None:
    """A commit group is named by its import, which is linked to it after the group is written."""
    row = await conn.fetchrow(
        """
        SELECT (SELECT a.filename FROM attachments a WHERE a.id = i.attachment_id) AS filename,
               (SELECT count(*) FROM statement_lines s
                 WHERE s.import_id = i.id AND s.match_state <> 'ignored') AS rows
        FROM imports i
        WHERE jsonb_exists(i.commit_groups, $1) AND i.commit_groups ->> 0 = $1
        LIMIT 1
        """,
        group_id,
    )
    if row is None:
        return None
    return f"Committed import of {row['filename'] or 'a statement'} ({_rows(int(row['rows']))})"


DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        created_at, group_id = cursor.rsplit("|", 1)
        value = datetime.fromisoformat(created_at)
        if not group_id or value.tzinfo is None:
            raise ValueError
        return value, group_id
    except (TypeError, ValueError) as exc:
        raise InvalidLedgerError("Invalid history cursor") from exc


async def list_history(
    conn: Connection, *, limit: int = DEFAULT_LIMIT, cursor: str | None = None
) -> dict[str, Any]:
    """One page of write groups, newest first, each with what undo would say about it."""
    limit = max(1, min(limit, MAX_LIMIT))
    after = "WHERE (u.created_at, u.id) < ($1::timestamptz, $2::text)" if cursor else ""
    params: list[Any] = list(_decode_cursor(cursor)) if cursor else []
    groups = await conn.fetch(
        f"""
        SELECT u.*, m.conversation_id
        FROM undo_log u LEFT JOIN messages m ON m.id = u.message_id
        {after}
        ORDER BY u.created_at DESC, u.id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `after` is a fixed fragment, never user text
        *params,
        limit + 1,
    )
    has_more = len(groups) > limit
    groups = groups[:limit]
    items = []
    for group in groups:
        check = await _check(conn, group, frozenset())
        items.append(
            {
                "undo_id": group["id"],
                "created_at": group["created_at"].isoformat(),
                "actor": group["actor"],
                "message_id": group["message_id"],
                "conversation_id": group["conversation_id"],
                "summary": await _import_phrase(conn, group["id"]) or group["summary"],
                "records": [{"table": e["table"], "id": e["key"]} for e in group["snapshot"]],
                "undoable": check.reason is None,
                "reason": check.reason,
            }
        )
    next_cursor = f"{groups[-1]['created_at'].isoformat()}|{groups[-1]['id']}" if has_more else None
    return {"items": items, "next_cursor": next_cursor}


async def _restore(conn: Connection, undo: WriteGroup, entry: dict[str, Any]) -> None:
    """Put one snapshot entry back, recording the change in the undo's own group."""
    table, key, before = entry["table"], entry["key"], entry["before"]
    tbl, pk = quote_ident(table), quote_ident(_key_column(table))
    current = await conn.fetchrow(f"SELECT * FROM {tbl} WHERE {pk}::text = $1", key)  # noqa: S608

    if before is None:  # the group created this row
        if current is None:
            return
        if table == "schedule_occurrences" and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schedule_payments WHERE occurrence_id = $1)", key
        ):
            return  # a later match shares the frozen terms; undo only this group's allocation
        if table in _ARCHIVED_ON_UNDO or table == "goals":
            assignment = "status = 'abandoned'" if table == "goals" else "active = false"
            after = await conn.fetchrow_required(
                f"UPDATE {tbl} SET {assignment} WHERE {pk}::text = $1 RETURNING *",  # noqa: S608
                key,
            )
            undo.record_updated(table, key, current, after)
        else:
            await conn.execute(f"DELETE FROM {tbl} WHERE {pk}::text = $1", key)  # noqa: S608
            undo.record_deleted(table, current)
    elif current is None:  # the group deleted this row
        if table == "schedule_payments":
            from arno.ledger.schedule_payments import validate_restored_payment

            await validate_restored_payment(conn, before)
        restored = await conn.fetchrow_required(
            f"INSERT INTO {tbl} SELECT * FROM jsonb_populate_record(NULL::{tbl}, $1) "  # noqa: S608
            "RETURNING *",
            before,
        )
        undo.record_created(table, restored)
    else:  # the group edited this row
        columns = [c for c in before if c not in _IMMUTABLE_COLUMNS and c != _key_column(table)]
        assignments = ", ".join(f"{quote_ident(c)} = r.{quote_ident(c)}" for c in columns)
        after = await conn.fetchrow_required(
            f"UPDATE {tbl} AS t SET {assignments} "  # noqa: S608
            f"FROM jsonb_populate_record(NULL::{tbl}, $1) AS r "
            f"WHERE t.{pk}::text = $2 RETURNING t.*",
            before,
            key,
        )
        undo.record_updated(table, key, current, after)


async def undo_group(
    conn: Connection,
    undo_id: str,
    *,
    actor: Actor = "user",
    message_id: str | None = None,
    ignore_groups: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Revert one write group by replaying its snapshot backwards.

    `ignore_groups` names undo groups this same operation just wrote. They are not later writes;
    they are the previous step of one undo, such as an import's categorization groups before its
    commit groups.
    """
    target = await conn.fetchrow("SELECT * FROM undo_log WHERE id = $1", undo_id)
    if target is None:
        raise NotFoundLedgerError(f"No write group {undo_id}")
    check = await _check(conn, target, ignore_groups)
    if check.undone:
        return {
            "status": "already_undone",
            "undo_id": undo_id,
            "summary": "That write was already undone.",
        }
    if check.conflicts:
        names = sorted({f"{row['table_name']}:{row['row_id']}" for row in check.conflicts})
        raise ConflictLedgerError(
            _describe_conflicts(check.conflicts), details={"conflicts": names}
        )

    entries = target["snapshot"]
    tables = {entry["table"] for entry in entries}
    async with write_group(conn, actor=actor, message_id=message_id) as undo:
        undo.summary = f"Undid: {await _import_phrase(conn, undo_id) or target['summary']}"
        if tables & {"schedule_occurrences", "schedule_payments"}:
            from arno.ledger.schedule_payments import lock_payment_undo

            await lock_payment_undo(conn, entries)
        try:
            for entry in reversed(entries):
                await _restore(conn, undo, entry)
            # Foreign keys are deferred: check them now, so a row other records still point at
            # is a clear conflict here rather than a bare error at commit.
            await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        except asyncpg.IntegrityConstraintViolationError as exc:
            raise ConflictLedgerError(
                "Other records still depend on what this undo would change, or the rows it "
                "would restore already exist. Undo or remove those first, then try again."
            ) from exc
        await conn.execute("UPDATE undo_log SET undone_at = now() WHERE id = $1", undo_id)

    return {
        "status": "undone",
        "undo_id": undo_id,
        "undo_undo_id": undo.group_id,
        "tables": sorted(tables),
        "summary": f"Reverted {len(entries)} changes.",
    }


async def undo_latest(
    conn: Connection, *, actor: Actor = "user", message_id: str | None = None
) -> dict[str, Any]:
    group_id = await conn.fetchval(
        """
        SELECT id FROM undo_log WHERE undone_at IS NULL AND actor <> 'system'
        ORDER BY created_at DESC, id DESC LIMIT 1
        """
    )
    if group_id is None:
        raise NotFoundLedgerError("There is no write to undo.")
    return await undo_group(conn, str(group_id), actor=actor, message_id=message_id)
