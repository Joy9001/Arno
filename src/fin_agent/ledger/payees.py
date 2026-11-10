"""Payees and the ledger accounts that hold what people owe.

A person's balance is a real ledger account. A positive balance means they owe you; a negative
one means you owe them. Reimbursements use the same mechanism on an organization's account.
"""

from __future__ import annotations

from typing import Any

from fin_agent.db.conn import Connection, quote_ident
from fin_agent.ledger.accounts import _create_account_in_group
from fin_agent.ledger.audit import WriteGroup, write_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

PAYEE_KINDS = ("person", "merchant", "organization")

# A name that says who was paid says nothing when it is one of these. The agent invents them
# ("Unknown shop") when the user names no shop, and every such row piles unnamed spending into one
# fictional merchant. Whole-name matches only, so "Blue Tokai", "The Store" and "Corner Shop" stay
# real payees.
PLACEHOLDER_PAYEES = frozenset(
    {
        "unknown",
        "unknown shop",
        "unknown merchant",
        "unknown store",
        "unknown payee",
        "shop",
        "store",
        "merchant",
        "vendor",
        "misc",
        "miscellaneous",
        "n/a",
        "na",
        "someone",
        "somebody",
    }
)


def is_placeholder_name(name: str) -> bool:
    """Whether a payee name names nobody, so no payee should be created from it."""
    return " ".join(name.lower().split()).strip(" .,:;-") in PLACEHOLDER_PAYEES


async def get_payee(conn: Connection, payee_id: str) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT * FROM payees WHERE id = $1", payee_id)
    if row is None:
        raise NotFoundLedgerError(f"No payee {payee_id}")
    return row


async def ensure_person_account(
    conn: Connection, payee_id: str, group: WriteGroup | None = None
) -> dict[str, Any]:
    """Give a payee a ledger account, creating it once. Works for people and employers."""
    payee = await get_payee(conn, payee_id)
    if payee["person_account_id"]:
        row = await conn.fetchrow(
            "SELECT * FROM accounts WHERE id = $1", payee["person_account_id"]
        )
        if row is not None and row["active"]:
            return row

    if group is None:
        async with write_group(conn, actor="user") as own_group:
            return await ensure_person_account(conn, payee_id, own_group)

    account = await _create_account_in_group(
        conn,
        group,
        kind="person",
        name=payee["display_name"],
        liquid=False,
    )
    after = await conn.fetchrow(
        """
        UPDATE payees SET person_account_id = $1, updated_at = now()
        WHERE id = $2 RETURNING *
        """,
        account["id"],
        payee_id,
    )
    group.record_updated("payees", payee_id, payee, after)
    return account


async def create_payee(
    conn: Connection,
    *,
    display_name: str,
    kind: str = "merchant",
    upi_id: str | None = None,
    match_hash: str | None = None,
    default_category_id: str | None = None,
    create_person_account: bool = False,
    outside_text: bool = False,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    name = display_name.strip()
    if not name:
        raise InvalidLedgerError("A payee needs a name")
    if kind not in PAYEE_KINDS:
        raise InvalidLedgerError(f"Unknown payee kind {kind}")
    if default_category_id is not None:
        row = await conn.fetchrow("SELECT 1 FROM categories WHERE id = $1", default_category_id)
        if row is None:
            raise NotFoundLedgerError(f"No category {default_category_id}")
    if upi_id is not None:
        row = await conn.fetchrow("SELECT 1 FROM payees WHERE lower(upi_id) = lower($1)", upi_id)
        if row is not None:
            raise InvalidLedgerError(f"UPI id {upi_id} is already on another payee")

    async with write_group(conn, actor=actor, message_id=message_id) as group:
        row = await conn.fetchrow(
            """
            INSERT INTO payees
                (display_name, kind, upi_id, match_hash, default_category_id, outside_text)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
            """,
            name,
            kind,
            upi_id,
            match_hash,
            default_category_id,
            outside_text,
        )
        group.record_created("payees", row)
        if create_person_account or kind == "person":
            await ensure_person_account(conn, row["id"], group)
            row = await get_payee(conn, row["id"])
    return dict(row) | {"undo_id": group.group_id}


async def list_payees(conn: Connection, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active"
    return await conn.fetch(f"SELECT * FROM payees {where} ORDER BY display_name")


async def update_payee(
    conn: Connection,
    payee_id: str,
    *,
    display_name: str | None = None,
    kind: str | None = None,
    upi_id: str | None = None,
    default_category_id: str | None = None,
    active: bool | None = None,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    before = await get_payee(conn, payee_id)
    if kind is not None and kind not in PAYEE_KINDS:
        raise InvalidLedgerError(f"Unknown payee kind {kind}")
    if default_category_id is not None:
        row = await conn.fetchrow("SELECT 1 FROM categories WHERE id = $1", default_category_id)
        if row is None:
            raise NotFoundLedgerError(f"No category {default_category_id}")
    if upi_id is not None:
        row = await conn.fetchrow(
            "SELECT 1 FROM payees WHERE lower(upi_id) = lower($1) AND id <> $2", upi_id, payee_id
        )
        if row is not None:
            raise InvalidLedgerError(f"UPI id {upi_id} is already on another payee")
    updates: dict[str, Any] = {}
    for key, value in {
        "display_name": display_name,
        "kind": kind,
        "upi_id": upi_id,
        "default_category_id": default_category_id,
        "active": active,
    }.items():
        if value is not None:
            updates[key] = value
    if not updates:
        return dict(before) | {"undo_id": None}
    columns = list(updates)
    assignments = [f"{quote_ident(column)} = ${index}" for index, column in enumerate(columns, 1)]
    async with write_group(conn, actor=actor, message_id=message_id) as group:
        after = await conn.fetchrow(
            f"""
            UPDATE payees SET {", ".join(assignments)},
                updated_at = now()
            WHERE id = ${len(columns) + 1} RETURNING *
            """,
            *[updates[column] for column in columns],
            payee_id,
        )
        group.record_updated("payees", payee_id, before, after)
    return dict(after) | {"undo_id": group.group_id}


async def archive_payee(
    conn: Connection,
    payee_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return await update_payee(conn, payee_id, active=False, actor=actor, message_id=message_id)


async def people(conn: Connection) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT
            p.id AS payee_id,
            p.display_name,
            p.kind,
            p.upi_id,
            p.default_category_id,
            p.person_account_id AS account_id,
            coalesce(v.balance_paise, 0)::bigint AS balance_paise,
            coalesce(v.direction, 'settled') AS direction,
            v.next_expected_date,
            a.active AS account_active
        FROM payees p
        LEFT JOIN accounts a ON a.id = p.person_account_id
        LEFT JOIN v_owed v ON v.payee_id = p.id
        WHERE p.active AND p.kind IN ('person', 'organization')
        ORDER BY p.display_name
        """
    )
    return {
        "people": [dict(row) for row in rows],
        "as_of": today().isoformat(),
    }
