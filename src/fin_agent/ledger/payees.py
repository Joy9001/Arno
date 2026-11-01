"""Payees and the ledger accounts that hold what people owe.

A person's balance is a real ledger account. A positive balance means they owe you; a negative
one means you owe them. Reimbursements use the same mechanism on an organization's account.
"""

from __future__ import annotations

from typing import Any

import psycopg

from fin_agent.ledger.accounts import _create_account_in_group
from fin_agent.ledger.audit import WriteGroup, write_group
from fin_agent.ledger.common import today
from fin_agent.ledger.errors import InvalidLedgerError, NotFoundLedgerError

PAYEE_KINDS = ("person", "merchant", "organization")


def get_payee(conn: psycopg.Connection, payee_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM payees WHERE id = %s", (payee_id,)).fetchone()
    if row is None:
        raise NotFoundLedgerError(f"No payee {payee_id}")
    return row


def ensure_person_account(
    conn: psycopg.Connection, payee_id: str, group: WriteGroup | None = None
) -> dict[str, Any]:
    """Give a payee a ledger account, creating it once. Works for people and employers."""
    payee = get_payee(conn, payee_id)
    if payee["person_account_id"]:
        row = conn.execute(
            "SELECT * FROM accounts WHERE id = %s", (payee["person_account_id"],)
        ).fetchone()
        if row is not None and row["active"]:
            return row

    if group is None:
        with write_group(conn, actor="user") as own_group:
            return ensure_person_account(conn, payee_id, own_group)

    account = _create_account_in_group(
        conn,
        group,
        kind="person",
        name=payee["display_name"],
        liquid=False,
    )
    after = conn.execute(
        """
        UPDATE payees SET person_account_id = %s, updated_at = now()
        WHERE id = %s RETURNING *
        """,
        (account["id"], payee_id),
    ).fetchone()
    group.record_updated("payees", payee_id, payee, after)
    return account


def create_payee(
    conn: psycopg.Connection,
    *,
    display_name: str,
    kind: str = "merchant",
    upi_id: str | None = None,
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
        row = conn.execute(
            "SELECT 1 FROM categories WHERE id = %s", (default_category_id,)
        ).fetchone()
        if row is None:
            raise NotFoundLedgerError(f"No category {default_category_id}")
    if upi_id is not None:
        row = conn.execute("SELECT 1 FROM payees WHERE upi_id = %s", (upi_id,)).fetchone()
        if row is not None:
            raise InvalidLedgerError(f"UPI id {upi_id} is already on another payee")

    with write_group(conn, actor=actor, message_id=message_id) as group:
        row = conn.execute(
            """
            INSERT INTO payees
                (display_name, kind, upi_id, default_category_id, outside_text)
            VALUES (%s, %s, %s, %s, %s) RETURNING *
            """,
            (name, kind, upi_id, default_category_id, outside_text),
        ).fetchone()
        group.record_created("payees", row)
        if create_person_account or kind == "person":
            ensure_person_account(conn, row["id"], group)
            row = get_payee(conn, row["id"])
    return dict(row) | {"undo_id": group.group_id}


def list_payees(
    conn: psycopg.Connection, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    where = "" if include_inactive else "WHERE active"
    return list(
        conn.execute(
            f"SELECT * FROM payees {where} ORDER BY display_name"  # noqa: S608
        ).fetchall()
    )


def update_payee(
    conn: psycopg.Connection,
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
    before = get_payee(conn, payee_id)
    if kind is not None and kind not in PAYEE_KINDS:
        raise InvalidLedgerError(f"Unknown payee kind {kind}")
    if default_category_id is not None:
        row = conn.execute(
            "SELECT 1 FROM categories WHERE id = %s", (default_category_id,)
        ).fetchone()
        if row is None:
            raise NotFoundLedgerError(f"No category {default_category_id}")
    if upi_id is not None:
        row = conn.execute(
            "SELECT 1 FROM payees WHERE upi_id = %s AND id <> %s", (upi_id, payee_id)
        ).fetchone()
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
    with write_group(conn, actor=actor, message_id=message_id) as group:
        after = conn.execute(
            f"""
            UPDATE payees SET {", ".join(f"{column} = %s" for column in columns)},
                updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (*[updates[column] for column in columns], payee_id),
        ).fetchone()
        group.record_updated("payees", payee_id, before, after)
    return dict(after) | {"undo_id": group.group_id}


def archive_payee(
    conn: psycopg.Connection,
    payee_id: str,
    *,
    actor: str = "user",
    message_id: str | None = None,
) -> dict[str, Any]:
    return update_payee(conn, payee_id, active=False, actor=actor, message_id=message_id)


def people(conn: psycopg.Connection) -> dict[str, Any]:
    rows = list(
        conn.execute(
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
        ).fetchall()
    )
    return {
        "people": [dict(row) for row in rows],
        "as_of": today().isoformat(),
    }
